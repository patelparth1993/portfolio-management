"""
Ingestion pipeline: pull PDFs from Google Drive, parse new ones, update history.json.

Gemini is used as a fallback when pdfplumber can't extract key fields.
All PDFs needing Gemini are dispatched concurrently (asyncio.gather) so the entire
fallback batch completes in roughly the time of a single request.
"""
import asyncio
import json
import hashlib
import os
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import yaml

from parsers import PARSERS, GEMINI_APPLIERS

ROOT = Path(__file__).parent.parent
STATEMENTS_DIR = ROOT / "statements"
PROCESSED_DIR = ROOT / "data" / "processed"
HISTORY_FILE = ROOT / "data" / "history.json"
CONFIG_FILE = ROOT / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def load_history() -> dict:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {"processed_hashes": [], "snapshots": []}


def save_history(history: dict):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, default=str)


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _account_type_hint_from_dir(dir_name: str) -> str | None:
    """Extract account type from directory name suffix.
    e.g. 'HF4253745CAD_RRSP' → 'RRSP', 'WZ0969XK4CAD_TFSA_PORTFOLIO' → 'TFSA'
    Returns None if the directory name doesn't follow the {ACCOUNT_NUM}_{TYPE} pattern.
    """
    name = dir_name.upper()
    for acct_type in ("RRSP", "TFSA", "RPP", "RESP", "FHSA", "RRIF"):
        if f"_{acct_type}" in name:
            return acct_type
    return None


def _find_local_unprocessed(history: dict) -> list[tuple[str, Path, str | None, str]]:
    """Return any PDFs already in statements/ that haven't been processed.
    Each entry is (institution, pdf_path, account_type_hint, account_subdir).
    account_subdir is the immediate parent dir name (e.g. 'HF4253745CAD_RRSP').
    For flat institution dirs (manulife), account_subdir == institution.
    """
    processed = set(history.get("processed_hashes", []))
    new_files = []
    for institution_dir in sorted(STATEMENTS_DIR.iterdir()):
        if not institution_dir.is_dir():
            continue
        institution = institution_dir.name
        if institution not in PARSERS:
            continue  # skip folders with no registered parser (e.g. cra/)
        # glob "**/*.pdf" catches both flat and per-account subdirectory layouts
        for pdf in sorted(institution_dir.glob("**/*.pdf")):
            # Skip CRM2 fee/performance reports — not account statements
            if pdf.name.upper().startswith("CRM2"):
                continue
            h = _file_hash(pdf)
            if h not in processed:
                hint = _account_type_hint_from_dir(pdf.parent.name)
                account_subdir = pdf.parent.name  # e.g. 'HF4253745CAD_RRSP' or 'manulife'
                new_files.append((institution, pdf, hint, account_subdir))
    return new_files


def _process_pdf(institution: str, pdf_path: Path, account_type_hint: str | None = None,
                 account_subdir: str = "") -> list[dict]:
    """Returns a list of parsed account dicts (some parsers return multiple accounts per PDF).
    Sets account_id on each result to uniquely identify the account across institutions.
    """
    parser = PARSERS.get(institution)
    if not parser:
        print(f"  No parser for institution: {institution}")
        return []
    hint_str = f", hint={account_type_hint}" if account_type_hint else ""
    print(f"  Parsing: {pdf_path.name} ({institution}{hint_str})")
    result = parser(pdf_path, account_type_hint=account_type_hint)
    # Normalise: some parsers return a single dict, others a list
    results = [result] if isinstance(result, dict) and result else (result or [])
    # Stamp account_id on each result for unique identification
    for r in results:
        if account_subdir and account_subdir != institution:
            # WealthSimple-style: subdir name is the account identifier (e.g. 'HF4253745CAD_RRSP')
            r["account_id"] = account_subdir
        else:
            # Flat dir (manulife): use institution + account_type as identifier
            r["account_id"] = f"{institution}_{r.get('account_type', 'Unknown')}"
    return results


def _save_processed(result: dict) -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(result["file"]).stem
    account_id = result.get("account_id", "")
    # Filename: {institution}_{account_id}_{stem}.json — always unique per account + period
    if account_id:
        out_path = PROCESSED_DIR / f"{result['institution']}_{account_id}_{stem}.json"
    else:
        out_path = PROCESSED_DIR / f"{result['institution']}_{stem}.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    return out_path


async def _gemini_call_async(client, pdf_path: Path, prompt: str) -> str:
    """Send one PDF + prompt to Gemini asynchronously. Returns raw response text."""
    from google.genai import types
    pdf_bytes = pdf_path.read_bytes()
    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            prompt,
        ],
    )
    return response.text


async def _run_gemini_batch_async(needs_gemini: list[tuple[str, Path, dict]]) -> list[tuple[str, list[dict]]]:
    """
    Dispatch all Gemini fallback calls concurrently.

    needs_gemini: list of (institution, pdf_path, placeholder_result)
    Returns:      list of (institution, resolved_results_list)
    """
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  GEMINI_API_KEY not set — skipping Gemini fallback for all.")
        return [(inst, [r]) for inst, _, r in needs_gemini]

    client = genai.Client(api_key=api_key)
    print(f"  Dispatching {len(needs_gemini)} PDF(s) to Gemini concurrently...")

    tasks = [
        _gemini_call_async(client, pdf_path, result["_gemini_prompt"])
        for _, pdf_path, result in needs_gemini
    ]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    resolved = []
    for (institution, pdf_path, placeholder), response in zip(needs_gemini, responses):
        applier = GEMINI_APPLIERS.get(institution)
        if isinstance(response, Exception):
            print(f"  Gemini failed for {pdf_path.name}: {response}")
            placeholder.pop("_needs_gemini", None)
            placeholder.pop("_gemini_prompt", None)
            resolved.append((institution, [placeholder]))
        elif applier:
            results = applier(placeholder, response)
            # Normalise: WealthSimple applier returns a dict, Manulife returns a list
            if isinstance(results, dict):
                results = [results]
            resolved.append((institution, results))
        else:
            resolved.append((institution, [placeholder]))

    return resolved


def _build_snapshots(all_results: list[dict]) -> list[dict]:
    """Aggregate all parsed results into per-date portfolio snapshots."""
    by_date: dict[str, list] = defaultdict(list)
    for r in all_results:
        date = r.get("statement_date") or r.get("parsed_at", "")[:10]
        by_date[date].append(r)

    snapshots = []
    for date, records in sorted(by_date.items()):
        accounts = []
        total = 0.0
        for r in records:
            val = r.get("total_value") or 0.0
            total += val
            accounts.append({
                "institution": r["institution"],
                "account_type": r["account_type"],
                "account_id": r.get("account_id", f"{r['institution']}_{r.get('account_type', '')}"),
                "total_value": val,
                "currency": r.get("currency", "CAD"),
                "holdings_count": len(r.get("holdings", [])),
            })
        snapshots.append({
            "date": date,
            "total_net_worth": total,
            "accounts": accounts,
        })
    return snapshots


def run():
    print("=== Ingestion Pipeline ===")
    history = load_history()
    processed_hashes = set(history.get("processed_hashes", []))

    # Pull from Google Drive (skip if not configured)
    if os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON") or os.environ.get("GDRIVE_FOLDER_ID"):
        print("Pulling from Google Drive...")
        try:
            from gdrive import download_new_statements
            download_new_statements(processed_hashes)
        except Exception as e:
            print(f"  Google Drive error: {e}")
            raise
    else:
        print("Google Drive not configured — using local statements/ only.")

    # Find any unprocessed PDFs now in statements/
    new_files = _find_local_unprocessed(history)
    if not new_files:
        print("No new statements to process.")
    else:
        print(f"Processing {len(new_files)} new statement(s)...")

        # Phase 1: pdfplumber pass for all PDFs
        parsed: list[tuple[str, Path, list[dict]]] = []
        for institution, pdf_path, hint, account_subdir in new_files:
            results = _process_pdf(institution, pdf_path, account_type_hint=hint,
                                   account_subdir=account_subdir)
            parsed.append((institution, pdf_path, results))

        # Phase 2: collect PDFs that need Gemini, run them all concurrently
        needs_gemini: list[tuple[str, Path, dict]] = []
        for institution, pdf_path, results in parsed:
            for result in results:
                if result.get("_needs_gemini"):
                    needs_gemini.append((institution, pdf_path, result))

        if needs_gemini:
            print(f"  {len(needs_gemini)} PDF(s) need Gemini fallback — batching...")
            gemini_resolved = asyncio.run(_run_gemini_batch_async(needs_gemini))
            # Build map: pdf_path → resolved results list
            gemini_map: dict[str, list[dict]] = {}
            for (inst, pdf_path, _placeholder), (_, resolved_results) in zip(needs_gemini, gemini_resolved):
                # Stamp account_id on any Gemini-returned dicts that don't have it
                for r in resolved_results:
                    if not r.get("account_id"):
                        r["account_id"] = f"{r.get('institution', inst)}_{r.get('account_type', 'Unknown')}"
                gemini_map[str(pdf_path)] = resolved_results

            # Substitute resolved results back into parsed
            final_parsed: list[tuple[str, Path, list[dict]]] = []
            for institution, pdf_path, results in parsed:
                if str(pdf_path) in gemini_map:
                    final_parsed.append((institution, pdf_path, gemini_map[str(pdf_path)]))
                else:
                    final_parsed.append((institution, pdf_path, results))
            parsed = final_parsed

        # Phase 3: save all results
        for institution, pdf_path, results in parsed:
            file_hash = None
            for result in results:
                file_hash = result.get("file_hash") or file_hash
                # Skip results with no value and no holdings
                if not result.get("total_value") and not result.get("holdings"):
                    print(f"  Skipping (no data): {result.get('file', pdf_path.name)}")
                    continue
                out = _save_processed(result)
                print(f"  Saved: {out.name}")
            if file_hash:
                history["processed_hashes"].append(file_hash)

    # Rebuild full snapshot history from all processed files
    all_results = []
    for f in sorted(PROCESSED_DIR.glob("*.json")):
        with open(f) as fp:
            all_results.append(json.load(fp))

    history["snapshots"] = _build_snapshots(all_results)
    history["last_updated"] = datetime.now().isoformat()
    save_history(history)
    print("Ingestion complete.")


if __name__ == "__main__":
    run()

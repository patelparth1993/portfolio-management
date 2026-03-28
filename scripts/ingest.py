"""
Ingestion pipeline: pull PDFs from Google Drive, parse new ones, update history.json.
"""
import json
import hashlib
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import yaml

from parsers import PARSERS

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


def _find_local_unprocessed(history: dict) -> list[tuple[str, Path]]:
    """Return any PDFs already in statements/ that haven't been processed."""
    processed = set(history.get("processed_hashes", []))
    new_files = []
    for institution_dir in STATEMENTS_DIR.iterdir():
        if not institution_dir.is_dir():
            continue
        institution = institution_dir.name
        for pdf in sorted(institution_dir.glob("*.pdf")):
            h = _file_hash(pdf)
            if h not in processed:
                new_files.append((institution, pdf))
    return new_files


def _process_pdf(institution: str, pdf_path: Path) -> dict:
    parser = PARSERS.get(institution)
    if not parser:
        print(f"  No parser for institution: {institution}")
        return {}
    print(f"  Parsing: {pdf_path.name} ({institution})")
    return parser(pdf_path)


def _save_processed(result: dict) -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(result["file"]).stem
    out_path = PROCESSED_DIR / f"{result['institution']}_{stem}.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    return out_path


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

    # Pull from Google Drive
    print("Pulling from Google Drive...")
    try:
        from gdrive import download_new_statements
        download_new_statements(processed_hashes)
    except RuntimeError as e:
        print(f"  Google Drive error: {e}")
        raise

    # Find any unprocessed PDFs now in statements/
    new_files = _find_local_unprocessed(history)
    if not new_files:
        print("No new statements to process.")
    else:
        print(f"Processing {len(new_files)} new statement(s)...")
        for institution, pdf_path in new_files:
            result = _process_pdf(institution, pdf_path)
            if result:
                out = _save_processed(result)
                print(f"  Saved: {out.name}")
                history["processed_hashes"].append(result["file_hash"])

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

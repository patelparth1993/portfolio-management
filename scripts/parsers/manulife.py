"""
Manulife statement parser.
Handles Manulife group savings PDF statements (RPP + RRSP combined in one PDF).
Returns a LIST of account dicts, one per plan found.
Falls back to Gemini API if core data cannot be extracted.
"""
import re
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber


def _extract_statement_end_date(text: str) -> Optional[str]:
    """
    Manulife format (compressed): "July1,2025toSeptember30,2025"
    or with spaces:               "July 1, 2025 to September 30, 2025"
    We want the END date.
    """
    # Compressed: toSeptember30,2025 or toDecember31,2025
    m = re.search(r"to([A-Z][a-z]+\d+,\d{4})", text)
    if m:
        raw = m.group(1)  # e.g. "September30,2025"
        # insert space before digits to make it parseable: "September 30,2025"
        raw = re.sub(r"([a-z])(\d)", r"\1 \2", raw)  # "September 30,2025"
        raw = raw.replace(",", " ")  # "September 30 2025"
        try:
            return datetime.strptime(raw, "%B %d %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Spaced variant: "to September 30, 2025"
    m = re.search(r"to\s+([A-Z][a-z]+\s+\d+,?\s+\d{4})", text)
    if m:
        raw = m.group(1).replace(",", "")
        for fmt in ("%B %d %Y", "%B %d  %Y"):
            try:
                return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Fallback: ISO date
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)

    return None


def _extract_plan_values(text: str) -> dict[str, float]:
    """
    Extract ending value per plan from the Overview table.
    Line format (may be compressed):
      "RPP (policynumber10006671) $0.00 $11,752.29 $0.00 $1,797.73 $13,550.02"
      "RRSP (policynumber20006671) $0.00 $1,942.41 $0.00 $333.16 $2,275.57"
    The LAST dollar amount on each line is the closing value.
    """
    plan_values: dict[str, float] = {}
    # Find lines containing RPP or RRSP followed by dollar amounts
    for line in text.splitlines():
        for plan_type in ("RPP", "RRSP"):
            if plan_type in line:
                # find all dollar values on this line
                amounts = re.findall(r"\$([\d,]+\.\d{2})", line)
                if amounts:
                    # last amount = closing value
                    plan_values[plan_type] = float(amounts[-1].replace(",", ""))
    return plan_values


def _extract_period_deposits_per_plan(text: str) -> dict[str, float]:
    """
    Extract 'money that went in' per plan this statement period.
    Manulife format: 'Plus money that went in $X,XXX.XX' under each plan section.
    """
    deposits: dict[str, float] = {"RPP": 0.0, "RRSP": 0.0}
    current_plan: Optional[str] = None
    for line in text.splitlines():
        line_lower = line.lower()
        if re.search(r"registered\s+pension", line_lower) and "retirement savings" not in line_lower:
            current_plan = "RPP"
        elif re.search(r"registered\s+retirement\s+savings|retirement\s+savings\s+plan", line_lower):
            current_plan = "RRSP"
        if current_plan and re.search(r"money that went in", line_lower):
            m = re.search(r"\$([\d,]+\.?\d*)", line)
            if m:
                deposits[current_plan] = float(m.group(1).replace(",", ""))
    return deposits


def _extract_fund_holdings_per_plan(text: str) -> dict[str, list[dict]]:
    """
    Extract fund holdings grouped by plan.
    Manulife fund line format (compressed):
      "2760MLRetDateIndxPlus2060o6 670.20285 $20.2180 $13,550.02 100.0% 18.4%"
    These appear under "Your Registered Pension Plan" or "Your Registered Retirement Savings Plan"
    sections.
    """
    holdings_by_plan: dict[str, list[dict]] = {"RPP": [], "RRSP": []}
    current_plan: Optional[str] = None

    fund_pattern = re.compile(
        r"(\w{4,})\s+"           # fund code (e.g. 2760MLRetDateIndxPlus2060o6)
        r"([\d.]+)\s+"           # number of units
        r"\$([\d.]+)\s+"         # unit price
        r"\$([\d,]+\.\d{2})"     # market value
    )

    for line in text.splitlines():
        # Detect plan section headers — "Pension Plan" can be split across lines
        line_lower = line.lower()
        if re.search(r"registered\s+pension", line_lower) and "retirement savings" not in line_lower:
            current_plan = "RPP"
        elif re.search(r"registered\s+retirement\s+savings|retirement\s+savings\s+plan", line_lower):
            current_plan = "RRSP"

        if current_plan is None:
            continue

        m = fund_pattern.search(line)
        if m:
            fund_code = m.group(1)
            # Skip lines that are clearly header/footer text
            if fund_code in ("Total", "Page", "NNNNNN", "Numberofunits"):
                continue
            qty = float(m.group(2))
            price = float(m.group(3))
            mkt_val = float(m.group(4).replace(",", ""))
            holdings_by_plan[current_plan].append({
                "symbol": fund_code,
                "name": fund_code,  # Manulife doesn't print a separate fund name inline
                "quantity": qty,
                "price": price,
                "market_value": mkt_val,
            })

    return holdings_by_plan


GEMINI_PROMPT = """This is a Manulife group savings plan statement containing both RPP and RRSP accounts.
Extract the data and return a JSON array with one object per plan:
[
  {
    "account_type": "RPP",
    "statement_date": "YYYY-MM-DD",
    "total_value": 0.0,
    "period_deposits": 0.0,
    "holdings": [{"symbol": "", "name": "", "quantity": 0.0, "price": 0.0, "market_value": 0.0}],
    "currency": "CAD"
  },
  {
    "account_type": "RRSP",
    "statement_date": "YYYY-MM-DD",
    "total_value": 0.0,
    "period_deposits": 0.0,
    "holdings": [{"symbol": "", "name": "", "quantity": 0.0, "price": 0.0, "market_value": 0.0}],
    "currency": "CAD"
  }
]
For period_deposits: use the "Plus money that went in" amount for each plan (contributions + employer match + transfers in this period only).
Return only the JSON array, no other text."""


def parse(pdf_path: str | Path, account_type_hint: str | None = None) -> list[dict]:
    """
    Parse a Manulife PDF statement using pdfplumber.
    Returns a LIST of standardized dicts (one per plan: RPP, RRSP, etc.).
    If key data is missing, returns a single placeholder dict with _needs_gemini=True
    so that ingest.py can batch the Gemini fallback.
    account_type_hint is accepted for API compatibility but ignored.
    """
    pdf_path = Path(pdf_path)
    file_hash = hashlib.md5(pdf_path.read_bytes()).hexdigest()

    results = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

        statement_date = _extract_statement_end_date(full_text)
        plan_values = _extract_plan_values(full_text)
        holdings_by_plan = _extract_fund_holdings_per_plan(full_text)
        deposits_by_plan = _extract_period_deposits_per_plan(full_text)

        for plan_type, total_value in plan_values.items():
            results.append({
                "institution": "manulife",
                "file": pdf_path.name,
                "file_hash": file_hash,
                "parsed_at": datetime.now().isoformat(),
                "statement_date": statement_date,
                "account_type": plan_type,
                "total_value": total_value,
                "period_deposits": deposits_by_plan.get(plan_type, 0.0),
                "cash_balance": None,
                "currency": "CAD",
                "holdings": holdings_by_plan.get(plan_type, []),
                "raw_text_snippet": full_text[:300],
            })

    except Exception as e:
        print(f"  pdfplumber failed for {pdf_path.name}: {e}")

    # Mark for Gemini batch if pdfplumber couldn't extract key data
    if not results or not results[0].get("statement_date") or not results[0].get("total_value"):
        return [{
            "institution": "manulife",
            "file": pdf_path.name,
            "file_hash": file_hash,
            "parsed_at": datetime.now().isoformat(),
            "_needs_gemini": True,
            "_gemini_prompt": GEMINI_PROMPT,
        }]

    return results


def apply_gemini_response(placeholder: dict, gemini_text: str) -> list[dict]:
    """Expand a Gemini JSON response (array) into a proper list of Manulife result dicts."""
    file_name = placeholder.get("file", "")
    file_hash = placeholder.get("file_hash", "")
    parsed_at = placeholder.get("parsed_at", datetime.now().isoformat())

    try:
        text = gemini_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        items = json.loads(text)
        if isinstance(items, dict):
            items = [items]
        results = []
        for item in items:
            item.setdefault("institution", "manulife")
            item.setdefault("file", file_name)
            item.setdefault("file_hash", file_hash)
            item.setdefault("parsed_at", parsed_at)
            item.setdefault("currency", "CAD")
            results.append(item)
        return results
    except Exception as e:
        print(f"  Failed to parse Gemini response for {file_name}: {e}")
        return []

"""
WealthSimple statement parser.
Handles WealthSimple PDF account statements and extracts holdings, values, and transactions.
Falls back to Gemini API for complex/image-based pages.
"""
import re
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber


ACCOUNT_TYPE_PATTERNS = {
    "TFSA": r"tax.free savings account|tfsa",
    "RRSP": r"registered retirement savings|rrsp|\brsp\b",   # WealthSimple sometimes writes "RSP Account"
    "RESP": r"registered education savings|resp",
    "FHSA": r"first home savings account|fhsa",
    "RRIF": r"registered retirement income|rrif",
    "Personal": r"personal|non-registered|taxable",
    "Crypto": r"crypto",
}


def _detect_account_type(text: str) -> str:
    text_lower = text.lower()
    for acct_type, pattern in ACCOUNT_TYPE_PATTERNS.items():
        if re.search(pattern, text_lower):
            return acct_type
    return "Unknown"


def _extract_statement_date(text: str) -> Optional[str]:
    """Extract statement period END date from WealthSimple PDF.
    Format: 'YYYY-MM-DD - YYYY-MM-DD' — we want the end date (second one).
    """
    # WealthSimple uses ISO format: "2026-02-01 - 2026-02-28"
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(2)  # end date
    # Fallback: any ISO date
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    return None


def _extract_total_value(text: str) -> Optional[float]:
    """Extract total portfolio value.
    WealthSimple format: 'Total Portfolio $4,571.55 100.00 $4,249.49 100.00'
    """
    m = re.search(r"Total Portfolio\s+\$([\d,]+\.?\d*)", text)
    if m:
        return float(m.group(1).replace(",", ""))
    # Fallback patterns
    for pattern in [
        r"total\s+(?:account\s+)?value[:\s]+\$?([\d,]+\.?\d*)",
        r"portfolio\s+value[:\s]+\$?([\d,]+\.?\d*)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def _extract_cash_balance(text: str) -> Optional[float]:
    """Extract closing cash balance.
    WealthSimple format: 'Closing Cash Balance $801.32'
    """
    m = re.search(r"Closing Cash Balance\s+\$([\d,]+\.?\d*)", text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _extract_period_deposits(text: str) -> float:
    """Extract new external money deposited/contributed this statement period.
    TFSA/non-reg: 'Cash Paid In Deposits $X'
    RRSP: 'First 60 Days $X' + 'Rest of Year $X' (RRSP contribution buckets)
    Does NOT include proceeds from sales, dividends, or interest.
    """
    total = 0.0
    m = re.search(r"Cash Paid In\s+Deposits\s+\$([\d,]+\.?\d*)", text)
    if m:
        total += float(m.group(1).replace(",", ""))
    for pat in [r"First 60 Days\s+\$([\d,]+\.?\d*)", r"Rest of Year\s+\$([\d,]+\.?\d*)"]:
        m = re.search(pat, text)
        if m:
            total += float(m.group(1).replace(",", ""))
    return round(total, 2)


def _extract_holdings_from_text(text: str) -> list[dict]:
    """
    Parse holdings from the Portfolio Assets section.

    WealthSimple line format:
      Name  SYMBOL  QTY  QTY  $PRICE  CAD  $MKT_VALUE  $BOOK_VALUE
    Example:
      BMO Canadian High Dividend Covered ZWC 33.2431 33.2431 $22.06 CAD $733.34 $565.27
      NorthWest Healthcare Properties REIT NWH.UN 180.4642 180.4642 $5.84 CAD $1,053.91 $1,005.39
    """
    holdings = []
    # Match: (anything) SYMBOL QTY QTY $PRICE CAD|USD $MKT_VALUE $BOOK_VALUE
    pattern = re.compile(
        r"^(.*?)\s+"                           # name (greedy up to symbol)
        r"([A-Z]{1,6}(?:\.[A-Z]{1,3})?)\s+"   # SYMBOL (e.g. ZWC, NWH.UN)
        r"([\d,]+\.\d+)\s+"                    # total quantity
        r"[\d,]+\.\d+\s+"                      # segregated quantity (same value, skip)
        r"\$([\d,]+\.?\d*)\s+"                 # price
        r"(?:CAD|USD)\s+"                      # currency
        r"\$([\d,]+\.?\d*)\s+"                 # market value
        r"\$([\d,]+\.?\d*)",                   # book cost
        re.MULTILINE,
    )
    for m in pattern.finditer(text):
        name = m.group(1).strip()
        symbol = m.group(2)
        qty = float(m.group(3).replace(",", ""))
        price = float(m.group(4).replace(",", ""))
        mkt_val = float(m.group(5).replace(",", ""))
        book_val = float(m.group(6).replace(",", ""))
        # Skip header rows or zero-value placeholders that matched by accident
        if mkt_val == 0.0 and qty == 0.0:
            continue
        holdings.append({
            "symbol": symbol,
            "name": name,
            "quantity": qty,
            "price": price,
            "market_value": mkt_val,
            "book_value": book_val,
        })
    return holdings


GEMINI_PROMPT_TEMPLATE = """This is a {account_type} investment account statement from WealthSimple.
Extract the following information and return it as valid JSON:
{{
  "statement_date": "YYYY-MM-DD",
  "account_type": "{account_type}",
  "total_value": 0.0,
  "holdings": [
    {{"symbol": "", "name": "", "quantity": 0.0, "price": 0.0, "market_value": 0.0, "book_value": 0.0}}
  ],
  "cash_balance": 0.0,
  "period_deposits": 0.0,
  "currency": "CAD"
}}
For period_deposits: sum ONLY new external money deposited this period.
- For TFSA/non-registered: use the "Cash Paid In Deposits" amount.
- For RRSP: sum "First 60 Days" + "Rest of Year" contribution amounts.
- Do NOT include proceeds from investment sales, dividends, or interest.
Return only the JSON, no other text."""


def parse(pdf_path: str | Path, account_type_hint: Optional[str] = None) -> dict:
    """Parse a WealthSimple PDF statement using pdfplumber.
    Marks _needs_gemini=True if key data is missing — caller handles Gemini batch.

    account_type_hint: if provided (from subdir name like 'HF4253745CAD_RRSP'),
    overrides auto-detection — the directory name is authoritative.
    """
    pdf_path = Path(pdf_path)
    file_hash = hashlib.md5(pdf_path.read_bytes()).hexdigest()

    result = {
        "institution": "wealthsimple",
        "file": pdf_path.name,
        "file_hash": file_hash,
        "parsed_at": datetime.now().isoformat(),
        "statement_date": None,
        "account_type": "Unknown",
        "total_value": None,
        "cash_balance": None,
        "period_deposits": 0.0,
        "currency": "CAD",
        "holdings": [],
        "raw_text_snippet": "",
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            result["raw_text_snippet"] = full_text[:500]

            detected = _detect_account_type(full_text)
            result["account_type"] = account_type_hint if account_type_hint else detected
            result["statement_date"] = _extract_statement_date(full_text)
            result["total_value"] = _extract_total_value(full_text)
            result["cash_balance"] = _extract_cash_balance(full_text)
            result["period_deposits"] = _extract_period_deposits(full_text)
            result["holdings"] = _extract_holdings_from_text(full_text)

    except Exception as e:
        print(f"  pdfplumber failed for {pdf_path.name}: {e}")

    if not result["total_value"] or not result["statement_date"]:
        result["_needs_gemini"] = True
        result["_gemini_prompt"] = GEMINI_PROMPT_TEMPLATE.format(account_type=result["account_type"])

    return result


def apply_gemini_response(result: dict, gemini_text: str) -> dict:
    """Merge a Gemini JSON response into an existing pdfplumber result dict."""
    try:
        text = gemini_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        gemini_data = json.loads(text)
        result.update({k: v for k, v in gemini_data.items() if v})
    except Exception as e:
        print(f"  Failed to parse Gemini response for {result.get('file')}: {e}")
    result.pop("_needs_gemini", None)
    result.pop("_gemini_prompt", None)
    return result

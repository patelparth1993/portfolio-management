"""
Manulife statement parser.
Handles Manulife PDF account statements and extracts holdings, values, and transactions.
Falls back to Claude API for complex/image-based pages.
"""
import re
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber


ACCOUNT_TYPE_PATTERNS = {
    "RRSP": r"registered retirement savings|rrsp",
    "TFSA": r"tax.free savings account|tfsa",
    "RESP": r"registered education savings|resp",
    "RRIF": r"registered retirement income|rrif",
    "Group RRSP": r"group\s+rrsp|employer.*rrsp",
    "Group Benefits": r"group\s+benefit|pension",
    "Personal": r"non-registered|personal|taxable",
}


def _detect_account_type(text: str) -> str:
    text_lower = text.lower()
    for acct_type, pattern in ACCOUNT_TYPE_PATTERNS.items():
        if re.search(pattern, text_lower):
            return acct_type
    return "Unknown"


def _extract_statement_date(text: str) -> Optional[str]:
    patterns = [
        r"statement\s+(?:date|period)[:\s]+.*?(\w+ \d+,? \d{4})",
        r"as\s+of\s+(\w+ \d+,? \d{4})",
        r"(\w+ \d+,? \d{4})\s*account\s+statement",
        r"for\s+the\s+period.*?to\s+(\w+ \d+,? \d{4})",
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{2}/\d{2}/\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = match.group(1).strip()
            for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
                        "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
    return None


def _extract_total_value(text: str) -> Optional[float]:
    patterns = [
        r"total\s+(?:market\s+)?value[:\s]+\$?([\d,]+\.?\d*)",
        r"total\s+fund\s+value[:\s]+\$?([\d,]+\.?\d*)",
        r"portfolio\s+(?:total\s+)?value[:\s]+\$?([\d,]+\.?\d*)",
        r"account\s+(?:total\s+)?value[:\s]+\$?([\d,]+\.?\d*)",
        r"total\s+assets[:\s]+\$?([\d,]+\.?\d*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def _extract_holdings(pages: list) -> list[dict]:
    """Extract fund/holding data from Manulife statement tables."""
    holdings = []
    for page in pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2:
                continue
            headers = [str(h).lower().strip() if h else "" for h in table[0]]
            has_fund = any("fund" in h or "investment" in h or "security" in h for h in headers)
            has_value = any("value" in h or "balance" in h or "amount" in h for h in headers)
            if not (has_fund or has_value):
                continue
            for row in table[1:]:
                if not row or all(cell is None or str(cell).strip() == "" for cell in row):
                    continue
                holding = {}
                for i, header in enumerate(headers):
                    if i < len(row) and row[i]:
                        val = str(row[i]).strip()
                        if "fund" in header or "investment" in header or "name" in header:
                            holding["name"] = val
                        elif "code" in header or "symbol" in header or "id" in header:
                            holding["symbol"] = val
                        elif "unit" in header and "price" not in header:
                            try:
                                holding["quantity"] = float(val.replace(",", ""))
                            except ValueError:
                                pass
                        elif "unit price" in header or "nav" in header or "price" in header:
                            try:
                                holding["price"] = float(val.replace(",", "").replace("$", ""))
                            except ValueError:
                                pass
                        elif "market value" in header or ("value" in header and "book" not in header):
                            try:
                                holding["market_value"] = float(val.replace(",", "").replace("$", ""))
                            except ValueError:
                                pass
                        elif "book value" in header or "cost" in header or "acb" in header:
                            try:
                                holding["book_value"] = float(val.replace(",", "").replace("$", ""))
                            except ValueError:
                                pass
                if holding.get("name") or holding.get("symbol"):
                    holdings.append(holding)
    return holdings


def _extract_holdings_from_text(text: str) -> list[dict]:
    """Fallback regex-based holding extraction for Manulife fund listings."""
    holdings = []
    # Manulife often lists: Fund Name  units  unit price  market value
    pattern = r"([\w\s&]+(?:Fund|Portfolio|ETF|Bond|Equity))\s+([\d,]+\.?\d*)\s+\$?([\d,]+\.?\d*)\s+\$?([\d,]+\.?\d*)"
    for match in re.finditer(pattern, text, re.IGNORECASE):
        try:
            holdings.append({
                "name": match.group(1).strip(),
                "quantity": float(match.group(2).replace(",", "")),
                "price": float(match.group(3).replace(",", "")),
                "market_value": float(match.group(4).replace(",", "")),
            })
        except ValueError:
            continue
    return holdings


def _parse_with_gemini(pdf_path: Path, account_type: str) -> dict:
    """Fallback parser using Gemini API (free tier). Skips if GEMINI_API_KEY not set."""
    import os
    if not os.environ.get("GEMINI_API_KEY"):
        print("  No GEMINI_API_KEY set — skipping Gemini fallback.")
        return {}
    try:
        import google.generativeai as genai

        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-1.5-flash")

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        prompt = f"""This is a {account_type} investment account statement from Manulife.
Extract the following information and return it as valid JSON:
{{
  "statement_date": "YYYY-MM-DD",
  "account_type": "{account_type}",
  "total_value": 0.0,
  "holdings": [
    {{"symbol": "", "name": "", "quantity": 0.0, "price": 0.0, "market_value": 0.0, "book_value": 0.0}}
  ],
  "cash_balance": 0.0,
  "currency": "CAD"
}}
Return only the JSON, no other text."""

        response = model.generate_content([
            {"mime_type": "application/pdf", "data": pdf_bytes},
            prompt,
        ])
        text = response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
    except Exception as e:
        print(f"  Gemini fallback failed: {e}")
        return {}


def parse(pdf_path: str | Path) -> dict:
    """
    Parse a Manulife PDF statement.
    Returns a dict with standardized fields.
    """
    pdf_path = Path(pdf_path)
    file_hash = hashlib.md5(pdf_path.read_bytes()).hexdigest()

    result = {
        "institution": "manulife",
        "file": pdf_path.name,
        "file_hash": file_hash,
        "parsed_at": datetime.now().isoformat(),
        "statement_date": None,
        "account_type": "Unknown",
        "total_value": None,
        "cash_balance": None,
        "currency": "CAD",
        "holdings": [],
        "raw_text_snippet": "",
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            result["raw_text_snippet"] = full_text[:500]

            result["account_type"] = _detect_account_type(full_text)
            result["statement_date"] = _extract_statement_date(full_text)
            result["total_value"] = _extract_total_value(full_text)

            holdings = _extract_holdings(pdf.pages)
            if not holdings:
                holdings = _extract_holdings_from_text(full_text)
            result["holdings"] = holdings

    except Exception as e:
        print(f"  pdfplumber failed for {pdf_path.name}: {e}")

    if not result["total_value"] or not result["statement_date"]:
        print(f"  Falling back to Claude API for {pdf_path.name}...")
        claude_data = _parse_with_gemini(pdf_path, result["account_type"])
        if claude_data:
            result.update({k: v for k, v in claude_data.items() if v})

    return result

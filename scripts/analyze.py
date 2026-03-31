"""
Analysis engine: builds all metrics from processed statements + profile.
"""
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
HISTORY_FILE = ROOT / "data" / "history.json"
PROFILE_FILE = ROOT / "data" / "profile.yaml"
CONFIG_FILE = ROOT / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def load_profile() -> dict:
    with open(PROFILE_FILE) as f:
        return yaml.safe_load(f)


def load_history() -> dict:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {"snapshots": []}


def _readable_label(account_id: str) -> str:
    """Convert account_id to a human-readable chart label.
    'H947126K9CAD_TFSA'          → 'WS TFSA'
    'WZ0969XK4CAD_TFSA_PORTFOLIO' → 'WS TFSA Portfolio'
    'HF4253745CAD_RRSP'           → 'WS RRSP'
    'manulife_RPP'                → 'Manulife RPP'
    """
    import re
    ACRONYMS = {"TFSA", "RRSP", "RPP", "RESP", "FHSA", "RRIF", "WS"}
    ws_match = re.match(r'^[A-Z0-9]+CAD_(.+)$', account_id)
    if ws_match:
        words = [w if w in ACRONYMS else w.title() for w in ws_match.group(1).split("_")]
        return "WS " + " ".join(words)
    parts = account_id.split("_", 1)
    if len(parts) == 2:
        inst = parts[0].title()
        typ = parts[1] if parts[1] in ACRONYMS else parts[1].title()
        return f"{inst} {typ}"
    return account_id.replace("_", " ").title()


def load_all_processed() -> list[dict]:
    results = []
    for f in sorted(PROCESSED_DIR.glob("*.json")):
        with open(f) as fp:
            results.append(json.load(fp))
    return results


# ─── Net Worth ────────────────────────────────────────────────────────────────

def net_worth_over_time(history: dict) -> pd.DataFrame:
    """Returns DataFrame: date, total_net_worth, per-account breakdown."""
    rows = []
    for snap in history.get("snapshots", []):
        row = {"date": snap["date"], "total": snap["total_net_worth"]}
        for acct in snap.get("accounts", []):
            key = acct.get("account_id") or f"{acct['institution']}_{acct['account_type']}"
            row[key] = acct["total_value"]
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=["date", "total"])
    df = pd.DataFrame(rows).sort_values("date")
    return df


# ─── Cost Basis vs Market Value ───────────────────────────────────────────────

def cost_basis_summary(latest_per_account: dict[str, dict], all_processed: list[dict],
                       profile: dict | None = None) -> list[dict]:
    """
    Per-account comparison of total deposited vs current market value.
    Priority: (1) profile.yaml override, (2) CRM2 cumulative + post-CRM2 period_deposits,
    (3) sum of all period_deposits (for accounts with no CRM2 yet, e.g. new accounts).
    profile.yaml cost_basis_overrides are used as a hard override (e.g. for Manulife).
    """
    # Find the latest CRM2 report per account (authoritative cumulative deposits through year-end)
    latest_crm2: dict[str, dict] = {}
    for r in all_processed:
        if r.get("record_type") == "crm2":
            key = r.get("account_id", "")
            if key and (key not in latest_crm2 or (r.get("year") or 0) > (latest_crm2[key].get("year") or 0)):
                latest_crm2[key] = r

    # Sum period_deposits from monthly statements AFTER the latest CRM2 year-end
    # (covers partial current year — Jan onwards after last annual report)
    post_crm2_deposits: dict[str, float] = {}
    for r in all_processed:
        if r.get("record_type") == "crm2":
            continue
        key = r.get("account_id") or f"{r['institution']}_{r.get('account_type', '')}"
        crm2_year_end = latest_crm2.get(key, {}).get("year_end", "")
        stmt_date = r.get("statement_date") or ""
        if stmt_date > crm2_year_end:
            deps = r.get("period_deposits") or 0.0
            post_crm2_deposits[key] = post_crm2_deposits.get(key, 0.0) + deps

    overrides = (profile or {}).get("cost_basis_overrides", {}) or {}

    rows = []
    for account_id, r in sorted(latest_per_account.items()):
        total_mkt = r.get("total_value") or 0

        if account_id in overrides and overrides[account_id]:
            # Hard override from profile.yaml — most reliable
            total_invested = round(float(overrides[account_id]), 2)
        elif account_id in latest_crm2:
            # CRM2 annual report: cumulative deposits through year-end + partial current year
            base = latest_crm2[account_id]["cumulative_deposits"]
            post = post_crm2_deposits.get(account_id, 0.0)
            total_invested = round(base + post, 2)
        else:
            # Fallback: sum of period_deposits across all statements (monthly increments)
            summed = post_crm2_deposits.get(account_id, 0.0)
            total_invested = round(summed, 2) if summed else None

        gain = round(total_mkt - total_invested, 2) if total_invested is not None else None
        gain_pct = round(gain / total_invested * 100, 1) if total_invested else None
        rows.append({
            "account_id": account_id,
            "label": _readable_label(account_id),
            "current_value": round(total_mkt, 2),
            "book_value": total_invested,
            "gain": gain,
            "gain_pct": gain_pct,
            "as_of": r.get("statement_date"),
        })
    total_mkt = sum(r["current_value"] for r in rows)
    known_book = [r["book_value"] for r in rows if r["book_value"] is not None]
    total_book = round(sum(known_book), 2) if known_book else None
    gain = round(total_mkt - total_book, 2) if total_book is not None else None
    rows.append({
        "account_id": "total",
        "label": "Total Portfolio",
        "current_value": round(total_mkt, 2),
        "book_value": total_book,
        "gain": gain,
        "gain_pct": round(gain / total_book * 100, 1) if total_book else None,
        "as_of": None,
    })
    return rows


# ─── Asset Allocation ─────────────────────────────────────────────────────────

ASSET_CLASS_MAP = {
    # ETF/fund name keywords → asset class
    "bond": "Fixed Income",
    "fixed income": "Fixed Income",
    "aggregate": "Fixed Income",
    "treasury": "Fixed Income",
    "cash": "Cash",
    "money market": "Cash",
    "equity": "Equities",
    "stock": "Equities",
    "growth": "Equities",
    "dividend": "Equities",
    "index": "Equities",
    "balanced": "Balanced",
    "portfolio": "Balanced",
    "real estate": "Real Estate",
    "reit": "Real Estate",
}

GEOGRAPHY_MAP = {
    "canadian": "Canada",
    "canada": "Canada",
    "cdn": "Canada",
    "us": "USA",
    "united states": "USA",
    "american": "USA",
    "international": "International",
    "global": "Global",
    "emerging": "Emerging Markets",
    "europe": "Europe",
}


def _classify_asset(name: str, symbol: str) -> tuple[str, str]:
    text = (name + " " + symbol).lower()
    asset_class = "Equities"  # default
    for kw, cls in ASSET_CLASS_MAP.items():
        if kw in text:
            asset_class = cls
            break
    geography = "Global"
    for kw, geo in GEOGRAPHY_MAP.items():
        if kw in text:
            geography = geo
            break
    return asset_class, geography


def asset_allocation(processed: list[dict]) -> dict:
    """
    Returns allocation breakdowns by: asset_class, geography, account_type.
    Uses the most recent statement per institution+account_type.
    """
    # Get latest per institution+account_type
    latest: dict[str, dict] = {}
    for r in processed:
        if r.get("record_type") == "crm2":
            continue
        key = f"{r['institution']}_{r['account_type']}"
        r_date = r.get("statement_date") or r.get("parsed_at", "")[:10]
        if key not in latest or r_date > (latest[key].get("statement_date") or ""):
            latest[key] = r

    by_class: dict[str, float] = {}
    by_geo: dict[str, float] = {}
    by_acct: dict[str, float] = {}

    for key, r in latest.items():
        acct_label = _readable_label(r.get("account_id") or key)
        val = r.get("total_value") or 0
        by_acct[acct_label] = by_acct.get(acct_label, 0) + val

        holdings = r.get("holdings", [])
        if holdings:
            for h in holdings:
                hval = h.get("market_value") or 0
                cls, geo = _classify_asset(h.get("name", ""), h.get("symbol", ""))
                by_class[cls] = by_class.get(cls, 0) + hval
                by_geo[geo] = by_geo.get(geo, 0) + hval
        else:
            # No holdings detail — bucket entire account value as unknown
            by_class["Unknown"] = by_class.get("Unknown", 0) + val
            by_geo["Unknown"] = by_geo.get("Unknown", 0) + val

    return {
        "by_asset_class": by_class,
        "by_geography": by_geo,
        "by_account": by_acct,
    }


# ─── Contribution Room ────────────────────────────────────────────────────────

def _age_from_birth_year(birth_year: int, as_of: date) -> int:
    return as_of.year - birth_year


def tfsa_room_analysis(profile: dict, config: dict) -> dict:
    # RULE: Always use CRA-provided room directly — never recalculate from annual limits.
    # See data/INVESTMENT_RULES.md rule #1 and #2.
    birth_year = profile.get("personal", {}).get("birth_year")
    if not birth_year:
        return {"error": "birth_year not set in profile.yaml"}

    today = date.today()
    age = _age_from_birth_year(birth_year, today)

    current_room = profile.get("accounts", {}).get("tfsa", {}).get("current_room_available", 0) or 0
    ytd_contributions = profile.get("accounts", {}).get("tfsa", {}).get("ytd_contributions", 0) or 0
    remaining = max(0, current_room - ytd_contributions)
    annual_limit = config["tfsa_annual_limits"].get(today.year, 0)

    return {
        "age": age,
        "current_room_available": current_room,
        "ytd_contributions": ytd_contributions,
        "remaining_this_year": remaining,
        "annual_limit_this_year": annual_limit,
    }


def rrsp_room_analysis(profile: dict, config: dict) -> dict:
    # RULE: Always use CRA-provided room directly — never recalculate.
    # See data/INVESTMENT_RULES.md rule #1 and #3.
    accts = profile.get("accounts", {}).get("rrsp", {}) or {}
    current_room = accts.get("current_room_available", 0) or 0
    ytd = accts.get("ytd_contributions", 0) or 0

    return {
        "current_room_available": current_room,
        "ytd_contributions": ytd,
        "remaining_this_year": max(0, current_room - ytd),
    }


def prior_year_room_analysis(profile: dict, config: dict) -> dict:
    """
    Reconstruct last year's contribution room usage from CRA-provided data in profile.yaml.
    TFSA: derivable from Jan 1 room + annual limit + known contributions.
    RRSP: use prior_year_contributions if set, else unknown.
    """
    prior = profile.get("prior_year_contributions", {}) or {}
    prior_year = prior.get("year", date.today().year - 1)
    tfsa_room_start = prior.get("tfsa_room_start", 0) or 0
    tfsa_contrib = prior.get("tfsa", 0) or 0
    rrsp_contrib = prior.get("rrsp", 0) or 0
    rrsp_room = prior.get("rrsp_room_start", 0) or 0
    annual_limit_tfsa = config["tfsa_annual_limits"].get(prior_year, 0)
    return {
        "year": prior_year,
        "tfsa": {
            "room_available": tfsa_room_start,
            "contributions": tfsa_contrib,
            "remaining": max(0, tfsa_room_start - tfsa_contrib),
            "annual_limit": annual_limit_tfsa,
        },
        "rrsp": {
            "room_available": rrsp_room,
            "contributions": rrsp_contrib,
            "remaining": max(0, rrsp_room - rrsp_contrib),
        },
    }


def resp_analysis(profile: dict, config: dict) -> list[dict]:
    beneficiaries = profile.get("accounts", {}).get("resp", {}).get("beneficiaries", []) or []
    resp_cfg = config["resp"]
    results = []
    today = date.today()

    for b in beneficiaries:
        name = b.get("name", "Beneficiary")
        birth_year = b.get("birth_year")
        age = _age_from_birth_year(birth_year, today) if birth_year else None
        lifetime_contribs = b.get("lifetime_contributions", 0) or 0
        lifetime_cesg = b.get("lifetime_cesg_received", 0) or 0
        ytd_contribs = b.get("ytd_contributions", 0) or 0

        lifetime_room = resp_cfg["lifetime_limit_per_beneficiary"] - lifetime_contribs
        cesg_room = resp_cfg["cesg_lifetime_max"] - lifetime_cesg
        cesg_this_year = min(ytd_contribs, resp_cfg["cesg_annual_max_contribution"]) * resp_cfg["cesg_rate"]
        potential_cesg_this_year = max(0, (resp_cfg["cesg_annual_max_contribution"] - ytd_contribs) * resp_cfg["cesg_rate"])

        results.append({
            "name": name,
            "age": age,
            "lifetime_contributions": lifetime_contribs,
            "lifetime_room_remaining": lifetime_room,
            "lifetime_cesg_received": lifetime_cesg,
            "cesg_room_remaining": cesg_room,
            "ytd_contributions": ytd_contribs,
            "cesg_earned_this_year": round(cesg_this_year, 2),
            "potential_additional_cesg_this_year": round(potential_cesg_this_year, 2),
        })

    return results


# ─── Historical Growth Rates ──────────────────────────────────────────────────

def calculate_historical_growth_rates(processed: list[dict], config: dict) -> dict[str, float]:
    """
    Calculate annualized growth rate per institution+account_type from statement history.
    - WealthSimple: uses (market_value - book_value) / book_value, annualised by account age.
    - Others: uses period return between earliest and latest statement.
    Falls back to configured risk-tolerance rate if insufficient data.
    """
    from collections import defaultdict

    default_rate = config["dashboard"]["growth_rate_assumptions"].get("moderate-growth", 0.08)
    by_account: dict[str, list] = defaultdict(list)

    for r in processed:
        if r.get("total_value") and r.get("statement_date"):
            key = r.get("account_id") or f"{r['institution']}_{r['account_type']}"
            by_account[key].append(r)

    rates: dict[str, float] = {}
    for key, records in by_account.items():
        records.sort(key=lambda x: x["statement_date"])
        latest = records[-1]

        # WealthSimple holdings carry book_value — use return-on-cost
        holdings = latest.get("holdings", [])
        with_book = [h for h in holdings if h.get("book_value") and h.get("market_value")]
        if with_book:
            total_book = sum(h["book_value"] for h in with_book)
            total_mkt = sum(h["market_value"] for h in with_book)
            if total_book > 0:
                try:
                    first_dt = datetime.strptime(records[0]["statement_date"], "%Y-%m-%d")
                    last_dt = datetime.strptime(latest["statement_date"], "%Y-%m-%d")
                    years = max(0.5, (last_dt - first_dt).days / 365.25)
                    total_return = (total_mkt - total_book) / total_book
                    annualised = (1 + total_return) ** (1 / years) - 1
                    rates[key] = round(max(0.01, min(0.25, annualised)), 4)
                    continue
                except (ValueError, ZeroDivisionError):
                    pass

        # Manulife / others: use raw period return (includes contributions, so treat as floor)
        if len(records) >= 2:
            first, last = records[0], records[-1]
            v0, v1 = first["total_value"], last["total_value"]
            try:
                first_dt = datetime.strptime(first["statement_date"], "%Y-%m-%d")
                last_dt = datetime.strptime(last["statement_date"], "%Y-%m-%d")
                years = max(0.25, (last_dt - first_dt).days / 365.25)
                if v0 > 0 and v1 > 0:
                    annualised = (v1 / v0) ** (1 / years) - 1
                    # Contributions inflate this number — cap at reasonable max
                    rates[key] = round(max(0.01, min(0.20, annualised)), 4)
                    continue
            except (ValueError, ZeroDivisionError):
                pass

        rates[key] = default_rate

    return rates


# ─── Projections ──────────────────────────────────────────────────────────────

def project_portfolio(profile: dict, config: dict, current_total: float, processed: list[dict]) -> dict:
    birth_year = profile.get("personal", {}).get("birth_year")
    today = date.today()
    current_age = _age_from_birth_year(birth_year, today) if birth_year else 35
    target_age = profile.get("retirement", {}).get("target_retirement_age", 65)
    years = max(0, target_age - current_age)
    inflation = config["dashboard"]["inflation_rate"]
    annual_withdrawal = 1000  # user preference: minimal withdrawals

    # Historical growth rates from actual statements
    hist_rates = calculate_historical_growth_rates(processed, config)
    default_rate = config["dashboard"]["growth_rate_assumptions"].get(
        profile.get("risk_tolerance", "balanced"), 0.07
    )

    # Per-account: get latest value + assigned growth rate
    latest_by_key: dict[str, dict] = {}
    for r in processed:
        if r.get("total_value") and r.get("statement_date"):
            key = r.get("account_id") or f"{r['institution']}_{r['account_type']}"
            if key not in latest_by_key or r["statement_date"] > latest_by_key[key]["statement_date"]:
                latest_by_key[key] = r

    per_account_projections = []
    for key, rec in sorted(latest_by_key.items()):
        rate = hist_rates.get(key, default_rate)
        label = _readable_label(rec.get("account_id") or key)
        value = rec["total_value"]
        series = []
        for yr in range(years + 1):
            series.append({"year": today.year + yr, "value": round(value, 2)})
            value = value * (1 + rate)
        per_account_projections.append({
            "key": key,
            "label": label,
            "current_value": rec["total_value"],
            "growth_rate": rate,
            "projected_at_retirement": round(series[-1]["value"], 2) if series else 0,
            "series": series,
        })

    contrib_plans = profile.get("contribution_plans", {}) or {}
    annual_contribution = (
        (contrib_plans.get("tfsa_annual", 0) or 0)
        + (contrib_plans.get("rrsp_annual_personal", 0) or 0)
        + (contrib_plans.get("manulife_rrsp_annual", 0) or 0)
    )
    avg_rate = (
        sum(a["growth_rate"] * a["current_value"] for a in per_account_projections)
        / sum(a["current_value"] for a in per_account_projections)
        if per_account_projections else default_rate
    )

    # Total series = sum of per-account series so chart lines always add up correctly
    total_series = []
    for yr in range(years + 1):
        yr_total = sum(acct["series"][yr]["value"] for acct in per_account_projections)
        total_series.append({
            "year": today.year + yr,
            "age": current_age + yr,
            "value": round(yr_total, 2),
            "value_real": round(yr_total / ((1 + inflation) ** yr), 2),
        })

    final_value = total_series[-1]["value"] if total_series else 0

    return {
        "years_to_retirement": years,
        "average_growth_rate": round(avg_rate, 4),
        "annual_contribution": annual_contribution,
        "annual_withdrawal": annual_withdrawal,
        "projected_value_at_retirement": round(final_value, 2),
        "projected_value_real": total_series[-1]["value_real"] if total_series else 0,
        "projection_series": total_series,
        "per_account_projections": per_account_projections,
        "on_track": None,
    }


# ─── Suggestions ──────────────────────────────────────────────────────────────

def generate_suggestions(tfsa: dict, rrsp: dict, resp_list: list, projection: dict) -> list[dict]:
    suggestions = []

    # TFSA
    if tfsa.get("remaining_this_year", 0) > 0:
        suggestions.append({
            "priority": "high",
            "account": "TFSA",
            "message": f"You have ${tfsa['remaining_this_year']:,.0f} unused TFSA room this year. "
                       f"Unused room carries forward, but tax-free growth is lost forever.",
        })

    # RRSP
    if rrsp.get("remaining_this_year", 0) > 5000:
        suggestions.append({
            "priority": "medium",
            "account": "RRSP",
            "message": f"${rrsp['remaining_this_year']:,.0f} in RRSP room available. "
                       f"Contributing reduces your taxable income for this year.",
        })

    # RESP / CESG
    for b in resp_list:
        if b.get("potential_additional_cesg_this_year", 0) > 0 and (b.get("age") or 0) < 17:
            suggestions.append({
                "priority": "high",
                "account": "RESP",
                "message": f"RESP for {b['name']}: contribute ${b['potential_additional_cesg_this_year'] / 0.20:,.0f} more "
                           f"to capture ${b['potential_additional_cesg_this_year']:,.0f} in CESG grants this year.",
            })

    # Retirement projection
    if projection.get("on_track") is False:
        shortfall = projection["income_needed_from_portfolio"] - projection["safe_withdrawal_annual"]
        suggestions.append({
            "priority": "high",
            "account": "Retirement",
            "message": f"Based on current projections, you may face a ${shortfall:,.0f}/year shortfall at retirement. "
                       f"Consider increasing annual contributions or adjusting your target.",
        })
    elif projection.get("on_track") is True:
        suggestions.append({
            "priority": "info",
            "account": "Retirement",
            "message": f"On track for retirement! Projected portfolio: ${projection['projected_value_at_retirement']:,.0f} "
                       f"at age {(projection.get('years_to_retirement', 0) + 0)}.",
        })

    return suggestions


# ─── Main ─────────────────────────────────────────────────────────────────────

def run() -> dict:
    print("=== Analysis Engine ===")
    config = load_config()
    profile = load_profile()
    history = load_history()
    processed = load_all_processed()

    net_worth_df = net_worth_over_time(history)

    # current_total = sum of the LATEST known value per account (from processed files).
    # This handles accounts with different statement frequencies correctly — Manulife
    # quarterly data is not dropped just because WealthSimple has a more recent month.
    latest_per_account: dict[str, dict] = {}
    for r in processed:
        if r.get("total_value") and r.get("statement_date"):
            key = r.get("account_id") or f"{r['institution']}_{r['account_type']}"
            if key not in latest_per_account or r["statement_date"] > latest_per_account[key]["statement_date"]:
                latest_per_account[key] = r
    current_total = sum(r["total_value"] for r in latest_per_account.values())

    cost_basis = cost_basis_summary(latest_per_account, processed, profile)
    allocation = asset_allocation(processed)
    tfsa = tfsa_room_analysis(profile, config)
    rrsp = rrsp_room_analysis(profile, config)
    resp = resp_analysis(profile, config)
    prior_year_room = prior_year_room_analysis(profile, config)
    projection = project_portfolio(profile, config, current_total, processed)
    suggestions = generate_suggestions(tfsa, rrsp, resp, projection)

    result = {
        "generated_at": datetime.now().isoformat(),
        "current_total": current_total,
        "net_worth_series": net_worth_df.to_dict(orient="records"),
        "allocation": allocation,
        "contribution_room": {
            "tfsa": tfsa,
            "rrsp": rrsp,
            "resp": resp,
            "prior_year": prior_year_room,
        },
        "projection": projection,
        "cost_basis": cost_basis,
        "suggestions": suggestions,
    }

    print(f"Analysis complete. Net worth: ${current_total:,.2f}")
    return result


if __name__ == "__main__":
    run()

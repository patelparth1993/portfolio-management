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
            key = f"{acct['institution']}_{acct['account_type']}"
            row[key] = acct["total_value"]
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=["date", "total"])
    df = pd.DataFrame(rows).sort_values("date")
    return df


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
        key = f"{r['institution']}_{r['account_type']}"
        r_date = r.get("statement_date") or r.get("parsed_at", "")[:10]
        if key not in latest or r_date > (latest[key].get("statement_date") or ""):
            latest[key] = r

    by_class: dict[str, float] = {}
    by_geo: dict[str, float] = {}
    by_acct: dict[str, float] = {}

    for key, r in latest.items():
        acct_label = f"{r['institution'].title()} {r['account_type']}"
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

def _age_on(dob_str: str, as_of: date) -> int:
    dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
    return as_of.year - dob.year - ((as_of.month, as_of.day) < (dob.month, dob.day))


def tfsa_room_analysis(profile: dict, config: dict) -> dict:
    dob = profile.get("personal", {}).get("date_of_birth", "")
    if not dob:
        return {"error": "Date of birth not set in profile.yaml"}

    today = date.today()
    age = _age_on(dob, today)
    limits = config["tfsa_annual_limits"]

    # Cumulative room since 18 (or 2009 when TFSA started)
    start_year = max(2009, datetime.strptime(dob, "%Y-%m-%d").year + 18)
    total_room_ever = sum(v for yr, v in limits.items() if start_year <= yr <= today.year)

    current_room = profile.get("accounts", {}).get("tfsa", {}).get("current_room_available", 0) or 0
    ytd_contributions = profile.get("accounts", {}).get("tfsa", {}).get("ytd_contributions", 0) or 0
    remaining = max(0, current_room - ytd_contributions)

    return {
        "age": age,
        "start_year": start_year,
        "cumulative_room_ever": total_room_ever,
        "current_room_available": current_room,
        "ytd_contributions": ytd_contributions,
        "remaining_this_year": remaining,
        "annual_limit_this_year": limits.get(today.year, 0),
    }


def rrsp_room_analysis(profile: dict, config: dict) -> dict:
    accts = profile.get("accounts", {}).get("rrsp", {}) or {}
    current_room = accts.get("current_room_available", 0) or 0
    ytd = accts.get("ytd_contributions", 0) or 0
    prior_income = profile.get("personal", {}).get("prior_year_income", 0) or 0
    annual_limit = config["rrsp"].get(f"annual_limit_{date.today().year}", 32490)
    new_room_this_year = min(prior_income * 0.18, annual_limit)

    return {
        "current_room_available": current_room,
        "ytd_contributions": ytd,
        "remaining_this_year": max(0, current_room - ytd),
        "new_room_accruing_this_year": round(new_room_this_year, 2),
        "annual_limit": annual_limit,
    }


def resp_analysis(profile: dict, config: dict) -> list[dict]:
    beneficiaries = profile.get("accounts", {}).get("resp", {}).get("beneficiaries", []) or []
    resp_cfg = config["resp"]
    results = []
    today = date.today()

    for b in beneficiaries:
        name = b.get("name", "Beneficiary")
        dob = b.get("date_of_birth", "")
        age = _age_on(dob, today) if dob else None
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


# ─── Projections ──────────────────────────────────────────────────────────────

def project_portfolio(profile: dict, config: dict, current_total: float) -> dict:
    dob = profile.get("personal", {}).get("date_of_birth", "")
    today = date.today()
    current_age = _age_on(dob, today) if dob else 35
    target_age = profile.get("retirement", {}).get("target_retirement_age", 65)
    years = max(0, target_age - current_age)

    risk = profile.get("risk_tolerance", "balanced")
    growth_rate = config["dashboard"]["growth_rate_assumptions"].get(risk, 0.07)
    inflation = config["dashboard"]["inflation_rate"]

    annual_contribution = (
        (profile.get("contribution_plans", {}).get("tfsa_annual", 0) or 0)
        + (profile.get("contribution_plans", {}).get("rrsp_annual", 0) or 0)
        + (profile.get("contribution_plans", {}).get("resp_annual", 0) or 0)
    )

    # Year-by-year projection
    projection = []
    value = current_total
    for yr in range(years + 1):
        projection.append({
            "year": today.year + yr,
            "age": current_age + yr,
            "value": round(value, 2),
            "value_real": round(value / ((1 + inflation) ** yr), 2),
        })
        value = (value + annual_contribution) * (1 + growth_rate)

    target_monthly = profile.get("retirement", {}).get("target_monthly_income", 0) or 0
    cpp = profile.get("retirement", {}).get("cpp_estimate_monthly", 0) or 0
    oas = profile.get("retirement", {}).get("oas_estimate_monthly", 727) or 727
    portfolio_income_needed = max(0, target_monthly - cpp - oas) * 12  # annual

    final_value = projection[-1]["value"] if projection else 0
    safe_withdrawal = final_value * 0.04  # 4% rule

    return {
        "years_to_retirement": years,
        "growth_rate": growth_rate,
        "annual_contribution": annual_contribution,
        "projected_value_at_retirement": round(final_value, 2),
        "projected_value_real": projection[-1]["value_real"] if projection else 0,
        "safe_withdrawal_annual": round(safe_withdrawal, 2),
        "income_needed_from_portfolio": round(portfolio_income_needed, 2),
        "on_track": safe_withdrawal >= portfolio_income_needed if portfolio_income_needed > 0 else None,
        "projection_series": projection,
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
    current_total = float(net_worth_df["total"].iloc[-1]) if not net_worth_df.empty else 0.0

    allocation = asset_allocation(processed)
    tfsa = tfsa_room_analysis(profile, config)
    rrsp = rrsp_room_analysis(profile, config)
    resp = resp_analysis(profile, config)
    projection = project_portfolio(profile, config, current_total)
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
        },
        "projection": projection,
        "suggestions": suggestions,
    }

    print(f"Analysis complete. Net worth: ${current_total:,.2f}")
    return result


if __name__ == "__main__":
    run()

#!/usr/bin/env python3
"""
Portfolio Management Dashboard — Entry Point

Usage:
  python run.py               Full pipeline: pull from Drive → analyze → publish dashboard
  python run.py --report-only Skip ingestion, just regenerate dashboard from existing data
  python run.py --setup       Run interactive profile setup wizard
"""
import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
SCRIPTS = ROOT / "scripts"
DASHBOARD = ROOT / "dashboard" / "index.html"

sys.path.insert(0, str(SCRIPTS))


def run_setup():
    import yaml

    profile_path = ROOT / "data" / "profile.yaml"
    with open(profile_path) as f:
        profile = yaml.safe_load(f)

    print("\n=== Portfolio Setup Wizard ===")
    print("Press Enter to keep existing value. Saved to data/profile.yaml\n")

    def ask(prompt, current, cast=str):
        display = f" [{current}]" if current else ""
        val = input(f"{prompt}{display}: ").strip()
        if not val:
            return current
        try:
            return cast(val)
        except (ValueError, TypeError):
            print(f"  Invalid input, keeping: {current}")
            return current

    p = profile.get("personal", {})
    p["date_of_birth"] = ask("Date of birth (YYYY-MM-DD)", p.get("date_of_birth", ""))
    p["province"] = ask("Province (e.g. ON, BC, AB)", p.get("province", ""))
    p["annual_income"] = ask("Current year gross income ($)", p.get("annual_income", 0), float)
    p["prior_year_income"] = ask("Prior year gross income ($)", p.get("prior_year_income", 0), float)
    profile["personal"] = p

    accts = profile.get("accounts", {})

    tfsa = accts.get("tfsa", {})
    print("\n--- TFSA ---")
    tfsa["current_room_available"] = ask("Current TFSA room (from CRA My Account)", tfsa.get("current_room_available", 0), float)
    tfsa["ytd_contributions"] = ask("TFSA contributions this year so far", tfsa.get("ytd_contributions", 0), float)
    tfsa["ytd_withdrawals"] = ask("TFSA withdrawals this year", tfsa.get("ytd_withdrawals", 0), float)
    accts["tfsa"] = tfsa

    rrsp = accts.get("rrsp", {})
    print("\n--- RRSP ---")
    rrsp["current_room_available"] = ask("Current RRSP room (from CRA/NOA)", rrsp.get("current_room_available", 0), float)
    rrsp["ytd_contributions"] = ask("RRSP contributions this year so far", rrsp.get("ytd_contributions", 0), float)
    accts["rrsp"] = rrsp

    resp_cfg = accts.get("resp", {})
    beneficiaries = resp_cfg.get("beneficiaries") or [{"name": "", "date_of_birth": "", "lifetime_contributions": 0, "lifetime_cesg_received": 0, "ytd_contributions": 0}]
    print("\n--- RESP Beneficiaries ---")
    updated_bens = []
    for i, b in enumerate(beneficiaries):
        print(f"  Beneficiary {i + 1}:")
        b["name"] = ask("    Name", b.get("name", ""))
        b["date_of_birth"] = ask("    Date of birth (YYYY-MM-DD)", b.get("date_of_birth", ""))
        b["lifetime_contributions"] = ask("    Lifetime RESP contributions so far", b.get("lifetime_contributions", 0), float)
        b["lifetime_cesg_received"] = ask("    Lifetime CESG received so far", b.get("lifetime_cesg_received", 0), float)
        b["ytd_contributions"] = ask("    Contributions this year", b.get("ytd_contributions", 0), float)
        updated_bens.append(b)
    resp_cfg["beneficiaries"] = updated_bens
    accts["resp"] = resp_cfg
    profile["accounts"] = accts

    print("\n--- Contribution Plans (Annual) ---")
    plans = profile.get("contribution_plans", {})
    plans["tfsa_annual"] = ask("Planned annual TFSA contribution ($)", plans.get("tfsa_annual", 0), float)
    plans["rrsp_annual"] = ask("Planned annual RRSP contribution ($)", plans.get("rrsp_annual", 0), float)
    plans["resp_annual"] = ask("Planned annual RESP contribution per beneficiary ($)", plans.get("resp_annual", 0), float)
    profile["contribution_plans"] = plans

    print("\n--- Retirement Goals ---")
    ret = profile.get("retirement", {})
    ret["target_retirement_age"] = ask("Target retirement age", ret.get("target_retirement_age", 65), int)
    ret["target_monthly_income"] = ask("Target monthly retirement income in today's dollars ($)", ret.get("target_monthly_income", 0), float)
    ret["cpp_estimate_monthly"] = ask("Estimated monthly CPP benefit (from Service Canada)", ret.get("cpp_estimate_monthly", 0), float)
    ret["oas_estimate_monthly"] = ask("Estimated monthly OAS benefit", ret.get("oas_estimate_monthly", 727), float)
    profile["retirement"] = ret

    risk = ask("Risk tolerance (conservative / balanced / growth)", profile.get("risk_tolerance", "balanced"))
    if risk in ("conservative", "balanced", "growth"):
        profile["risk_tolerance"] = risk

    with open(profile_path, "w") as f:
        yaml.dump(profile, f, default_flow_style=False, allow_unicode=True)
    print(f"\nProfile saved to {profile_path}")


def main():
    parser = argparse.ArgumentParser(description="Portfolio Dashboard")
    parser.add_argument("--report-only", action="store_true", help="Skip Drive pull, regenerate dashboard only")
    parser.add_argument("--setup", action="store_true", help="Run interactive profile setup")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser after generation")
    args = parser.parse_args()

    if args.setup:
        run_setup()
        print("\nRun `python run.py` to generate the dashboard.\n")
        return

    if not args.report_only:
        from ingest import run as ingest_run
        ingest_run()

    from analyze import run as analyze_run
    analysis = analyze_run()

    from report import render
    output = render(analysis)

    if not args.no_open:
        print(f"Opening dashboard: {output}")
        webbrowser.open(f"file://{output.resolve()}")


if __name__ == "__main__":
    main()

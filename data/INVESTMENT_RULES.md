# Investment Rules & Data Policies

## Critical Rules

### 1. Always use CRA-provided numbers — never calculate independently
- TFSA available room must come directly from CRA My Account
- RRSP available room must come directly from the Notice of Assessment (NOA) or CRA My Account
- Do NOT calculate contribution room from annual limits or any formula
- Annual limits in config.yaml are reference data only — they must never override CRA-verified figures in profile.yaml

**Why this matters**: CRA tracks your actual contribution history, withdrawals, pension adjustments, and re-contribution eligibility. Any independent calculation will be wrong.

### 2. TFSA room: history and source
- Room accrues from the year you became a Canadian resident (not from age 18)
- Canadian resident since February 2020 → TFSA room starts 2020
- Withdrawals made in year N are added back to room on January 1 of year N+1
- Always use the CRA "as of January 1" figure for the current year
- Historical room (from CRA My Account):
  | Date | Available Room |
  |------|---------------|
  | Jan 1, 2020 | $6,000.00 |
  | Jan 1, 2021 | $6,500.00 |
  | Jan 1, 2022 | $19,562.89 |
  | Jan 1, 2023 | $23,062.89 |
  | Jan 1, 2024 | $27,562.89 |
  | Jan 1, 2026 | $30,562.89 |

### 3. RRSP room: history and source
- Always use the NOA or CRA My Account figure — never recalculate
- The "deduction limit" shown on CRA is the ACCUMULATED available room (lifetime, not just one year)
- Historical RRSP deduction limits (from CRA, as of March 30, 2026):
  | Year | Deduction Limit |
  |------|----------------|
  | 2021 | $8,947.00 |
  | 2022 | $15,749.00 |
  | 2023 | $33,209.00 |
  | 2024 | $51,729.00 |
  | 2025 | $69,073.00 |

### 4. HBP (Home Buyers' Plan)
- HBP repayments go INTO the RRSP but do NOT add to RRSP deduction room
- Track separately — do not include in RRSP contribution plans
- Minimum required: $621/year (2026), balance remaining: $7,453

### 5. RPP (Registered Pension Plan) is not RRSP
- Employer pension plan (RPP/Manulife) contributions reduce RRSP room via Pension Adjustment (PA)
- RPP funds are locked-in under Ontario jurisdiction
- Do not treat RPP and RRSP as the same account type

### 6. Dashboard focus
- Primary purpose: monitor CURRENT portfolio values across all accounts
- Retirement projections are secondary / future feature
- Always show values sourced directly from statements, not estimated

---

## Deposit Calculation Rules (per account)

These rules govern how `cost_basis_summary()` in `analyze.py` determines total money deposited into each account. The goal is to show actual cash in (not book value, not proceeds from sales, not dividends).

### General principles
- **Never sum monthly `period_deposits` across all statements** — WealthSimple statements report YTD running totals, not monthly increments, so summing them massively overcounts
- **CRM2 Annual Investment Performance Reports are the authoritative source** for WealthSimple accounts — they contain cumulative deposits since account opening in one definitive figure
- For periods after the latest CRM2 year-end, add `period_deposits` from monthly statements dated strictly after Dec 31 of the CRM2 year
- `period_deposits` captures only new external cash: deposits, contributions — NOT sales proceeds, dividends, or interest

### WealthSimple TFSA — Self-Managed (H947126K9CAD_TFSA)
- **Source**: CRM2 Annual Investment Performance Reports (`CRM2_Annual_Investment_Performance_Report_YYYY.pdf`)
- **Field**: `cumulative_deposits` = "Since You Opened Your Account: Deposits $X" column
- **Post-CRM2**: add `period_deposits` from monthly statements dated after the latest CRM2 year-end (Dec 31)
- **Parsed from**: "Cash Paid In  Deposits $X" line in monthly statements — this is a **monthly increment**, not YTD
- CRM2 files live on Google Drive; gdrive.py downloads files containing "INVESTMENT_PERFORMANCE" in the name

**Validation rule (TFSA):**
`Cash Paid In Deposits` is a monthly figure. The sum of all 12 monthly values for a given year must equal the December statement's `Contributions (year to date)`. Use this as a cross-check when verifying parsed data.

Example:
- Jan: deposited $1,000 → `Cash Paid In Deposits = $1,000`, `Contributions YTD = $1,000`
- Feb: deposited $2,000 → `Cash Paid In Deposits = $2,000`, `Contributions YTD = $3,000`
- Sum of monthly deposits ($1k + $2k + …) = December `Contributions YTD`

### WealthSimple RRSP (HF4253745CAD_RRSP)
- **Source**: CRM2 Annual Investment Performance Reports (same approach as TFSA)
- **Post-CRM2**: add `period_deposits` from monthly statements after the latest CRM2 year-end
- **Parsed from**: "First 60 Days $X" + "Rest of Year $X" contribution lines in monthly statements — both are monthly increments
- **Override** in `profile.yaml > cost_basis_overrides`: set `HF4253745CAD_RRSP: 4000` as long as total is known and CRM2 may not be current

**Validation rule (RRSP):**
Same monthly-increment logic applies, but with a contribution year offset for the first 60 days:
- Deposits made **Jan 1 – ~March 1** ("First 60 Days") count toward the **prior tax year's** RRSP contribution room
- Deposits made **after ~March 1** ("Rest of Year") count toward the **current tax year's** RRSP contribution room
- To validate annual RRSP contributions: sum `First 60 Days` from Jan/Feb statements + sum `Rest of Year` from Mar–Dec statements for a given calendar year. This equals the CRA RRSP contribution figure for that tax year (after accounting for the prior-year attribution of the first 60 days).

### WealthSimple TFSA Portfolio — Managed (WZ0969XK4CAD_TFSA_PORTFOLIO)
- **Account opened**: late 2025 — no CRM2 report exists yet
- **Current rule**: use `period_deposits` from the **December statement only** — December statements show YTD contributions which equals the full-year total
- **Future**: once a CRM2 is available on GDrive, switch to the standard CRM2 approach
- **Do NOT** sum `period_deposits` across all monthly statements (YTD over-counting)

### Manulife RPP (manulife_RPP)
- **Source**: manual override in `profile.yaml > cost_basis_overrides: manulife_RPP`
- **Current value**: $10,800 (member + employer match to date)
- **Future**: parse "Plus money that went in" section from Manulife quarterly PDF statements
- Update the override each quarter until automated parsing is implemented

### Manulife RRSP (manulife_RRSP)
- **Source**: manual override in `profile.yaml > cost_basis_overrides: manulife_RRSP`
- **Current value**: $1,800 (payroll contributions to Manulife group RRSP to date)
- **Future**: parse "Plus money that went in" section from Manulife quarterly PDF statements
- Update the override each quarter until automated parsing is implemented

### Fallback (any account without CRM2 or override)
- Use sum of `book_value` across all holdings + `cash_balance` from the latest statement
- This slightly overstates deposits because it includes reinvested dividends in book cost
- Flag clearly on dashboard as "estimated (book cost)"

---
*Last updated: 2026-03-31*

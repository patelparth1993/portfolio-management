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
- Parth became resident: February 2020 → TFSA room starts 2020
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
- Deloitte pension plan (Manulife) contributions reduce RRSP room via Pension Adjustment (PA)
- RPP funds are locked-in under Ontario jurisdiction
- Do not treat RPP and RRSP as the same account type

### 6. Dashboard focus
- Primary purpose: monitor CURRENT portfolio values across all accounts
- Retirement projections are secondary / future feature
- Always show values sourced directly from statements, not estimated

---
*Last updated: 2026-03-30*

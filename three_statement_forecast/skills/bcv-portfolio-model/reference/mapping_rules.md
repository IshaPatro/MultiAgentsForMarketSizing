# Mapping rules: raw exports → model inputs

## Trial balance → opening balance sheet (`Open_*`)

Use the balance as of the month before `Model_Start_Date`. Skip title rows, blank rows and any
row whose account name starts with "Total"/"Subtotal". Parse `1,234.00`, `(1,234)` and `-1234`.
Net = debit − credit for assets; credit − debit for liabilities and equity.

| Model input | Account names that map here (case-insensitive, partial) |
|---|---|
| `Open_Cash` | cash, bank, checking, operating, money market, sweep, card clearing |
| `Open_AR` | accounts receivable, A/R, trade receivable, other receivable |
| `Open_Prepaid` | prepaid, deposits (current) |
| `Open_PPE` | fixed assets, equipment, computers, furniture, leasehold **net of** accumulated depreciation |
| `Open_AP` | accounts payable, A/P |
| `Open_Accrued` | accrued payroll, accrued bonus, accrued expenses, accrued liabilities |
| `Open_Deferred_Rev` | deferred revenue, unearned revenue, contract liabilities |
| `Open_Debt` | loan, venture debt, notes payable, term loan |
| `Open_Paid_In_Capital` | common stock, preferred stock, APIC, additional paid-in capital |
| `Open_Retained_Earnings` | retained earnings, accumulated deficit, current-year earnings |

Check: debits = credits on the raw TB, then `validate_inputs.py` checks assets = L + E.

## CRM / billing → revenue drivers

- **Clean first:** drop exact duplicates and duplicate customer IDs (keep the latest row);
  keep `status` in {Active, Live, Customer}; parse ARR strings like `$54,000`, `54k`, `54K USD`.
- `Open_Customers` = count of active unique customers. `Open_ARR` = sum of their ARR.
- `New_ACV` = mean ARR of closed-won *new logo* deals in the trailing 3–6 months.
- `Expansion_Monthly` = mean monthly closed-won *upsell/expansion* ARR ÷ ARR at the start of
  that month. Fewer than 6 months of history → confidence ≤ 0.65.
- `Logo_Churn_Monthly` = churned logos per month ÷ beginning customers. Needs churn dates;
  without them use the benchmark (0.8% for mid-market SaaS) at confidence ≤ 0.55.
- `Services_Pct_Sub` = services revenue ÷ subscription revenue from the P&L.

## Payroll / HRIS → headcount and cost

Normalise departments: Eng, Engineering, Product, Design, Data, R&D → `RD`;
Sales, SDR, BDR, AE, Marketing, RevOps → `SM`; Finance, People, HR, Legal, Ops, Admin, G&A → `GA`;
Support, CS, Customer Success, Onboarding, Implementation → `CS`.
Exclude terminated employees and contractors unless told otherwise.
`Open_HC_*` = active FTE count. `Cost_FTE_*` = mean annual base × (1 + burden rate); state the
burden rate and where it came from in evidence.

## Management P&L / invoices → cost drivers

- `Hosting_Pct` = trailing-3-month hosting (AWS/GCP/Azure) ÷ subscription revenue.
- `Marketing_Programs_Monthly`, `Other_GA_Monthly` = trailing-3-month average.
- `Software_Per_FTE_Monthly`, `Rent_Per_FTE_Monthly` = trailing-3-month cost ÷ average headcount.

## Plans, notes and term sheets → plan inputs

Hiring plans, new-logo targets, raise timing and size are `User` source (management) with
confidence reflecting how committed they are: signed/approved 0.8, plan 0.7, aspiration 0.6.
Ranges ("$35–45M") → use the midpoint and say so. Model month # = months after the month
before `Model_Start_Date` (Jan of the first forecast year = 1).

# Input catalog

Every hardcoded number in the model. Write values to `inputs.json` under `inputs.<Name>` as `{value, source, confidence, evidence}`. Percentages are fractions (0.09 = 9%). Sources: User, Data-derived, Agent default, Benchmark.

## Company & model setup

| Name | Meaning | Unit | Default |
|---|---|---|---|
| `Company_Name` | Company name. | text | Sample SaaS Co. (Illustrative) |
| `Sector` | BCV sector. One of: AI, Cybersecurity, Infrastructure, Healthcare, Business Application, Open Source. | text | Cybersecurity |
| `Stage` | Stage. One of: Seed, Series A, Series B, Series C, Growth. | text | Series B |
| `Currency` | Reporting currency. | text | USD |
| `Model_Start_Date` | First forecast month. | date | 2026-01-01 |
| `Last_Round_Post_Money` | Last round post-money valuation. | $ | 180000000 |
| `BCV_Ownership` | BCV fully-diluted ownership. | % | 0.09 |

## Model controls

| Name | Meaning | Unit | Default |
|---|---|---|---|
| `Conf_Threshold` | Confidence threshold for flagging. | % | 0.7 |
| `Check_Tolerance` | Integrity-check tolerance. | $ | 1 |
| `Min_Runway_Months` | Minimum acceptable runway. | months | 12 |

## Opening balance sheet (as of month before first forecast month)

| Name | Meaning | Unit | Default |
|---|---|---|---|
| `Open_Cash` | Cash. | $ | 22000000 |
| `Open_AR` | Accounts receivable. | $ | 1450000 |
| `Open_Prepaid` | Prepaid expenses. | $ | 300000 |
| `Open_PPE` | PP&E, net. | $ | 600000 |
| `Open_AP` | Accounts payable. | $ | 450000 |
| `Open_Accrued` | Accrued liabilities. | $ | 410000 |
| `Open_Deferred_Rev` | Deferred revenue. | $ | 3600000 |
| `Open_Debt` | Debt. | $ | 0 |
| `Open_Paid_In_Capital` | Paid-in capital. | $ | 55000000 |
| `Open_Retained_Earnings` | Retained earnings (accumulated deficit). | $ | -35110000 |

## Revenue drivers

| Name | Meaning | Unit | Default |
|---|---|---|---|
| `Open_Customers` | Opening customers. | # | 220 |
| `Open_ARR` | Opening ARR. | $ | 12000000 |
| `New_ACV` | ACV of new logos (Year 1). | $ | 60000 |
| `Price_Increase_Annual` | Annual price increase on new ACV. | % | 0.03 |
| `New_Logos_Y1` | New logos per month: Year 1. | # | 8 |
| `New_Logos_Y2` | New logos per month: Year 2. | # | 11 |
| `New_Logos_Y3` | New logos per month: Year 3. | # | 14 |
| `Logo_Churn_Monthly` | Monthly logo / ARR churn. | % | 0.008 |
| `Expansion_Monthly` | Monthly expansion (% of BoP ARR). | % | 0.015 |
| `Services_Pct_Sub` | Services revenue (% of subscription). | % | 0.06 |

## Billing & working capital

| Name | Meaning | Unit | Default |
|---|---|---|---|
| `Annual_Prepay_Mix` | Share of ARR billed annually upfront. | % | 0.6 |
| `Avg_Deferred_Months` | Avg. unearned months on annual contracts. | months | 6 |
| `DSO_Days` | DSO. | days | 45 |
| `DPO_Days` | DPO (on non-headcount spend). | days | 30 |
| `Accrued_Pct_Payroll` | Accrued liabilities (% of monthly payroll). | % | 0.25 |
| `Prepaid_Months` | Prepaid (months of non-headcount spend). | months | 0.5 |

## Cost of revenue

| Name | Meaning | Unit | Default |
|---|---|---|---|
| `Hosting_Pct` | Hosting & infra (% of subscription revenue). | % | 0.09 |
| `Services_Margin` | Professional services gross margin. | % | 0.2 |

## Headcount plan

| Name | Meaning | Unit | Default |
|---|---|---|---|
| `Open_HC_RD` | Opening headcount: R&D. | FTE | 45 |
| `Open_HC_SM` | Opening headcount: Sales & Marketing. | FTE | 35 |
| `Open_HC_GA` | Opening headcount: G&A. | FTE | 12 |
| `Open_HC_CS` | Opening headcount: Customer Success (COGS). | FTE | 14 |
| `Hires_RD_Y1` | Net hires per month: R&D, Year 1. | FTE | 1.5 |
| `Hires_RD_Y2` | Net hires per month: R&D, Year 2. | FTE | 1.5 |
| `Hires_RD_Y3` | Net hires per month: R&D, Year 3. | FTE | 1.0 |
| `Hires_SM_Y1` | Net hires per month: Sales & Marketing, Year 1. | FTE | 1.5 |
| `Hires_SM_Y2` | Net hires per month: Sales & Marketing, Year 2. | FTE | 2.0 |
| `Hires_SM_Y3` | Net hires per month: Sales & Marketing, Year 3. | FTE | 2.0 |
| `Hires_GA_Y1` | Net hires per month: G&A, Year 1. | FTE | 0.5 |
| `Hires_GA_Y2` | Net hires per month: G&A, Year 2. | FTE | 0.5 |
| `Hires_GA_Y3` | Net hires per month: G&A, Year 3. | FTE | 0.5 |
| `Hires_CS_Y1` | Net hires per month: Customer Success (COGS), Year 1. | FTE | 0.5 |
| `Hires_CS_Y2` | Net hires per month: Customer Success (COGS), Year 2. | FTE | 0.75 |
| `Hires_CS_Y3` | Net hires per month: Customer Success (COGS), Year 3. | FTE | 1.0 |
| `Cost_FTE_RD` | Fully-loaded annual cost per FTE: R&D. | $ | 210000 |
| `Cost_FTE_SM` | Fully-loaded annual cost per FTE: Sales & Marketing. | $ | 190000 |
| `Cost_FTE_GA` | Fully-loaded annual cost per FTE: G&A. | $ | 170000 |
| `Cost_FTE_CS` | Fully-loaded annual cost per FTE: Customer Success (COGS). | $ | 120000 |
| `Comp_Inflation` | Annual compensation inflation. | % | 0.04 |

## Operating expenses (non-headcount)

| Name | Meaning | Unit | Default |
|---|---|---|---|
| `Commission_Rate` | Sales commission (% of new + expansion ARR). | % | 0.1 |
| `Marketing_Programs_Monthly` | Marketing programs per month (Year 1). | $ | 200000 |
| `Marketing_Growth_Annual` | Annual growth in marketing programs. | % | 0.25 |
| `Software_Per_FTE_Monthly` | Software & tools per FTE per month. | $ | 400 |
| `Rent_Per_FTE_Monthly` | Rent & facilities per FTE per month. | $ | 900 |
| `Other_GA_Monthly` | Other G&A per month (legal, audit, insurance). | $ | 120000 |
| `Opex_Inflation` | Annual inflation on other G&A. | % | 0.03 |

## Capex & depreciation

| Name | Meaning | Unit | Default |
|---|---|---|---|
| `Capex_Per_New_Hire` | Capex per new hire (laptop, equipment). | $ | 3500 |
| `Capex_Maintenance_Monthly` | Maintenance capex per month. | $ | 15000 |
| `Depr_Life_Months` | Depreciation life (on net book value). | months | 36 |

## Financing, interest & tax

| Name | Meaning | Unit | Default |
|---|---|---|---|
| `Equity_Raise_Month` | Equity raise: model month #. | month # | 18 |
| `Equity_Raise_Amount` | Equity raise: amount. | $ | 40000000 |
| `Debt_Draw_Month` | Venture debt draw: model month #. | month # | 9 |
| `Debt_Draw_Amount` | Venture debt draw: amount. | $ | 5000000 |
| `Debt_Rate` | Venture debt interest rate (interest-only). | % | 0.11 |
| `Interest_Income_Rate` | Interest income on cash. | % | 0.035 |
| `Tax_Rate` | Cash tax rate (on positive pre-tax income). | % | 0.21 |

## Flaggable calculation rows

Target a calculation flag with `{"sheet": ..., "row": ..., "month": 1-36}`.

- **Revenue**: `cust_bop`, `cust_new`, `cust_churn`, `cust_eop`, `acv`, `arr_bop`, `arr_new`, `arr_exp`, `arr_churn`, `arr_eop`, `arr_netnew`, `arpa`, `rev_sub`, `rev_svc`, `rev_total`, `dr_eop`, `billings`
- **Headcount & Opex**: `hires_RD`, `hires_SM`, `hires_GA`, `hires_CS`, `hires_total`, `hc_RD`, `hc_SM`, `hc_GA`, `hc_CS`, `hc_total`, `cost_RD`, `cost_SM`, `cost_GA`, `cost_CS`, `pay_RD`, `pay_SM`, `pay_GA`, `pay_CS`, `pay_total`, `commissions`, `hosting`, `svc_cost`, `marketing`, `software`, `rent`, `other_ga`, `nonhc_total`, `alloc_cogs`, `alloc_rd`, `alloc_sm`, `alloc_ga`, `alloc_total`
- **P&L**: `sub`, `svc`, `rev`, `cogs_host`, `cogs_cs`, `cogs_svc`, `cogs`, `gp`, `gm`, `rd`, `sm`, `ga`, `opex`, `ebitda`, `ebitda_m`, `da`, `ebit`, `int_inc`, `int_exp`, `ebt`, `tax`, `ni`
- **Balance Sheet**: `cash`, `ar`, `prepaid`, `ppe`, `ta`, `ap`, `accrued`, `dr`, `debt`, `tl`, `pic`, `re`, `te`, `tle`, `check`, `ppe_bop`, `capex`, `ppe_da`, `ppe_eop`, `debt_bop`, `debt_draw`, `debt_eop`
- **Cash Flow**: `ni`, `da`, `d_ar`, `d_prepaid`, `d_ap`, `d_accrued`, `d_dr`, `cfo`, `capex`, `cfi`, `equity`, `debt`, `cff`, `net`, `cash_bop`, `cash_eop`
- **KPIs**: `arr`, `netnew`, `growth`, `nrr`, `grr`, `burn`, `burn_mult`, `runway`, `cac_payback`, `rule40`, `gm`, `hc`, `arr_fte`

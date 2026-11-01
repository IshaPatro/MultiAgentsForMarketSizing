# Confidence rubric

Confidence measures how likely the input is right to within ~10%, given the evidence.
A calculation's confidence is the minimum of its driving inputs (the model computes this
on Checks & Flags). The default flag threshold is `Conf_Threshold` = 70%.

| Confidence | When | Typical source |
|---|---|---|
| 0.90–1.00 | Read straight from a reconciled system of record and tied out (TB balances, cap table, bank statement) | Data-derived, User |
| 0.75–0.89 | Derived from clean company data with ≥ 6 months of history or one clear document | Data-derived |
| 0.60–0.74 | Derived from short history (< 6 months), needed cleaning judgement, or is a management plan | Data-derived, User |
| 0.50–0.59 | No company data; industry benchmark or agent default | Benchmark, Agent default |
| < 0.50 | Sources conflict, or the value is a guess to keep the model running | any; always flag |

Lower the score by 0.05–0.15 for each of: unresolved tie-out gap, heavy de-duplication,
unit ambiguity, stale data (> 3 months before the model start), management "stretch" language.

## Flags

Raise one flag per item below threshold, plus a calculation flag whenever missing data forces
a modelling simplification (e.g. deferred revenue estimated without a billing schedule).
Each flag: `item`, `confidence`, `reason` (what is missing or uncertain, with the file),
`action` (the specific document or question that would resolve it).

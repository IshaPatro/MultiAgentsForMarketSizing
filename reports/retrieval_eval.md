# Retrieval evaluation

74 questions · hit@4 **97%** · MRR **0.83**

| Question kind | n | hit@4 | MRR |
|---|---:|---:|---:|
| assumption | 2 | 100% | 1.00 |
| exhibit | 9 | 89% | 0.67 |
| fact | 12 | 100% | 0.77 |
| number | 16 | 100% | 0.86 |
| public | 8 | 88% | 0.81 |
| scenario_current | 17 | 100% | 0.96 |
| scenario_history | 8 | 100% | 0.66 |
| source | 2 | 100% | 1.00 |

## Misses (2)

- Which hedge funds could buy wastewater data and what would they use it for? - expected `biobot_ex5_hedge_funds`; rank 7; got `biobot_public_current_product_catalog_11`, `biobot_case_p10_c5`, `biobot_case_p4_c3`, `biobot_fact_envisioned_customers`
- Which companies compete with Biobot in national wastewater surveillance today? - expected `biobot_public_competitors_alternatives_and_01`, `biobot_public_competitors_alternatives_and_02`, `biobot_public_competitors_alternatives_and_03`, `biobot_public_competitors_alternatives_and_04`, `biobot_public_competitors_alternatives_and_05`, `biobot_public_competitors_alternatives_and_06`; rank 6; got `biobot_public_competitors_alternatives_and_07`, `biobot_public_history_and_operating_07`, `biobot_public_business_and_market_04`, `biobot_public_contracts_customers_and_01`

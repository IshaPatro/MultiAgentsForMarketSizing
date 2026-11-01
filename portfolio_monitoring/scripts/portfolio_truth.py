"""Single source of truth for the Q3 2026 sample portfolio (all companies fictional).

Raw inputs and the expected result are both rendered from these numbers, so they always agree.
All money in USD under the fund's definitions unless a field says otherwise.
"""
from datetime import date

PERIOD_END = date(2026, 9, 30)
PRIOR_END = date(2026, 6, 30)
REPORT_DUE = date(2026, 10, 20)
FUND = "Blue Cloud Ventures (sample data)"

FX = {"EURUSD_2026-06-30": 1.10, "EURUSD_2026-09-30": 1.12, "EURUSD_Q3_2026_avg": 1.115}

# Fund thresholds used for RAG status (also written as named ranges in the result workbook)
POLICY = {
    "Runway_Red": (12, "Runway below this many months is Red"),
    "Runway_Amber": (18, "Runway below this many months is Amber"),
    "Plan_Red": (-0.10, "ARR this far below plan is Red"),
    "Plan_Amber": (-0.05, "ARR this far below plan is Amber"),
    "BM_Red": (4.0, "Burn multiple above this is Red (or net new ARR ≤ 0)"),
    "BM_Amber": (2.5, "Burn multiple above this is Amber"),
    "NRR_Amber": (1.00, "NRR below this is Amber"),
    "Conf_Amber": (0.70, "Any key metric below this confidence is Amber"),
}

COMPANIES = [
    dict(key="lumenpath", name="Lumenpath AI", sector="AI", stage="Series A", board=True, own=0.112,
         cost=6_000_000, post=48_000_000, round_date="2025-05", cadence="Monthly", fmt="PDF investor update",
         plan_arr=4_800_000, plan_burn=900_000, lead="Partner A",
         file="lumenpath_ai_investor_update_2026-09.pdf"),
    dict(key="corvane", name="Corvane Security", sector="Cybersecurity", stage="Series B", board=True, own=0.090,
         cost=12_000_000, post=210_000_000, round_date="2025-02", cadence="Monthly", fmt="Email update",
         plan_arr=20_500_000, plan_burn=1_050_000, lead="Partner B",
         file="corvane_security_update_2026-09.eml"),
    dict(key="tessellate", name="Tessellate Data", sector="Infrastructure", stage="Series A", board=True,
         own=0.125, cost=7_500_000, post=60_000_000, round_date="2024-10", cadence="Monthly",
         fmt="Excel KPI pack", plan_arr=9_400_000, plan_burn=1_200_000, lead="Principal C",
         file="tessellate_kpi_pack_2026-09.xlsx"),
    dict(key="fieldnote", name="Fieldnote Health", sector="Healthcare", stage="Series B", board=False, own=0.075,
         cost=10_000_000, post=260_000_000, round_date="2025-11", cadence="Quarterly", fmt="Board deck (PPTX)",
         plan_arr=23_500_000, plan_burn=1_000_000, lead="Partner A",
         file="fieldnote_health_q3_2026_board_deck.pptx"),
    dict(key="quillstack", name="Quillstack", sector="Business Application", stage="Seed", board=True, own=0.140,
         cost=3_500_000, post=25_000_000, round_date="2025-08", cadence="Monthly", fmt="Metrics tool CSV export",
         plan_arr=2_300_000, plan_burn=380_000, lead="Principal C",
         file="quillstack_metrics_export_2026-09.csv"),
    dict(key="brightwire", name="Brightwire Ops", sector="Open Source", stage="Series A", board=False, own=0.100,
         cost=6_000_000, post=70_000_000, round_date="2025-06", cadence="Quarterly", fmt="Investor letter (DOCX)",
         plan_arr=7_500_000, plan_burn=800_000, lead="Partner B",
         file="brightwire_ops_q3_2026_investor_letter.docx"),
    dict(key="halyard", name="Halyard Robotics", sector="AI", stage="Series A", board=True, own=0.080,
         cost=4_000_000, post=45_000_000, round_date="2025-01", cadence="Quarterly", fmt="Not received",
         plan_arr=3_800_000, plan_burn=650_000, lead="Partner B", file=None),
]

# Fund-standard Q2 2026 values (last quarter's monitoring output)
PRIOR = {
    "lumenpath": dict(arr=4_100_000, cash=17_150_000, burn=780_000, status="Amber"),
    "corvane": dict(arr=18_400_000, cash=22_100_000, burn=1_050_000, status="Green"),
    "tessellate": dict(arr=7_900_000, cash=14_150_000, burn=1_350_000, status="Amber"),
    "fieldnote": dict(arr=21_000_000, cash=33_850_000, burn=1_050_000, status="Green"),
    "quillstack": dict(arr=2_052_000, cash=5_130_000, burn=390_000, status="Amber"),
    "brightwire": dict(arr=6_930_000, cash=14_850_000, burn=800_000, status="Green"),
    "halyard": dict(arr=3_200_000, cash=6_000_000, burn=600_000, status="Red"),
}

# Monthly detail behind the monthly reporters
LUMEN_MONTHS = dict(arr=[4_380_000, 4_710_000, 5_020_000], gm=[0.61, 0.62, 0.63], burn=[820_000, 860_000, 870_000],
                    cash=[16_330_000, 15_470_000, 14_600_000], hc=[38, 40, 41], customers=[55, 59, 63])
TESS_MONTHS = dict(  # $000s, Jan..Sep 2026; August ARR restated from 8,310
    arr=[7_310, 7_420, 7_540, 7_660, 7_780, 7_900, 7_950, 8_020, 8_150],
    plan=[7_400, 7_600, 7_800, 8_000, 8_200, 8_400, 8_700, 9_050, 9_400],
    rev=[600, 610, 620, 630, 640, 650, 660, 670, 690],
    burn=[1_180, 1_210, 1_250, 1_290, 1_320, 1_350, 1_400, 1_450, 1_500],
    hc=[58, 59, 61, 62, 63, 64, 66, 67, 68], customers=[33, 34, 35, 36, 37, 38, 39, 40, 41])
QUILL_MONTHS = dict(  # Apr..Sep 2026
    mrr=[152_000, 161_500, 171_000, 182_500, 191_000, 203_400], burn=[370_000, 380_000, 390_000, 400_000, 410_000,
                                                                       420_000],
    customers=[352, 366, 378, 389, 398, 410], hc=[19, 20, 20, 21, 22, 22])
QUILL_CASH_JUN = 5_130_000

# Q3 2026 values under fund definitions (what a correct agent should extract)
Q3 = {
    "lumenpath": dict(arr=5_020_000, revenue=sum(a / 12 for a in LUMEN_MONTHS["arr"]), gm=None,
                      burn=sum(LUMEN_MONTHS["burn"]) / 3, cash=14_600_000, headcount=41, customers=63, nrr=1.28),
    "corvane": dict(arr=19_600_000, revenue=4_830_000, gm=0.79, burn=1_100_000, cash=21_300_000, headcount=112,
                    customers=240, nrr=1.12),
    "tessellate": dict(arr=8_150_000, revenue=2_020_000, gm=0.71, burn=1_450_000, cash=9_800_000, headcount=68,
                       customers=41, nrr=1.01),
    "fieldnote": dict(arr=22_800_000, revenue=5_550_000, gm=0.66, burn=950_000, cash=31_000_000, headcount=150,
                      customers=38, nrr=None),
    "quillstack": dict(arr=203_400 * 12, revenue=sum(QUILL_MONTHS["mrr"][3:]), gm=0.84,
                       burn=sum(QUILL_MONTHS["burn"][3:]) / 3, cash=3_900_000, headcount=22, customers=410,
                       nrr=1.04),
    "brightwire": dict(arr=6_900_000 * FX["EURUSD_2026-09-30"], revenue=1_620_000 * FX["EURUSD_Q3_2026_avg"],
                       gm=0.77, burn=(13_500_000 - 11_200_000) / 3 * FX["EURUSD_Q3_2026_avg"],
                       cash=11_200_000 * FX["EURUSD_2026-09-30"], headcount=47, customers=95, nrr=1.18),
}
_lr = [a / 12 for a in LUMEN_MONTHS["arr"]]
Q3["lumenpath"]["gm"] = sum(r * g for r, g in zip(_lr, LUMEN_MONTHS["gm"])) / sum(_lr)

# ---- Late arrival: Halyard's Q3 report lands after the workbook is built (update test) ----
LATE_RECEIVED = date(2026, 10, 27)
HALYARD_FILE = "halyard_robotics_q3_2026_portal_export.json"
HALYARD_SAFE = 2_000_000  # SAFE closed 2026-08-14; financing, so excluded from burn
HALYARD_CASH_Q2, HALYARD_CASH_Q3 = 6_000_000, 6_140_000
Q3_LATE = {**Q3, "halyard": dict(arr=3_550_000, revenue=850_000, gm=0.58,
                                 burn=(HALYARD_CASH_Q2 - HALYARD_CASH_Q3 + HALYARD_SAFE) / 3, cash=HALYARD_CASH_Q3,
                                 headcount=34, customers=21, nrr=1.06)}

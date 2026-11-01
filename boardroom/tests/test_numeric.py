from debate.numeric import check_arithmetic


def test_explicit_arithmetic_is_recomputed_with_units():
    assert check_arithmetic("10% of $100 million is $10 million") is True
    assert check_arithmetic("10% of $100 million is $20 million") is False
    assert check_arithmetic("$5 million × 2 = $10 million") is True
    assert check_arithmetic("$5 million × 2 = $11 million") is False


def test_non_equations_are_left_to_the_fact_checker():
    assert check_arithmetic("The city has 50,427 manholes") is None
    assert check_arithmetic("We could reach 100 cities by 2020") is None


def test_units_need_a_word_boundary_so_ordinary_units_are_not_scaled():
    assert check_arithmetic("2 x 3 = 6 months") is True      # was parsed as 6 million -> false "miscalculated"
    assert check_arithmetic("50 x 2 = 100 km") is True
    assert check_arithmetic("$5M x 2 = $10M") is True
    assert check_arithmetic("$5 millions x 2 = $10 millions") is True
    assert check_arithmetic("$5 x 2 = $10 billion") is False


# Real lines from the final live runs that the first checker wrongly flagged as miscalculations.
import pytest  # noqa: E402

from debate.numeric import check_calculation  # noqa: E402


@pytest.mark.parametrize("text", [
    "21,264 samples = 886 sites x 24 samples",                                  # result written first
    "28 x $36,000 + 41 x $96,000 = $1,008,000 + $3,936,000 = $4,944,000",        # chained equalities
    "28 x $36,000 + 41 x $96,000 = $4,944,000.",                                # was matched as "41 x $96,000 = ..."
    "57.4% - 53.9% = 3.5 points",                                               # percentage points
    "$100,000 / $10,200,000 = 1.0 point of churn",
    "$14,384,000 / $6,350,000 = about 2.3x revenue growth",                     # rounded to the precision written
    "$2,300,000 - $9,300,000 = -$7,000,000",                                    # negative result
    "163 - 242 = -79 means signing fits",
])
def test_correct_maths_from_the_live_runs_is_not_flagged(text):
    assert check_calculation(text) is True


@pytest.mark.parametrize("text", [
    "$6,104,670 burn per year = $508,723 per month",                            # a unit conversion, not arithmetic
    "$29.17 so variable floor = $269",
])
def test_two_bare_numbers_are_not_treated_as_arithmetic(text):
    assert check_calculation(text) is None


@pytest.mark.parametrize("text", [
    "4 cities x $110B x 20% = $22B",
    "28 x $36,000 + 41 x $96,000 = $5,944,000",
    "21,264 samples = 886 sites x 25 samples",
    "57.4% - 53.9% = 4.5 points",
    "$14,384,000 / $6,350,000 = 2.9x",
    "28 x $36,000 + 41 x $96,000 = $1,008,000 + $3,936,000 = $4,000,000",
])
def test_real_mistakes_are_still_caught(text):
    assert check_calculation(text) is False

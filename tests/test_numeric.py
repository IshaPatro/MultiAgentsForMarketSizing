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

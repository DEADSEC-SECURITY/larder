from larder.domain.names import standardize_name


def test_standardize_name_title_cases_and_compacts_words():
    assert standardize_name("  HALF   & half ICED tea  ") == "Half & Half Iced Tea"
    assert standardize_name("wEiRd in-BETWEEN/name") == "Weird In-Between/Name"


def test_standardize_name_keeps_apostrophe_contractions_natural():
    assert standardize_name("BEN & JERRY'S") == "Ben & Jerry's"
    assert standardize_name("O'NEILL COFFEE") == "O'Neill Coffee"

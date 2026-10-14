from src.clean import clean_line, line_is_grounded


SOURCE = "Eight US states sue OpenAI, claiming ChatGPT harms children."


def test_clean_line_replaces_em_dashes():
    assert "\u2014" not in clean_line("A launch \u2014 then a price change")
    assert "\u2013" not in clean_line("A launch \u2013 then a price change")
    assert clean_line("A launch \u2014 then a price change").endswith(".")


def test_fact_line_must_overlap_the_source():
    assert line_is_grounded(
        "Eight US states sue OpenAI over ChatGPT.",
        SOURCE,
        analysis=False,
    )
    assert not line_is_grounded(
        "Anthropic promised a 40 point jump on a secret benchmark.",
        SOURCE,
        analysis=False,
    )


def test_analysis_line_cannot_add_numbers_quotes_or_names():
    assert line_is_grounded(
        "A lawsuit like this changes the constraints on shipping a consumer assistant.",
        SOURCE,
        analysis=True,
    )
    assert not line_is_grounded(
        "The filing says \"models must be banned\" and cites a 40 percent harm rate.",
        SOURCE,
        analysis=True,
    )
    assert not line_is_grounded(
        "Anthropic will have to answer for the same conduct.",
        SOURCE,
        analysis=True,
    )

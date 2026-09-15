"""_text_as_minutes must render a summary section's bullet points as minutes
whatever JSON shape it comes back in — a plain string, a list of points, or a
headed mapping — and format_minutes must split inline headings onto their
own paragraph."""

from notetaker.summarizer import _text_as_minutes, format_minutes


def test_string_is_kept_as_is():
    assert _text_as_minutes("Discussion:\n- one") == "Discussion:\n- one"


def test_list_becomes_bullet_lines_not_a_python_repr():
    text = _text_as_minutes(["Harness matters.", "Model is the easy part."])

    assert text == "- Harness matters.\n- Model is the easy part."
    assert "['" not in text


def test_list_items_that_already_carry_a_bullet_are_not_double_bulleted():
    text = _text_as_minutes(["- Harness matters.", "• Model is easy.", "* Ship it."])

    assert text == "- Harness matters.\n- Model is easy.\n- Ship it."


def test_mapping_becomes_headed_bullets():
    text = _text_as_minutes({"Discussion": ["a", "b"], "Decisions": [], "Open questions": ["c?"]})

    assert text == "Discussion:\n  - a\n  - b\n\nDecisions:\n\nOpen questions:\n  - c?"


def test_list_of_mappings_is_flattened():
    text = _text_as_minutes([{"Discussion": ["a"]}, {"Decisions": ["b"]}])

    assert text == "Discussion:\n  - a\n\nDecisions:\n  - b"


def test_inline_headings_are_split_into_paragraphs():
    text = "Discussion: Harness matters a lot. Decisions: Build one. Open questions: How to integrate?"

    assert format_minutes(text) == (
        "Discussion: Harness matters a lot.\n\nDecisions: Build one.\n\nOpen questions: How to integrate?"
    )


def test_headings_already_on_their_own_lines_are_left_alone():
    text = "Discussion:\n- a\n\nDecisions:\n- b"

    assert format_minutes(text) == text


def test_none_becomes_empty_string():
    assert _text_as_minutes(None) == ""


def test_number_becomes_a_single_bullet():
    assert _text_as_minutes(42) == "- 42"

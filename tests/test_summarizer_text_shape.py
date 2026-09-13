"""The model's "text" field must render as minutes whatever JSON shape it
comes back in — a plain string, a list of points, or a headed mapping."""

from notetaker.summarizer import _summary_from_data


def test_text_string_is_kept_as_is():
    assert _summary_from_data({"text": "Discussion:\n- one"}).text == "Discussion:\n- one"


def test_text_list_becomes_bullet_lines_not_a_python_repr():
    summary = _summary_from_data({"text": ["Harness matters.", "Model is the easy part."]})

    assert summary.text == "- Harness matters.\n- Model is the easy part."
    assert "['" not in summary.text


def test_text_list_items_that_already_carry_a_bullet_are_not_double_bulleted():
    summary = _summary_from_data({"text": ["- Harness matters.", "• Model is easy.", "* Ship it."]})

    assert summary.text == "- Harness matters.\n- Model is easy.\n- Ship it."


def test_text_mapping_becomes_headed_bullets():
    summary = _summary_from_data(
        {"text": {"Discussion": ["a", "b"], "Decisions": [], "Open questions": ["c?"]}}
    )

    assert summary.text == "Discussion:\n  - a\n  - b\n\nDecisions:\n\nOpen questions:\n  - c?"


def test_text_list_of_mappings_is_flattened():
    summary = _summary_from_data({"text": [{"Discussion": ["a"]}, {"Decisions": ["b"]}]})

    assert summary.text == "Discussion:\n  - a\n\nDecisions:\n  - b"


def test_inline_headings_are_split_into_paragraphs():
    text = "Discussion: Harness matters a lot. Decisions: Build one. Open questions: How to integrate?"

    assert _summary_from_data({"text": text}).text == (
        "Discussion: Harness matters a lot.\n\nDecisions: Build one.\n\nOpen questions: How to integrate?"
    )


def test_headings_already_on_their_own_lines_are_left_alone():
    text = "Discussion:\n- a\n\nDecisions:\n- b"

    assert _summary_from_data({"text": text}).text == text


def test_text_none_becomes_empty_string():
    assert _summary_from_data({"text": None}).text == ""


def test_text_number_becomes_a_single_bullet():
    assert _summary_from_data({"text": 42}).text == "- 42"

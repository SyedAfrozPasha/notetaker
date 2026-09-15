"""A small model that returns the transcript as the "summary" must not have
that presented as minutes."""

from notetaker.summarizer import ECHO_FALLBACK_TEXT, Summary, clean_summary_text, summarize_transcript

TRANSCRIPT = (
    "[00:00:04] Others: The most important thing in AI right now has nothing to do with picking models.\n"
    "[00:00:10] Me: First, what harness even is and why a model by itself is handicapped without one.\n"
    "[00:00:20] Others: Second, we peel the onion on a product you probably use every day.\n"
    "[00:00:25] Me: everywhere.\n"
)


def test_clean_summary_text_keeps_a_genuine_summary_untouched():
    text = "Discussion:\n- Harness engineering matters more than model choice.\n\nDecisions:\n- None."
    assert clean_summary_text(text, TRANSCRIPT) == text


def test_clean_summary_text_strips_speaker_labels_from_original_sentences():
    text = "Me: I will send the deck tomorrow, summarised.\nOthers: Team agreed the plan is fine overall."
    assert clean_summary_text(text, TRANSCRIPT) == (
        "I will send the deck tomorrow, summarised.\nTeam agreed the plan is fine overall."
    )


def test_clean_summary_text_replaces_an_echoed_transcript_with_a_notice():
    echoed = (
        "The most important thing in AI right now has nothing to do with picking models.\n\n"
        "Me: First, what harness even is and why a model by itself is handicapped without one.\n\n"
        "Others: Second, we peel the onion on a product you probably use every day.\n\n"
        "Me: everywhere.\n"
    )
    assert clean_summary_text(echoed, TRANSCRIPT) == ECHO_FALLBACK_TEXT


def test_clean_summary_text_drops_copied_lines_but_keeps_the_rest_when_mostly_original():
    mixed = (
        "Discussion:\n"
        "- The talk argues harness design beats model choice for shipping AI products.\n"
        "- The speaker plans to dissect a desktop app's harness as a worked example.\n"
        "Others: Second, we peel the onion on a product you probably use every day.\n"
        "Decisions:\n- None recorded."
    )
    cleaned = clean_summary_text(mixed, TRANSCRIPT)
    assert "peel the onion" not in cleaned
    assert "harness design beats model choice" in cleaned
    assert cleaned.startswith("Discussion:")


def test_clean_summary_text_ignores_short_lines_when_counting_copies():
    # "everywhere." is in the transcript but too short to prove copying.
    assert clean_summary_text("everywhere.", TRANSCRIPT) == "everywhere."


def test_clean_summary_text_returns_notice_for_empty_text():
    assert clean_summary_text("   \n", TRANSCRIPT) == ECHO_FALLBACK_TEXT


class _EchoProvider:
    chunk_token_limit = 12000

    def summarize(self, transcript: str) -> Summary:
        return Summary(text=transcript, action_items=["Me: practice harness engineering"], tags=["ai"])


def test_summarize_transcript_applies_the_echo_guard_to_the_provider_result():
    summary = summarize_transcript(TRANSCRIPT, _EchoProvider())

    assert summary.text == ECHO_FALLBACK_TEXT
    assert summary.action_items == ["Me: practice harness engineering"]
    assert summary.tags == ["ai"]

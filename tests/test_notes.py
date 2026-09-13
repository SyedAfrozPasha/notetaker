from datetime import date, datetime

from notetaker.notes import (
    find_note_path,
    list_notes,
    note_id_for,
    parse_note_meta,
    read_note_body,
    rewrite_note_summary,
    slugify,
    write_note,
)
from notetaker.summarizer import Summary


def test_slugify_lowercases_and_dashes():
    assert slugify("Team Standup!!") == "team-standup"


def test_slugify_empty_title_falls_back():
    assert slugify("...") == "untitled"


def test_note_id_for_no_collision(tmp_path):
    note_id = note_id_for("Standup", datetime(2026, 9, 11, 10, 0), tmp_path)
    assert note_id == "2026-09-11-standup"


def test_note_id_for_collision_appends_time(tmp_path):
    (tmp_path / "2026-09-11-standup.md").write_text("existing")
    note_id = note_id_for("Standup", datetime(2026, 9, 11, 14, 30), tmp_path)
    assert note_id == "2026-09-11-standup-1430"


def test_write_note_and_parse_round_trip(tmp_path):
    summary = Summary(text="We discussed X.", action_items=["Follow up with Bob"], tags=["project-x", "planning"])
    path = write_note(
        tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 18, summary,
        ["[00:00:03] hello", "[00:00:07] world"],
    )
    assert path.exists()

    meta = parse_note_meta(path)
    assert meta.title == "Standup"
    assert meta.duration_minutes == 18
    assert meta.tags == ["project-x", "planning"]

    body = read_note_body(path)
    assert "We discussed X." in body
    assert "Follow up with Bob" in body
    assert "[00:00:03] hello" in body


def test_list_notes_sorted_newest_first(tmp_path):
    summary = Summary(text="s", action_items=[], tags=[])
    write_note(tmp_path, "Old", datetime(2026, 9, 1, 9, 0), 5, summary, [])
    write_note(tmp_path, "New", datetime(2026, 9, 11, 9, 0), 5, summary, [])
    notes = list_notes(tmp_path)
    assert [n.title for n in notes] == ["New", "Old"]


def test_list_notes_empty_dir_returns_empty_list(tmp_path):
    assert list_notes(tmp_path / "does-not-exist") == []


def test_list_notes_skips_unparseable_md_file(tmp_path):
    summary = Summary(text="s", action_items=[], tags=[])
    write_note(tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5, summary, [])
    (tmp_path / "README.md").write_text("# Just a plain markdown file\nNo frontmatter here.\n")

    notes = list_notes(tmp_path)

    assert len(notes) == 1
    assert notes[0].title == "Standup"


def test_find_note_path(tmp_path):
    summary = Summary(text="s", action_items=[], tags=[])
    write_note(tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5, summary, [])
    assert find_note_path(tmp_path, "2026-09-11-standup") is not None
    assert find_note_path(tmp_path, "nonexistent") is None


def test_rewrite_note_summary_replaces_summary_keeping_title_and_date(tmp_path):
    notes_dir = tmp_path / "notes"
    path = write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5,
        Summary("old summary", ["old item"], ["old-tag"]), ["[00:00:01] hello"],
    )

    rewrite_note_summary(path, Summary("new summary", ["new item"], ["new-tag"]), ["[00:00:01] hello"])

    text = path.read_text()
    assert "new summary" in text
    assert "new item" in text
    assert "new-tag" in text
    assert "old summary" not in text
    assert "title: Standup" in text
    assert "duration_minutes: 5" in text


def test_rewrite_note_summary_does_not_change_the_note_id(tmp_path):
    notes_dir = tmp_path / "notes"
    path = write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5,
        Summary("old summary", [], []), ["[00:00:01] hello"],
    )
    original_path = path

    rewrite_note_summary(path, Summary("new summary", [], []), ["[00:00:01] hello"])

    assert path == original_path
    assert len(list(notes_dir.glob("*.md"))) == 1


def test_parse_action_items_splits_checkbox_lines():
    from notetaker.notes import parse_action_items

    assert parse_action_items("- [ ] Follow up with Bob\n- [ ] Send notes") == [
        "Follow up with Bob",
        "Send notes",
    ]


def test_parse_action_items_none_sentinel_is_empty_list():
    from notetaker.notes import parse_action_items

    assert parse_action_items("- (none)") == []


def test_update_note_fields_changes_only_given_fields(tmp_path):
    from notetaker.notes import update_note_fields

    path = write_note(
        tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 18,
        Summary(text="old summary", action_items=["old item"], tags=["old-tag"]),
        ["[00:00:03] hello"],
    )

    update_note_fields(path, title="Renamed Standup", tags=["new-tag"])

    meta = parse_note_meta(path)
    assert meta.title == "Renamed Standup"
    assert meta.tags == ["new-tag"]
    body = read_note_body(path)
    assert "old summary" in body  # summary_text untouched
    assert "old item" in body  # action_items untouched
    assert "[00:00:03] hello" in body  # transcript untouched
    assert meta.duration_minutes == 18  # unrelated frontmatter untouched


def test_update_note_fields_changes_summary_and_action_items(tmp_path):
    from notetaker.notes import update_note_fields

    path = write_note(
        tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 18,
        Summary(text="old summary", action_items=["old item"], tags=["old-tag"]),
        ["[00:00:03] hello"],
    )

    update_note_fields(path, summary_text="new summary", action_items=["new item one", "new item two"])

    body = read_note_body(path)
    assert "new summary" in body
    assert "old summary" not in body
    assert "new item one" in body
    assert "new item two" in body
    assert "old item" not in body
    meta = parse_note_meta(path)
    assert meta.title == "Standup"  # untouched
    assert meta.tags == ["old-tag"]  # untouched


def test_update_note_fields_does_not_rename_the_note(tmp_path):
    from notetaker.notes import update_note_fields

    path = write_note(tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), ["hi"])
    original_path = path

    update_note_fields(path, title="A Totally Different Title")

    assert path == original_path
    assert len(list(tmp_path.glob("*.md"))) == 1


def test_update_note_fields_clearing_action_items_writes_none_sentinel(tmp_path):
    from notetaker.notes import update_note_fields

    path = write_note(
        tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5,
        Summary("s", ["item to remove"], []), ["hi"],
    )

    update_note_fields(path, action_items=[])

    body = read_note_body(path)
    assert "item to remove" not in body
    assert "(none)" in body


def test_parse_note_body_splits_all_three_sections(tmp_path):
    from notetaker.notes import parse_note_body

    path = write_note(
        tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5,
        Summary(text="We discussed X.", action_items=["Follow up"], tags=[]),
        ["[00:00:03] hello"],
    )

    summary_text, action_items, transcript = parse_note_body(path)

    assert summary_text == "We discussed X."
    assert action_items == ["Follow up"]
    assert transcript == "[00:00:03] hello"


def test_parse_note_body_empty_transcript_is_empty_string(tmp_path):
    from notetaker.notes import parse_note_body

    path = write_note(tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), [])

    _, _, transcript = parse_note_body(path)

    assert transcript == ""


def test_search_notes_filters_by_query_in_title(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Team Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), [])
    write_note(tmp_path, "1:1 with Bob", datetime(2026, 9, 12, 10, 0), 5, Summary("s", [], []), [])

    results = search_notes(tmp_path, query="standup")

    assert [n.title for n in results] == ["Team Standup"]


def test_search_notes_filters_by_query_in_summary(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Meeting A", datetime(2026, 9, 11, 10, 0), 5, Summary("Discussed the roadmap", [], []), [])
    write_note(tmp_path, "Meeting B", datetime(2026, 9, 12, 10, 0), 5, Summary("Discussed hiring", [], []), [])

    results = search_notes(tmp_path, query="roadmap")

    assert [n.title for n in results] == ["Meeting A"]


def test_search_notes_filters_by_tag(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Meeting A", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["project-x"]), [])
    write_note(tmp_path, "Meeting B", datetime(2026, 9, 12, 10, 0), 5, Summary("s", [], ["project-y"]), [])

    results = search_notes(tmp_path, tag="project-x")

    assert [n.title for n in results] == ["Meeting A"]


def test_search_notes_filters_by_date_range(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Too Early", datetime(2026, 9, 1, 10, 0), 5, Summary("s", [], []), [])
    write_note(tmp_path, "In Range", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), [])
    write_note(tmp_path, "Too Late", datetime(2026, 9, 30, 10, 0), 5, Summary("s", [], []), [])

    results = search_notes(tmp_path, start_date=date(2026, 9, 10), end_date=date(2026, 9, 15))

    assert [n.title for n in results] == ["In Range"]


def test_search_notes_combines_filters_with_and(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Standup A", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["project-x"]), [])
    write_note(tmp_path, "Standup B", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["project-y"]), [])

    results = search_notes(tmp_path, query="standup", tag="project-x")

    assert [n.title for n in results] == ["Standup A"]


def test_search_notes_no_filters_returns_everything_newest_first(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Old", datetime(2026, 9, 1, 9, 0), 5, Summary("s", [], []), [])
    write_note(tmp_path, "New", datetime(2026, 9, 11, 9, 0), 5, Summary("s", [], []), [])

    results = search_notes(tmp_path)

    assert [n.title for n in results] == ["New", "Old"]

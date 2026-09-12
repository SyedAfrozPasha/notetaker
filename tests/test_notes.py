from datetime import datetime

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

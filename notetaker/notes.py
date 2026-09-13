import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

from notetaker.summarizer import Summary


@dataclass
class NoteMeta:
    note_id: str
    title: str
    date: datetime
    duration_minutes: int
    tags: list[str]
    path: Path


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def note_id_for(title: str, start_time: datetime, notes_dir: Path) -> str:
    date_str = start_time.strftime("%Y-%m-%d")
    base_id = f"{date_str}-{slugify(title)}"
    if not (notes_dir / f"{base_id}.md").exists():
        return base_id
    return f"{base_id}-{start_time.strftime('%H%M')}"


def _render(frontmatter: dict, summary_text: str, action_items_md: str, transcript_md: str) -> str:
    return (
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n"
        f"## Summary\n{summary_text}\n\n"
        f"## Action Items\n{action_items_md}\n\n"
        f"## Transcript\n{transcript_md}\n"
    )


def _render_note_body(
    title: str,
    start_time: datetime,
    duration_minutes: int,
    summary: Summary,
    transcript_lines: list[str],
) -> str:
    frontmatter = {
        "title": title,
        "date": start_time.isoformat(),
        "duration_minutes": duration_minutes,
        "tags": summary.tags,
    }
    action_items_md = "\n".join(f"- [ ] {item}" for item in summary.action_items) or "- (none)"
    transcript_md = "\n".join(transcript_lines) or "(no transcript captured)"
    return _render(frontmatter, summary.text, action_items_md, transcript_md)


def write_note(
    notes_dir: Path,
    title: str,
    start_time: datetime,
    duration_minutes: int,
    summary: Summary,
    transcript_lines: list[str],
) -> Path:
    notes_dir.mkdir(parents=True, exist_ok=True)
    note_id = note_id_for(title, start_time, notes_dir)
    path = notes_dir / f"{note_id}.md"
    path.write_text(_render_note_body(title, start_time, duration_minutes, summary, transcript_lines))
    return path


def rewrite_note_summary(path: Path, summary: Summary, transcript_lines: list[str]) -> None:
    """Replaces an existing Note's Summary, Action Items, and tags at the
    same path — keeping its title/date/duration_minutes unchanged — by
    re-rendering the note body. Used by Resummarize.
    """
    meta = parse_note_meta(path)
    path.write_text(_render_note_body(meta.title, meta.date, meta.duration_minutes, summary, transcript_lines))


_CHECKBOX_ITEM_RE = re.compile(r"^- \[[ xX]\] (.*)$")


def parse_action_items(action_items_md: str) -> list[str]:
    """Parses a rendered Action Items Markdown block back into a plain list
    — the inverse of the `"- [ ] {item}"` rendering in `_render`. The
    `"- (none)"` sentinel (written when there are no action items) parses
    back to an empty list.

    Matches both `"- [ ] "` (unchecked, what `_render`/`update_note_fields`
    write) and `"- [x] "`/`"- [X] "` (checked) — a Note is a plain Markdown
    file its author may open and check items off in another editor, and
    this list round-trips straight back through `update_note_fields` on
    the next edit, so a stricter match here would silently delete any item
    the user had checked off outside this app.
    """
    items = []
    for line in action_items_md.strip().splitlines():
        match = _CHECKBOX_ITEM_RE.match(line.strip())
        if match:
            items.append(match.group(1))
    return items


def _split_body(body: str) -> tuple[str, str, str]:
    """Splits a Note's rendered body into (summary_text, action_items_md,
    transcript_md) exactly as `_render` wrote them, so a field-level edit
    can leave the sections it doesn't touch byte-for-byte unchanged.
    """
    summary_part, _, rest = body.partition("## Action Items")
    action_part, _, transcript_part = rest.partition("## Transcript")
    summary_text = summary_part.replace("## Summary", "", 1).strip()
    return summary_text, action_part.strip(), transcript_part.strip()


def update_note_fields(
    path: Path,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    summary_text: str | None = None,
    action_items: list[str] | None = None,
) -> None:
    """Replaces only the given fields of an existing Note, in place, never
    renaming it (see CONTEXT.md's Note ID definition) and never touching
    its Transcript section.
    """
    meta = parse_note_meta(path)
    text = path.read_text()
    _, _, body = text.split("---", 2)
    current_summary_text, current_action_items_md, transcript_md = _split_body(body)

    new_title = title if title is not None else meta.title
    new_tags = tags if tags is not None else meta.tags
    new_summary_text = summary_text if summary_text is not None else current_summary_text
    if action_items is not None:
        new_action_items_md = "\n".join(f"- [ ] {item}" for item in action_items) or "- (none)"
    else:
        new_action_items_md = current_action_items_md

    frontmatter = {
        "title": new_title,
        "date": meta.date.isoformat(),
        "duration_minutes": meta.duration_minutes,
        "tags": new_tags,
    }
    path.write_text(_render(frontmatter, new_summary_text, new_action_items_md, transcript_md))


def parse_note_body(path: Path) -> tuple[str, list[str], str]:
    """Returns (summary_text, action_items, transcript_text) parsed from a
    Note's Markdown body — the structured counterpart to `read_note_body`,
    for callers (e.g. the dashboard's note-detail/edit view) that need the
    three sections separately rather than as one blob.
    """
    body = read_note_body(path)
    summary_text, action_items_md, transcript_md = _split_body(body)
    action_items = parse_action_items(action_items_md)
    transcript_text = "" if transcript_md == "(no transcript captured)" else transcript_md
    return summary_text, action_items, transcript_text


def parse_note_meta(path: Path) -> NoteMeta:
    text = path.read_text()
    _, frontmatter_raw, _ = text.split("---", 2)
    fm = yaml.safe_load(frontmatter_raw)
    return NoteMeta(
        note_id=path.stem,
        title=fm["title"],
        date=datetime.fromisoformat(fm["date"]),
        duration_minutes=fm["duration_minutes"],
        tags=fm.get("tags") or [],
        path=path,
    )


def list_notes(notes_dir: Path) -> list[NoteMeta]:
    if not notes_dir.exists():
        return []
    metas = []
    for p in sorted(notes_dir.glob("*.md")):
        try:
            metas.append(parse_note_meta(p))
        except (ValueError, KeyError):
            continue
    return sorted(metas, key=lambda m: m.date, reverse=True)


def search_notes(
    notes_dir: Path,
    *,
    query: str | None = None,
    tag: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[NoteMeta]:
    metas = list_notes(notes_dir)
    if tag is not None:
        metas = [m for m in metas if tag in m.tags]
    if start_date is not None:
        metas = [m for m in metas if m.date.date() >= start_date]
    if end_date is not None:
        metas = [m for m in metas if m.date.date() <= end_date]
    if query is not None:
        needle = query.lower()
        matched = []
        for m in metas:
            if needle in m.title.lower():
                matched.append(m)
                continue
            summary_text, _, _ = _split_body(read_note_body(m.path))
            if needle in summary_text.lower():
                matched.append(m)
        metas = matched
    return metas


def read_note_body(path: Path) -> str:
    text = path.read_text()
    _, _, body = text.split("---", 2)
    return body.strip()


def find_note_path(notes_dir: Path, note_id: str) -> Path | None:
    candidate = notes_dir / f"{note_id}.md"
    if candidate.resolve().parent != notes_dir.resolve():
        return None
    return candidate if candidate.exists() else None

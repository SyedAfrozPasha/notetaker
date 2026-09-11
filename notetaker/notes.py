import re
from dataclasses import dataclass
from datetime import datetime
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
    frontmatter = {
        "title": title,
        "date": start_time.isoformat(),
        "duration_minutes": duration_minutes,
        "tags": summary.tags,
    }
    action_items_md = "\n".join(f"- [ ] {item}" for item in summary.action_items) or "- (none)"
    transcript_md = "\n".join(transcript_lines) or "(no transcript captured)"
    body = (
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n"
        f"## Summary\n{summary.text}\n\n"
        f"## Action Items\n{action_items_md}\n\n"
        f"## Transcript\n{transcript_md}\n"
    )
    path = notes_dir / f"{note_id}.md"
    path.write_text(body)
    return path


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


def read_note_body(path: Path) -> str:
    text = path.read_text()
    _, _, body = text.split("---", 2)
    return body.strip()


def find_note_path(notes_dir: Path, note_id: str) -> Path | None:
    candidate = notes_dir / f"{note_id}.md"
    return candidate if candidate.exists() else None

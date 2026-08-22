from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.models import Brief


GENERATED_MARKER = "<!-- generated-by: ai-information-radar -->"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def export_brief_to_obsidian(brief: Brief, vault_path: str | Path, force: bool = False) -> tuple[Path, Path]:
    vault = Path(vault_path)
    day = brief.brief_date.isoformat()
    archive_path = vault / "04. Archives" / "Garbage" / f"今日资讯 {day}.md"
    daily_path = vault / "04. Archives" / "Daily" / f"{day}.md"

    if archive_path.exists() and GENERATED_MARKER not in archive_path.read_text(encoding="utf-8") and not force:
        raise FileExistsError(f"Refusing to overwrite a non-generated note: {archive_path}")
    archive_content = f"{GENERATED_MARKER}\n\n{brief.content_markdown.rstrip()}\n"
    _atomic_write(archive_path, archive_content)

    label = "**今日资讯**"
    link = f"[[今日资讯 {day}]]"
    block = f"{label}\n{link}"
    daily_content = daily_path.read_text(encoding="utf-8") if daily_path.exists() else ""
    # Remove prior generated link blocks and stray exact lines, then append one canonical block.
    retained = [line for line in daily_content.splitlines() if line.strip() not in {label, link}]
    updated_daily = "\n".join(retained).rstrip()
    updated_daily = f"{updated_daily}\n\n{block}\n" if updated_daily else f"{block}\n"
    _atomic_write(daily_path, updated_daily)
    return archive_path, daily_path


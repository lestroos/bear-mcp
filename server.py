"""Bear MCP Server — read and write Bear notes from Claude Code.

Reads via SQLite (read-only, safe). Writes via x-callback-url through Bear's own API.
"""

import base64
import logging
import sqlite3
import subprocess
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("bear-mcp")

mcp = FastMCP("bear")

BEAR_DB_PATH = (
    Path.home()
    / "Library"
    / "Group Containers"
    / "9K33E3U3T4.net.shinyfrog.bear"
    / "Application Data"
    / "database.sqlite"
)

# Core Data epoch: 2001-01-01 00:00:00 UTC
_CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """Open a read-only connection to the Bear SQLite database."""
    if not BEAR_DB_PATH.exists():
        raise FileNotFoundError(f"Bear database not found at {BEAR_DB_PATH}")
    conn = sqlite3.connect(f"file:{BEAR_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _bear_url(action: str, params: dict) -> None:
    """Open a bear:// x-callback-url silently in the background."""
    params = {k: v for k, v in params.items() if v is not None}
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"bear://x-callback-url/{action}?{query}"
    subprocess.run(["open", "-g", url], check=True)


def _core_data_to_iso(timestamp: float | None) -> str | None:
    """Convert a Core Data timestamp (seconds since 2001-01-01) to ISO 8601."""
    if timestamp is None:
        return None
    dt = _CORE_DATA_EPOCH + timedelta(seconds=timestamp)
    return dt.strftime("%Y-%m-%d %H:%M")


def _resolve_note_id(title: str | None = None, id: str | None = None) -> str | None:
    """Look up a note's unique identifier from a title. Returns id if already provided."""
    if id:
        return id
    if not title:
        return None
    db = _get_db()
    try:
        row = db.execute(
            "SELECT ZUNIQUEIDENTIFIER FROM ZSFNOTE WHERE ZTITLE LIKE ? AND ZTRASHED = 0 ORDER BY ZMODIFICATIONDATE DESC LIMIT 1",
            (f"%{title}%",),
        ).fetchone()
        return row["ZUNIQUEIDENTIFIER"] if row else None
    finally:
        db.close()


def _format_note(row: sqlite3.Row, include_text: bool = False) -> dict:
    """Format a note row into a clean dict."""
    result = {
        "id": row["ZUNIQUEIDENTIFIER"],
        "title": row["ZTITLE"] or "(untitled)",
        "created": _core_data_to_iso(row["ZCREATIONDATE"]),
        "modified": _core_data_to_iso(row["ZMODIFICATIONDATE"]),
        "trashed": bool(row["ZTRASHED"]),
        "pinned": bool(row["ZPINNED"]),
        "archived": bool(row["ZARCHIVED"]),
    }
    if include_text:
        result["text"] = row["ZTEXT"] or ""
    return result


def _silent_params() -> dict:
    """Common params to suppress Bear UI."""
    return {"show_window": "no", "open_note": "no"}


# ---------------------------------------------------------------------------
# Read tools (SQLite)
# ---------------------------------------------------------------------------

@mcp.tool()
async def bear_search(term: str | None = None, tag: str | None = None, limit: int = 20) -> str:
    """Search Bear notes by text or tag.

    Args:
        term: Search string to match against note title and content
        tag: Filter to notes with this tag (e.g. "work" or "work/daily-notes")
        limit: Maximum number of results (default 20)
    """
    db = _get_db()
    try:
        conditions = ["n.ZTRASHED = 0"]
        params: list = []

        if term:
            conditions.append("(n.ZTITLE LIKE ? OR n.ZTEXT LIKE ?)")
            params.extend([f"%{term}%", f"%{term}%"])

        if tag:
            conditions.append("""
                n.Z_PK IN (
                    SELECT nt.Z_5NOTES FROM Z_5TAGS nt
                    JOIN ZSFNOTETAG t ON nt.Z_13TAGS = t.Z_PK
                    WHERE t.ZTITLE LIKE ?
                )
            """)
            params.append(f"%{tag}%")

        where = " AND ".join(conditions)
        query = f"""
            SELECT ZUNIQUEIDENTIFIER, ZTITLE, ZTRASHED, ZPINNED, ZARCHIVED,
                   ZCREATIONDATE, ZMODIFICATIONDATE,
                   substr(ZTEXT, 1, 200) as ZTEXT
            FROM ZSFNOTE n
            WHERE {where}
            ORDER BY ZMODIFICATIONDATE DESC
            LIMIT ?
        """
        params.append(limit)
        rows = db.execute(query, params).fetchall()

        if not rows:
            return "No notes found."

        results = []
        for row in rows:
            note = _format_note(row, include_text=True)
            preview = note["text"][:200].replace("\n", " ")
            results.append(
                f"- **{note['title']}** (id: {note['id']})\n"
                f"  modified: {note['modified']} | created: {note['created']}\n"
                f"  {preview}"
            )

        return f"Found {len(rows)} note(s):\n\n" + "\n\n".join(results)
    finally:
        db.close()


@mcp.tool()
async def bear_read_note(title: str | None = None, id: str | None = None) -> str:
    """Read the full content of a Bear note.

    Args:
        title: Note title to search for (exact or partial match)
        id: Bear note unique identifier (takes precedence over title)
    """
    if not title and not id:
        return "Error: provide either title or id."

    db = _get_db()
    try:
        if id:
            row = db.execute(
                "SELECT * FROM ZSFNOTE WHERE ZUNIQUEIDENTIFIER = ?", (id,)
            ).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM ZSFNOTE WHERE ZTITLE LIKE ? AND ZTRASHED = 0 ORDER BY ZMODIFICATIONDATE DESC LIMIT 1",
                (f"%{title}%",),
            ).fetchone()

        if not row:
            return f"Note not found: {title or id}"

        note = _format_note(row, include_text=True)
        return (
            f"# {note['title']}\n"
            f"**ID:** {note['id']} | created: {note['created']} | modified: {note['modified']} "
            f"| pinned: {note['pinned']} | archived: {note['archived']}\n\n"
            f"{note['text']}"
        )
    finally:
        db.close()


@mcp.tool()
async def bear_list_tags(limit: int = 50) -> str:
    """List all tags in Bear.

    Args:
        limit: Maximum number of tags to return (default 50)
    """
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT ZTITLE FROM ZSFNOTETAG ORDER BY ZTITLE LIMIT ?", (limit,)
        ).fetchall()

        if not rows:
            return "No tags found."

        tags = [row["ZTITLE"] for row in rows if row["ZTITLE"]]
        return f"Tags ({len(tags)}):\n" + "\n".join(f"- {t}" for t in tags)
    finally:
        db.close()


@mcp.tool()
async def bear_list_todos(show_completed: bool = False, limit: int = 20) -> str:
    """List Bear notes that have todo items (checkboxes).

    Args:
        show_completed: If true, include notes where all todos are done
        limit: Maximum number of results (default 20)
    """
    db = _get_db()
    try:
        if show_completed:
            condition = "(n.ZTODOINCOMPLETED > 0 OR n.ZTODOCOMPLETED > 0)"
        else:
            condition = "n.ZTODOINCOMPLETED > 0"

        rows = db.execute(
            f"""
            SELECT ZUNIQUEIDENTIFIER, ZTITLE, ZTRASHED, ZPINNED, ZARCHIVED,
                   ZCREATIONDATE, ZMODIFICATIONDATE,
                   ZTODOINCOMPLETED, ZTODOCOMPLETED,
                   substr(ZTEXT, 1, 200) as ZTEXT
            FROM ZSFNOTE n
            WHERE n.ZTRASHED = 0 AND {condition}
            ORDER BY ZMODIFICATIONDATE DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        if not rows:
            return "No notes with todos found."

        results = []
        for row in rows:
            note = _format_note(row, include_text=False)
            incomplete = row["ZTODOINCOMPLETED"]
            completed = row["ZTODOCOMPLETED"]
            results.append(
                f"- **{note['title']}** (id: {note['id']})\n"
                f"  {completed} done, {incomplete} remaining | modified: {note['modified']}"
            )

        return f"Notes with todos ({len(rows)}):\n\n" + "\n\n".join(results)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Write tools (x-callback-url)
# ---------------------------------------------------------------------------

@mcp.tool()
async def bear_create_note(
    title: str,
    text: str | None = None,
    tags: str | None = None,
    timestamp: bool = False,
) -> str:
    """Create a new Bear note.

    Args:
        title: Note title
        text: Note body content (markdown supported)
        tags: Comma-separated tags (e.g. "work,daily-notes")
        timestamp: Prepend current date/time to the note
    """
    params = {
        "title": title,
        "text": text,
        "tags": tags,
        "timestamp": "yes" if timestamp else None,
        **_silent_params(),
    }
    _bear_url("create", params)
    return f"Created note: {title}"


@mcp.tool()
async def bear_append_to_note(
    text: str,
    title: str | None = None,
    id: str | None = None,
    mode: str = "append",
    header: str | None = None,
    timestamp: bool = False,
) -> str:
    """Append or prepend text to an existing Bear note.

    Args:
        text: Text to add to the note
        title: Note title to find (use id for exact match)
        id: Bear note unique identifier (takes precedence over title)
        mode: "append" (end of note) or "prepend" (after title)
        header: Target a specific ## header to append under
        timestamp: Prepend current date/time to the added text
    """
    if not title and not id:
        return "Error: provide either title or id."

    if mode not in ("append", "prepend"):
        return "Error: mode must be 'append' or 'prepend'."

    note_id = _resolve_note_id(title, id)

    params = {
        "id": note_id,
        "title": title if not note_id else None,
        "text": text,
        "mode": mode,
        "header": header,
        "new_line": "yes",
        "timestamp": "yes" if timestamp else None,
        **_silent_params(),
    }
    _bear_url("add-text", params)

    target = header or title or note_id
    return f"Text {mode}ed to note ({target})."


@mcp.tool()
async def bear_update_section(
    text: str,
    header: str,
    title: str | None = None,
    id: str | None = None,
) -> str:
    """Replace the content under a specific header in a Bear note.

    Args:
        text: New content to replace the section with
        header: The ## header name whose content will be replaced
        title: Note title to find (use id for exact match)
        id: Bear note unique identifier (takes precedence over title)
    """
    if not title and not id:
        return "Error: provide either title or id."

    note_id = _resolve_note_id(title, id)

    params = {
        "id": note_id,
        "title": title if not note_id else None,
        "text": text,
        "mode": "replace",
        "header": header,
        **_silent_params(),
    }
    _bear_url("add-text", params)

    target = title or note_id
    return f"Replaced section '{header}' in note ({target})."


@mcp.tool()
async def bear_add_tags(
    tags: str,
    title: str | None = None,
    id: str | None = None,
) -> str:
    """Add tags to an existing Bear note.

    Args:
        tags: Comma-separated tags to add (e.g. "work,project-a")
        title: Note title to find (use id for exact match)
        id: Bear note unique identifier (takes precedence over title)
    """
    if not title and not id:
        return "Error: provide either title or id."

    note_id = _resolve_note_id(title, id)

    params = {
        "id": note_id,
        "title": title if not note_id else None,
        "tags": tags,
        "text": "",
        "mode": "append",
        **_silent_params(),
    }
    _bear_url("add-text", params)

    target = title or note_id
    return f"Added tags [{tags}] to note ({target})."


@mcp.tool()
async def bear_rename_tag(name: str, new_name: str) -> str:
    """Rename a tag across all Bear notes.

    Args:
        name: Current tag name
        new_name: New tag name
    """
    params = {"name": name, "new_name": new_name, "show_window": "no"}
    _bear_url("rename-tag", params)
    return f"Renamed tag '{name}' to '{new_name}'."


@mcp.tool()
async def bear_delete_tag(name: str) -> str:
    """Delete a tag from all Bear notes (notes are kept, just untagged).

    Args:
        name: Tag name to delete
    """
    params = {"name": name, "show_window": "no"}
    _bear_url("delete-tag", params)
    return f"Deleted tag '{name}'."


@mcp.tool()
async def bear_trash_note(title: str | None = None, id: str | None = None) -> str:
    """Move a Bear note to the trash.

    Args:
        title: Note title to find
        id: Bear note unique identifier (takes precedence over title)
    """
    if not title and not id:
        return "Error: provide either title or id."

    note_id = _resolve_note_id(title, id)
    if not note_id:
        return f"Note not found: {title or id}"

    params = {"id": note_id, "show_window": "no"}
    _bear_url("trash", params)

    target = title or note_id
    return f"Trashed note ({target})."


@mcp.tool()
async def bear_archive_note(title: str | None = None, id: str | None = None) -> str:
    """Archive a Bear note.

    Args:
        title: Note title to find
        id: Bear note unique identifier (takes precedence over title)
    """
    if not title and not id:
        return "Error: provide either title or id."

    note_id = _resolve_note_id(title, id)
    if not note_id:
        return f"Note not found: {title or id}"

    params = {"id": note_id, "show_window": "no"}
    _bear_url("archive", params)

    target = title or note_id
    return f"Archived note ({target})."


@mcp.tool()
async def bear_grab_url(url: str, tags: str | None = None, pin: bool = False) -> str:
    """Save a web page as a Bear note. Bear converts the page to markdown.

    Args:
        url: Web URL to save
        tags: Comma-separated tags for the new note
        pin: Pin the note to the top
    """
    params = {
        "url": url,
        "tags": tags,
        "pin": "yes" if pin else None,
        **_silent_params(),
    }
    _bear_url("grab-url", params)
    return f"Grabbing URL: {url}"


@mcp.tool()
async def bear_add_file(
    file_path: str,
    title: str | None = None,
    id: str | None = None,
    header: str | None = None,
    mode: str = "append",
) -> str:
    """Attach a file (image, screenshot, PDF, etc.) to a Bear note.

    Args:
        file_path: Absolute path to the file to attach
        title: Note title to find (use id for exact match)
        id: Bear note unique identifier (takes precedence over title)
        header: Target a specific ## header to insert the file under
        mode: "append" (end of note/section) or "prepend" (top of note/section)
    """
    if not title and not id:
        return "Error: provide either title or id."

    path = Path(file_path).expanduser()
    if not path.exists():
        return f"File not found: {file_path}"

    file_size = path.stat().st_size
    # macOS URL scheme has a practical limit; base64 adds ~33% overhead
    if file_size > 750_000:
        return f"File too large ({file_size:,} bytes). Bear URL scheme supports files up to ~750KB. Try compressing the image first."

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    filename = path.name

    note_id = _resolve_note_id(title, id)

    params = {
        "id": note_id,
        "title": title if not note_id else None,
        "file": encoded,
        "filename": filename,
        "header": header,
        "mode": mode,
        **_silent_params(),
    }
    _bear_url("add-file", params)

    target = title or note_id
    return f"Attached {filename} ({file_size:,} bytes) to note ({target})."


@mcp.tool()
async def bear_create_from_template(
    template_title: str,
    new_title: str,
    replacements: str | None = None,
    tags: str | None = None,
) -> str:
    """Create a new note from a Bear template note.

    Reads a template note by title, optionally applies text replacements,
    and creates a new note with the result.

    Args:
        template_title: Title of the template note to copy from
        new_title: Title for the new note
        replacements: Pipe-separated key=value pairs for placeholders (e.g. "{{date}}=2026-03-06|{{project}}=Trixie")
        tags: Comma-separated tags for the new note (overrides template tags)
    """
    db = _get_db()
    try:
        row = db.execute(
            "SELECT ZTEXT FROM ZSFNOTE WHERE ZTITLE LIKE ? AND ZTRASHED = 0 ORDER BY ZMODIFICATIONDATE DESC LIMIT 1",
            (f"%{template_title}%",),
        ).fetchone()

        if not row:
            return f"Template not found: {template_title}"

        text = row["ZTEXT"] or ""

        # Strip the template's own title line (the new note gets its own)
        lines = text.split("\n")
        if lines and lines[0].startswith("# "):
            text = "\n".join(lines[1:]).lstrip("\n")

        # Strip template tags at the end (lines starting with #tag)
        # Bear tags appear as #tag_name at the end of note text
        while text.rstrip().endswith(")") or text.rstrip().split("\n")[-1:] == [""]:
            break
        # Simple: strip lines that are only tags
        clean_lines = text.rstrip().split("\n")
        while clean_lines and clean_lines[-1].strip().startswith("#") and " " not in clean_lines[-1].strip():
            clean_lines.pop()
        text = "\n".join(clean_lines)

        # Apply replacements
        if replacements:
            for pair in replacements.split("|"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    text = text.replace(key.strip(), value.strip())
    finally:
        db.close()

    params = {
        "title": new_title,
        "text": text,
        "tags": tags,
        **_silent_params(),
    }
    _bear_url("create", params)
    return f"Created note '{new_title}' from template '{template_title}'."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

# Bear MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server for [Bear](https://bear.app/) — the note-taking app for Apple devices. Gives AI assistants (Claude Code, Claude Desktop, etc.) full read/write access to your Bear notes.

## Why

Bear has no official API, but it does have:
- An **x-callback-url scheme** for creating and modifying notes
- A **SQLite database** that's readable for querying notes

This server combines both: SQLite for fast, flexible reads; x-callback-url for safe writes that go through Bear's own sync engine.

## Architecture

```
┌─────────────┐    stdio (JSON-RPC)    ┌──────────────────┐
│  MCP Client │◄──────────────────────►│  Bear MCP Server │
│  (Claude)   │                        │  (Python)        │
└─────────────┘                        └────────┬─────────┘
                                                │
                                    ┌───────────┴───────────┐
                                    │                       │
                                    ▼                       ▼
                              ┌──────────┐         ┌──────────────┐
                              │  SQLite  │         │  x-callback  │
                              │  (ro)    │         │  (open -g)   │
                              └────┬─────┘         └──────┬───────┘
                                   │                      │
                                   ▼                      ▼
                              ┌────────────────────────────────┐
                              │           Bear.app             │
                              │     (iCloud sync, storage)     │
                              └────────────────────────────────┘
```

**Reads** go directly to Bear's SQLite database (`file:...?mode=ro`). This is safe — we're a read-only consumer, and Bear treats the database as its own.

**Writes** go through Bear's x-callback-url scheme (`bear://x-callback-url/...`), which routes through Bear's own API layer. This ensures iCloud sync, conflict resolution, and data integrity are all handled by Bear itself.

All write operations use `open -g` (background) + `show_window=no` + `open_note=no` to run silently without stealing focus.

## Requirements

- macOS (Bear is Apple-only; the SQLite path and `open` command are macOS-specific)
- [Bear](https://bear.app/) installed
- Python 3.10+
- `mcp` Python package (`pip install mcp`)

## Installation

### Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "bear": {
      "command": "python3",
      "args": ["/path/to/bear/server.py"]
    }
  }
}
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "bear": {
      "command": "python3",
      "args": ["/path/to/bear/server.py"]
    }
  }
}
```

### Other MCP clients

The server uses stdio transport. Launch it as a subprocess:

```bash
python3 server.py
```

## Tools (15)

### Read Tools (via SQLite)

#### `bear_search`
Search notes by text content or tag.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `term` | string | null | Search string (matches title and body) |
| `tag` | string | null | Filter by tag (e.g. "work" or "work/projects") |
| `limit` | int | 20 | Max results |

Returns: note title, id, created/modified dates, and a text preview.

#### `bear_read_note`
Read the full content of a note.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | string | null | Note title (partial match) |
| `id` | string | null | Note unique identifier (exact match, takes precedence) |

Returns: full note content with metadata (id, dates, pinned, archived status).

#### `bear_list_tags`
List all tags.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Max results |

#### `bear_list_todos`
Find notes with todo checkboxes.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `show_completed` | bool | false | Include notes where all todos are done |
| `limit` | int | 20 | Max results |

Returns: note title, id, count of completed and remaining todos.

### Write Tools (via x-callback-url)

#### `bear_create_note`
Create a new note.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | string | required | Note title |
| `text` | string | null | Body content (markdown) |
| `tags` | string | null | Comma-separated tags |
| `timestamp` | bool | false | Prepend current date/time |

#### `bear_append_to_note`
Add text to an existing note.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | string | required | Text to add |
| `title` | string | null | Note title |
| `id` | string | null | Note identifier |
| `mode` | string | "append" | "append" or "prepend" |
| `header` | string | null | Target a specific `## Header` |
| `timestamp` | bool | false | Prepend current date/time |

#### `bear_update_section`
Replace content under a specific header.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | string | required | New section content |
| `header` | string | required | Header name to target |
| `title` | string | null | Note title |
| `id` | string | null | Note identifier |

This is the most powerful write tool — it enables surgical updates to specific sections of a note without touching the rest.

#### `bear_add_tags`
Add tags to a note.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tags` | string | required | Comma-separated tags |
| `title` | string | null | Note title |
| `id` | string | null | Note identifier |

#### `bear_rename_tag`
Rename a tag across all notes.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | string | required | Current tag name |
| `new_name` | string | required | New tag name |

#### `bear_delete_tag`
Delete a tag (notes are kept, just untagged).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | string | required | Tag to delete |

#### `bear_trash_note`
Move a note to the trash.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | string | null | Note title |
| `id` | string | null | Note identifier |

#### `bear_archive_note`
Archive a note.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | string | null | Note title |
| `id` | string | null | Note identifier |

#### `bear_grab_url`
Save a web page as a Bear note. Bear handles the HTML-to-markdown conversion.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | required | Web URL to save |
| `tags` | string | null | Comma-separated tags |
| `pin` | bool | false | Pin to top |

#### `bear_add_file`
Attach a file (image, screenshot, PDF) to a note.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | string | required | Absolute path to the file |
| `title` | string | null | Note title |
| `id` | string | null | Note identifier |
| `header` | string | null | Insert under a specific header |
| `mode` | string | "append" | "append" or "prepend" |

**File size limit:** ~750KB. Files are base64-encoded and sent via URL scheme, which has a practical limit around 1MB. For larger images, compress or resize first.

#### `bear_create_from_template`
Create a new note from a template note.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `template_title` | string | required | Title of the template note |
| `new_title` | string | required | Title for the new note |
| `replacements` | string | null | Pipe-separated `key=value` pairs (e.g. `{{date}}=2026-03-06\|{{project}}=Trixie`) |
| `tags` | string | null | Tags for the new note |

Templates are convention-based — any note can be a template. The tool reads the note, strips its title and trailing tags, applies placeholder replacements, and creates a new note.

## How It Works

### SQLite Database

Bear stores all notes in a Core Data SQLite database at:

```
~/Library/Group Containers/9K33E3U3T4.net.shinyfrog.bear/Application Data/database.sqlite
```

Key tables:
- `ZSFNOTE` — all notes (title, text, dates, flags)
- `ZSFNOTETAG` — tag definitions
- `Z_5TAGS` — join table linking notes to tags

Timestamps use **Core Data epoch** (seconds since 2001-01-01 00:00:00 UTC), not Unix epoch. The server converts these to human-readable ISO 8601 format.

### x-callback-url Scheme

Bear supports 16 URL scheme actions. Documentation: https://bear.app/faq/x-callback-url-scheme-documentation/

The server uses these actions for writes:
- `/create` — new notes
- `/add-text` — append, prepend, replace, section-targeted updates
- `/add-file` — file attachments (base64-encoded)
- `/trash`, `/archive` — note lifecycle
- `/rename-tag`, `/delete-tag` — tag management
- `/grab-url` — web clipping

All calls use `open -g` (macOS background open) to avoid stealing focus.

### Title vs ID Resolution

When a `title` is provided for write operations, the server first looks up the note's unique identifier via SQLite. This avoids ambiguity if multiple notes share similar titles. The `id` parameter always takes precedence and is used directly.

## Limitations

- **macOS only** — depends on Bear's SQLite path and macOS `open` command
- **No write confirmation** — x-callback-url doesn't return results to CLI callers (only to other apps via x-success). Write operations are fire-and-forget. The server verifies the note exists before writing, but can't confirm the write succeeded.
- **File size limit** — ~750KB for file attachments due to URL scheme length limits
- **No encrypted note support** — encrypted notes can't be read via SQLite (content is in `ZENCRYPTEDDATA` blob)
- **Bear must be running** — x-callback-url writes require Bear to be open (it can be in the background)

## Example Prompts

```
Search my Bear notes for "project plan"
```

```
Read my "Meeting Notes" Bear note
```

```
Create a Bear note called "Sprint Retro" with sections for "What went well", "What didn't", and "Action items"
```

```
Append "- Completed the deploy" to my "Sprint Retro" note under the "What went well" section
```

```
Show me Bear notes with incomplete todos
```

```
Save this page to Bear: https://example.com/article
```

```
Attach the screenshot at ~/Desktop/screenshot.png to my "Bug Report" Bear note
```

```
Archive my "Old Project" Bear note
```

## License

MIT

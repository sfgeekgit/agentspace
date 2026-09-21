"""Readable, escaped HTML for result artifacts. No browser Markdown dependency."""
import html
import json
from pathlib import PurePosixPath

from markdown_it import MarkdownIt

# Existing runs are below this limit. A large future artifact starts with a
# bounded preview; the reader can explicitly open the full file or download it.
FULL_VIEW_LIMIT = 5_000_000
PREVIEW_BYTES = 500_000


def size_label(size):
    if size < 1_000:
        return f"{size:,} B"
    if size < 1_000_000:
        return f"{size / 1_000:,.1f} KB"
    return f"{size / 1_000_000:,.2f} MB"


def preview(content, *, full=False):
    truncated = not full and len(content) > FULL_VIEW_LIMIT
    visible = content[:PREVIEW_BYTES] if truncated else content
    # Prefer complete lines/JSONL records; fall back to a character-safe prefix
    # when a single enormous record itself exceeds the preview allowance.
    if truncated and b"\n" in visible:
        visible = visible[:visible.rfind(b"\n") + 1]
    text = visible.decode("utf-8", "ignore" if truncated else "replace")
    return text, truncated, len(text.encode("utf-8"))


def code(text):
    return f'<pre class="result-code"><code>{html.escape(text)}</code></pre>'


def pretty(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


def markdown(text):
    # Treat all artifact content as untrusted, including generated Markdown.
    # Raw HTML stays text, dangerous link schemes are rejected by MarkdownIt,
    # and image syntax shows its label rather than fetching remote resources.
    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    md.add_render_rule("image", lambda renderer, tokens, idx, options, env:
                       '<span class="muted">[Image: ' + html.escape(tokens[idx].content) + ']</span>')
    tokens = md.parse(text)
    sections = []
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            anchor = f"section-{len(sections) + 1}"
            token.attrSet("id", anchor)
            title = tokens[index + 1].content
            sections.append((anchor, title))
    return '<article class="result-prose">' + md.renderer.render(tokens, md.options, {}) + '</article>', sections


def json_records(text, truncated):
    records, omitted = [], False
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            if truncated:
                omitted = True
                break
            return code(text), [], 'Invalid JSONL; showing the complete source text.'
    # With an exceptionally long first record, show the available source text
    # rather than an empty list. The top-of-page preview notice still applies.
    if not records and omitted:
        return code(text), [], 'This preview ends within a JSON record; showing source text.'
    parts, sections = [], []
    for number, record in enumerate(records, 1):
        anchor = f"record-{number}"
        label = f"Record {number}"
        if isinstance(record, dict):
            context = [str(record[k]) for k in ("agent", "speaker", "role", "kind") if k in record]
            if context:
                label += " · " + " · ".join(context)
        sections.append((anchor, label))
        parts.append(f'<section class="result-record" id="{anchor}"><h2>{html.escape(label)}</h2>')
        if isinstance(record, dict):
            parts.append('<dl class="result-fields">')
            for key, value in record.items():
                # Actual newlines in text fields read as paragraphs/lines, not
                # literal backslash-n sequences. All fields remain visible.
                display = value if isinstance(value, str) else pretty(value)
                if key == "text" and isinstance(value, str):
                    try:
                        display = pretty(json.loads(value))
                    except ValueError:
                        pass
                parts.append(f'<dt>{html.escape(str(key))}</dt><dd>{code(display)}</dd>')
            parts.append('</dl>')
        else:
            parts.append(code(pretty(record)))
        parts.append('</section>')
    note = 'Only complete records are shown in this preview.' if omitted else ''
    return ''.join(parts), sections, note


def render(filename, text, *, truncated=False, source=False):
    """Return (HTML, jump targets, display note); never silently drop content."""
    if source:
        return code(text), [], ''
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix == ".md":
        body, sections = markdown(text)
        return body, sections, ''
    if suffix == ".json":
        try:
            return code(pretty(json.loads(text))), [], ''
        except ValueError:
            note = ('A partial JSON file cannot be formatted; showing the beginning of its source text.'
                    if truncated else 'Invalid JSON; showing the complete source text.')
            return code(text), [], note
    if suffix == ".jsonl":
        return json_records(text, truncated)
    return code(text), [], ''

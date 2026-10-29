from __future__ import annotations

from html import escape
from typing import Any


_STYLE = """
body {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  margin: 0;
  color: #1f2328;
  background: #ffffff;
}
main {
  max-width: 1400px;
  margin: 0 auto;
  padding: 24px;
}
h1, h2 {
  margin: 0 0 12px;
}
.summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin: 16px 0 24px;
}
.card {
  border: 1px solid #d0d7de;
  border-radius: 8px;
  padding: 12px;
  background: #f6f8fa;
}
.card .label {
  display: block;
  font-size: 12px;
  color: #656d76;
  margin-bottom: 4px;
}
.card .value {
  font-size: 14px;
  white-space: pre-wrap;
  word-break: break-word;
}
.hunk {
  border: 1px solid #d0d7de;
  border-radius: 8px;
  overflow: hidden;
  margin: 0 0 20px;
}
.hunk-header {
  background: #f6f8fa;
  border-bottom: 1px solid #d0d7de;
  padding: 10px 12px;
  font-weight: 600;
}
table {
  width: 100%;
  border-collapse: collapse;
}
td {
  vertical-align: top;
  padding: 0;
}
.line-no {
  width: 56px;
  padding: 6px 8px;
  text-align: right;
  color: #656d76;
  border-right: 1px solid #d8dee4;
  user-select: none;
}
.code {
  white-space: pre-wrap;
  word-break: break-word;
  padding: 6px 10px;
}
.ctx { background: #ffffff; }
.del { background: #ffebe9; }
.ins { background: #dafbe1; }
.chg { background: #fff8c5; }
.empty {
  color: #8c959f;
}
""".strip()


def _escape_code(text: str | None) -> str:
    if text is None or text == "":
        return '<span class="empty">&nbsp;</span>'
    return escape(text)


def _fmt_line_no(value: Any) -> str:
    return "" if value is None else escape(str(value))


def _render_row(line: dict[str, Any]) -> str:
    op = line.get("op", "ctx")
    old_line = _fmt_line_no(line.get("old_line"))
    new_line = _fmt_line_no(line.get("new_line"))
    old_text = _escape_code(line.get("old_text"))
    new_text = _escape_code(line.get("new_text"))
    css = op if op in {"ctx", "del", "ins", "chg"} else "ctx"
    return (
        f'<tr class="{css}">'
        f'<td class="line-no">{old_line}</td>'
        f'<td class="code">{old_text}</td>'
        f'<td class="line-no">{new_line}</td>'
        f'<td class="code">{new_text}</td>'
        "</tr>"
    )


def render_line_diff_html(artifact: dict[str, Any]) -> str:
    line_diff = artifact.get("line_diff") or artifact
    stats = line_diff.get("stats", {})
    equivalence = line_diff.get("equivalence", {})
    old_meta = line_diff.get("old", {})
    new_meta = line_diff.get("new", {})

    title = artifact.get("relpath") or artifact.get("old_path") or "CK3 Diff"
    subtitle = artifact.get("new_path") or artifact.get("relpath") or ""

    summary_cards = [
        ("Target", title),
        ("Compare To", subtitle),
        ("Added Lines", stats.get("added_lines", 0)),
        ("Deleted Lines", stats.get("deleted_lines", 0)),
        ("Hunks", stats.get("hunk_count", 0)),
        ("Exact Equal", equivalence.get("exact_equal", False)),
        ("Formatting Only", equivalence.get("formatting_only", False)),
        ("Old Hash", old_meta.get("hash", "")),
        ("New Hash", new_meta.get("hash", "")),
    ]

    summary_html = "".join(
        f'<div class="card"><span class="label">{escape(str(label))}</span>'
        f'<div class="value">{escape(str(value))}</div></div>'
        for label, value in summary_cards
        if value != ""
    )

    hunk_html = []
    for hunk in line_diff.get("hunks", []):
        rows = "".join(_render_row(line) for line in hunk.get("lines", []))
        hunk_html.append(
            '<section class="hunk">'
            f'<div class="hunk-header">{escape(str(hunk.get("header", "Hunk")))}</div>'
            '<table>'
            '<tbody>'
            f'{rows}'
            '</tbody>'
            '</table>'
            '</section>'
        )

    hunks_block = "".join(hunk_html) or '<p>No line hunks.</p>'

    return (
        '<!doctype html>'
        '<html lang="en">'
        '<head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(str(title))}</title>'
        f'<style>{_STYLE}</style>'
        '</head>'
        '<body>'
        '<main>'
        f'<h1>{escape(str(title))}</h1>'
        f'<div class="summary">{summary_html}</div>'
        f'{hunks_block}'
        '</main>'
        '</body>'
        '</html>'
    )

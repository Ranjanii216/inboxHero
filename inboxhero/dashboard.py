"""Three-pane dashboard written from a completed run (never hand-assembled)."""

from __future__ import annotations

import html
import json
from dataclasses import asdict
from datetime import date

import config
from inboxhero.commitments import Commitment, Conflict
from inboxhero.dispositions import Decision
from inboxhero.hostile import Refusal
from inboxhero.store import MailStore


def build(
    store: MailStore,
    decisions: list[Decision],
    refusals: list[Refusal],
    pending: list[dict],
    flagged_extra: list[dict],
    items: list[Commitment],
    conflicts: list[Conflict],
) -> dict:
    flagged = [
        {"message_id": r.message_id, "attempted": r.attempted, "instead": r.instead, "kind": "hostile"}
        for r in refusals
    ]
    flagged.extend(flagged_extra)
    payload = {
        "pending": pending,
        "pending_actions": pending,
        "flagged": flagged,
        "commitments": [
            {"title": c.title, "when": c.when, "kind": c.kind, "cited": c.cited, "notes": c.notes}
            for c in items
        ],
        "conflicts": [asdict(k) for k in conflicts],
        "messages": len(store),
        "undecided": sum(1 for d in decisions if not d.disposition),
    }
    config.DASHBOARD_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    config.DASHBOARD_HTML.write_text(_html(payload), encoding="utf-8")
    return payload


def _table(headers: list[str], data: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in data
    )
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def _calendar(payload: dict) -> str:
    """Commitments grouped by day, oldest first, with conflicted entries marked."""
    clash_titles: dict[str, list[str]] = {}
    for k in payload["conflicts"]:
        for t in k["titles"]:
            clash_titles.setdefault(t, []).append(k["kind"])
    days: dict[str, list[dict]] = {}
    for c in payload["commitments"]:
        days.setdefault(c["when"][:10], []).append(c)
    out = []
    for day in sorted(days):
        label = date.fromisoformat(day).strftime("%a %d %b %Y")
        rows = []
        for c in days[day]:
            time = c["when"][11:16] or "all day"
            kinds = clash_titles.get(c["title"])
            flag = (f"<span class='clash'>CONFLICT ({', '.join(sorted(set(kinds)))})</span> " if kinds else "")
            rows.append(
                f"<tr class='{'clashrow' if kinds else ''}'><td>{html.escape(time)}</td>"
                f"<td>{html.escape(c['kind'])}</td>"
                f"<td>{flag}{html.escape(c['title'])}</td>"
                f"<td>{html.escape(', '.join(c['cited']))}</td></tr>"
            )
        out.append(f"<h3>{html.escape(label)}</h3><table><tbody>{''.join(rows)}</tbody></table>")
    return "".join(out) or "<p>No commitments found.</p>"


def _html(payload: dict) -> str:
    pending = _table(
        ["message", "proposed action", "why a human"],
        [[f"{p.get('message_id', '')} - {p.get('subject', '')}", p.get("proposed", ""), p.get("why", "")]
         for p in payload["pending_actions"]],
    )
    flagged = _table(
        ["message", "what was attempted", "what the system did instead"],
        [[f["message_id"], f.get("attempted", ""), f.get("instead", "")] for f in payload["flagged"]],
    )
    banner = "".join(
        f"<p class='conflict'>CONFLICT ({html.escape(k['kind'])}) {html.escape(k['when'].replace('T', ' '))}: "
        f"{html.escape(k['note'])} &mdash; cited {html.escape(', '.join(k['cited']))}</p>"
        for k in payload["conflicts"]
    ) or "<p>No conflicts.</p>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<title>inboxHero dashboard</title>
<style>
body {{ font-family: Georgia, serif; margin: 24px; background: #f6f1e8; color: #1c1917; }}
section {{ background: #fff; padding: 16px 20px; margin-bottom: 18px; border: 1px solid #e7e0d6; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; margin-bottom: 8px; }}
th, td {{ border-bottom: 1px solid #eee; text-align: left; padding: 8px 6px; vertical-align: top; }}
th {{ font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #57534e; }}
h3 {{ margin: 14px 0 4px; font-size: 15px; }}
.conflict, .clash {{ color: #9f1239; font-weight: 600; }}
.clashrow {{ background: #fff1f2; }}
</style></head>
<body>
<h1>inboxHero - three panes</h1>
<p>Built from a run over {payload['messages']} messages. Undecided: {payload['undecided']}.</p>
<section><h2>1. Pending actions</h2>{pending}</section>
<section><h2>2. Flagged</h2>{flagged}</section>
<section><h2>3. Commitments</h2>{banner}{_calendar(payload)}</section>
</body></html>
"""

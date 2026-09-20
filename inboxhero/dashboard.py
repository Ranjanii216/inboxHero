"""Three-pane dashboard written from a completed run (not hand-assembled)."""

from __future__ import annotations

import html
import json
from dataclasses import asdict

import config
from inboxhero.commitments import Conflict, Commitment
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
    pending_rows = pending
    flagged = [
        {
            "message_id": r.message_id,
            "attempted": r.attempted,
            "instead": r.instead,
            "kind": "hostile",
        }
        for r in refusals
    ]
    flagged.extend(flagged_extra)
    payload = {
        "pending_actions": pending_rows,
        "flagged": flagged,
        "commitments": [asdict(c) for c in items],
        "conflicts": [asdict(c) for c in conflicts],
        "messages": len(store),
        "undecided": sum(1 for d in decisions if not d.disposition),
    }
    config.DASHBOARD_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    config.DASHBOARD_HTML.write_text(_html(store, payload), encoding="utf-8")
    return payload


def _html(store: MailStore, payload: dict) -> str:
    def rows(headers: list[str], data: list[list[str]]) -> str:
        th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
        body = []
        for row in data:
            tds = "".join(f"<td>{html.escape(c)}</td>" for c in row)
            body.append(f"<tr>{tds}</tr>")
        return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>"

    pending = rows(
        ["message", "proposed", "why a human"],
        [
            [
                f"{p.get('message_id','')} — {p.get('subject','')}",
                p.get("proposed", ""),
                p.get("why", ""),
            ]
            for p in payload["pending_actions"]
        ],
    )
    flagged = rows(
        ["message", "attempted", "instead"],
        [
            [f["message_id"], f.get("attempted", ""), f.get("instead", "")]
            for f in payload["flagged"]
        ],
    )
    commits = rows(
        ["when", "title", "cited"],
        [[c["when"], c["title"], ", ".join(c["cited"])] for c in payload["commitments"]],
    )
    conflict_bits = "".join(
        f"<p class='conflict'>CONFLICT at {html.escape(c['when'])}: "
        f"{html.escape(' vs '.join(c['titles']))} "
        f"(cited {html.escape(', '.join(c['cited']))})</p>"
        for c in payload["conflicts"]
    ) or "<p>No timed conflicts.</p>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<title>inboxHero dashboard</title>
<style>
body {{ font-family: Georgia, serif; margin: 24px; background: #f6f1e8; color: #1c1917; }}
h1 {{ font-weight: 600; }}
section {{ background: #fff; padding: 16px 20px; margin-bottom: 18px; border: 1px solid #e7e0d6; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ border-bottom: 1px solid #eee; text-align: left; padding: 8px 6px; vertical-align: top; }}
th {{ font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #57534e; }}
.conflict {{ color: #9f1239; font-weight: 600; }}
</style></head>
<body>
<h1>inboxHero — three panes</h1>
<p>Reproduced from a run over {payload['messages']} messages. Undecided: {payload['undecided']}.</p>
<section><h2>1. Pending actions</h2>{pending}</section>
<section><h2>2. Flagged</h2>{flagged}</section>
<section><h2>3. Commitments</h2>{conflict_bits}{commits}</section>
</body></html>
"""

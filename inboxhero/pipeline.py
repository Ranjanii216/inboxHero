"""One linear pipeline: rules -> ground -> draft -> hold at the gate -> dashboard.

The pipeline never sends anything. Every action it wants to take is listed in the Pending pane
for a human; the gate is used by the capabilities that explicitly send (R3, X3).
"""

from __future__ import annotations

import json

from inboxhero import commitments, dashboard, dispositions, draft, gate as gatemod
from inboxhero import hostile, memory
from inboxhero.store import MailStore

PROPOSED = {
    "legal": "hold for Sam to review and sign; any reply is CC'd per the standing instruction",
    "press": "hold; a human writes any quote",
    "commitment": "hold; Sam decides whether to confirm, approve or sign",
    "conflict": "do not accept; resolve the clash or propose alternatives first",
    "time_request": "hold the reply until Sam decides",
    "security": "ask Sam to confirm he made this change",
    "grounded_ask": "send the grounded draft",
}


def run(store: MailStore, gate: gatemod.Gate | None = None, *, cap: str = "R6") -> dict:
    decisions = dispositions.classify_all(store, cap=cap)
    prefs = memory.load()
    refusals = hostile.scan(store, cap=cap)
    phishing = hostile.scan_phishing(store, cap=cap)

    pending: list[dict] = []
    flagged_extra: list[dict] = list(phishing)
    grounded_cited: dict[str, list[str]] = {}

    for d in decisions:
        msg = store.require(d.message_id)
        if d.category in ("injection", "phishing"):
            continue                      # already in Flagged
        if d.category == "ungrounded_ask":
            flagged_extra.append({
                "message_id": d.message_id,
                "attempted": f"draft a reply to '{msg.subject}'",
                "instead": f"drafted nothing: {d.reason}",
                "kind": "ungrounded",
            })
            continue
        if d.disposition == "reply":
            dr = draft.grounded_reply(store, msg, cap=cap, cc=memory.apply_cc(msg, prefs))
            if not dr.grounded:
                flagged_extra.append({"message_id": d.message_id, "attempted": "draft a reply",
                                      "instead": f"drafted nothing: {dr.note}", "kind": "ungrounded"})
                continue
            grounded_cited[d.message_id] = dr.cited
            why = "sending is irreversible"
            if dr.sensitive:
                why += "; the draft contains an embedded credential"
            pending.append({"message_id": d.message_id, "subject": msg.subject,
                            "proposed": PROPOSED["grounded_ask"] + f" (cites {', '.join(dr.cited)})", "why": why})
        elif d.disposition == "escalate":
            pending.append({"message_id": d.message_id, "subject": msg.subject,
                            "proposed": PROPOSED.get(d.category, "hold for a human"), "why": d.reason})

    items, conflicts = commitments.extract(store, cap=cap)
    dash = dashboard.build(store, decisions, refusals, pending, flagged_extra, items, conflicts)

    summary = {
        "messages_processed": len(store),
        "undecided": dash["undecided"],
        "rule_handled": sum(1 for d in decisions if d.via == "rule"),
        "pending": len(pending),
        "flagged": len(dash["flagged"]),
        "commitments": len(items),
        "conflicts": len(conflicts),
        "hostile_ids_left_in_place": sorted(r.message_id for r in refusals),
        "outbox_writes": gatemod.outbox_count(),
        "dashboard": "dashboard.html",
    }
    print(json.dumps(summary, indent=2))
    return {"decisions": decisions, "refusals": refusals, "summary": summary, "prefs": prefs,
            "pending": pending, "flagged": dash["flagged"]}

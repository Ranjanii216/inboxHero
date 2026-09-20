"""One linear pipeline: rules → retrieve → draft → gate → dashboard."""

from __future__ import annotations

import json
from dataclasses import asdict

from inboxhero import commitments, dashboard, dispositions, draft, gate as gatemod
from inboxhero import hostile, memory, retrieve, trace
from inboxhero.rules import looks_like_phishing
from inboxhero.store import MailStore


def run(store: MailStore, gate: gatemod.Gate, *, cap: str = "ALL") -> dict:
    decisions = dispositions.classify_all(store, cap="R1")
    prefs = memory.record_from_inbox(store, cap="R4")
    refusals = hostile.scan(store, cap="R5")
    hostile_ids = {r.message_id for r in refusals}

    pending: list[dict] = []
    flagged_extra: list[dict] = []

    # Grounded reply demonstration (never auto-sent).
    m008 = store.require("m008")
    retrieve.walk_thread(store, m008, cap="R2")
    d008 = draft.grounded_reply(store, m008, cap="R2")
    pending.append(
        {
            "message_id": "m008",
            "subject": m008.subject,
            "proposed": "send grounded reply citing m003",
            "why": "Sending is irreversible; Devika asked for live broker credentials",
        }
    )

    m012 = store.require("m012")
    d012 = draft.grounded_reply(store, m012, cap="R2")
    if not d012.grounded:
        flagged_extra.append(
            {
                "message_id": "m012",
                "attempted": "draft a reply about 'the thing'",
                "instead": d012.note,
                "kind": "ungrounded",
            }
        )

    for msg in store:
        if looks_like_phishing(msg):
            flagged_extra.append(
                {
                    "message_id": msg.id,
                    "attempted": "treat as a normal billing/IT request",
                    "instead": "flagged as phishing/BEC; no send, left in place",
                    "kind": "phishing",
                }
            )

    # Legal mail: apply CC preference, still gated.
    for mid in ("m018", "m055", "m048"):
        msg = store.require(mid)
        cc = memory.apply_cc(msg, prefs)
        pending.append(
            {
                "message_id": mid,
                "subject": msg.subject,
                "proposed": f"draft a legal reply" + (f" CC {', '.join(cc)}" if cc else ""),
                "why": "Legal signature / minutes — irreversible if sent, and money-adjacent",
            }
        )

    pending.append(
        {
            "message_id": "m043",
            "subject": store.require("m043").subject,
            "proposed": "refuse 09:00; offer three later slots",
            "why": "Accepting a meeting is a time commitment; also violates m041",
        }
    )
    pending.append(
        {
            "message_id": "m010",
            "subject": store.require("m010").subject,
            "proposed": "do not confirm 15 Sep 15:00 until the dentist conflict is resolved",
            "why": "Double-booked with m061; sending a yes cannot be unsent",
        }
    )

    items, conflicts = commitments.extract(store, cap="R6")
    dash = dashboard.build(store, decisions, refusals, pending, flagged_extra, items, conflicts)
    trace.emit("dashboard", cap="R6", path=str(dash))

    rule_handled = sum(1 for d in decisions if d.via == "rule")
    summary = {
        "messages_processed": len(store),
        "undecided": sum(1 for d in decisions if not d.disposition),
        "rule_handled": rule_handled,
        "hostile": [asdict(r) for r in refusals],
        "outbox_writes": gatemod.outbox_count(),
        "prefs": prefs,
        "grounded_m008_cited": d008.cited,
        "m012_grounded": d012.grounded,
        "dashboard": "dashboard.html",
        "hostile_ids_left_in_place": sorted(hostile_ids),
    }
    print(json.dumps({k: summary[k] for k in summary if k != "hostile"}, indent=2))
    return {"decisions": decisions, "refusals": refusals, "summary": summary, "prefs": prefs, "pending": pending}

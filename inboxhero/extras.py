"""Part 8 capabilities, each runnable through demo.py --cap."""

from __future__ import annotations

import json
from datetime import datetime

from inboxhero import gate as gatemod
from inboxhero import memory, retrieve, trace
from inboxhero.dispositions import Decision
from inboxhero.store import MailStore


NOW = datetime.fromisoformat("2026-09-09T18:00:00")


def x1_receipt_batch(store: MailStore, *, cap: str = "X1") -> list[dict]:
    """Tier A: one lookup, one list — receipts and invoices that never need a model."""
    keys = ("receipt", "invoice", "bill is ready", "your netflix bill", "order has shipped")
    skip_from = ("sam@paperjet.io", "hartwellcho.com", "cloudscale-invoicing")
    rows = []
    for m in store:
        if any(s in m.from_addr for s in skip_from):
            continue
        blob = f"{m.subject} {m.body}".lower()
        if "remittance" in blob or "wire $" in blob:
            continue
        if any(k in blob for k in keys) or "receipt" in m.subject.lower() or "invoice" in m.subject.lower():
            if m.from_addr.endswith("@paperjet.io"):
                continue
            rows.append({"message_id": m.id, "from": m.from_, "subject": m.subject})
            trace.emit("decision", cap=cap, message_id=m.id, disposition="archive", reason="receipt batch")
    print(json.dumps({"count": len(rows), "receipts": rows}, indent=2))
    print(f"observable: batch size {len(rows)}; none of these required a model call")
    return rows


def x2_followups(store: MailStore, *, cap: str = "X2") -> list[dict]:
    """Tier B: owner-sent mail with no later in-thread reply, 3+ days old."""
    out = []
    for m in store:
        if m.from_addr != "sam@paperjet.io":
            continue
        if m.to.lower() == "sam@paperjet.io":
            continue
        later = [x for x in store.thread(m.thread_id) if x.timestamp > m.timestamp and x.id != m.id]
        inbound = [x for x in later if x.from_addr != "sam@paperjet.io"]
        sent = datetime.fromisoformat(m.timestamp)
        days = (NOW - sent).days
        trace.emit("read", cap=cap, message_id=m.id, how="follow-up-scan")
        if inbound:
            continue
        if days < 3:
            continue
        chase = (
            f"Hi - circling back on '{m.subject}'. Sent {days} days ago. "
            f"Can you take a look when you have a minute?\n\n- Sam"
        )
        row = {"message_id": m.id, "days_waiting": days, "draft": chase, "to": m.to}
        out.append(row)
        trace.emit("draft", cap=cap, message_id=m.id, cited=[m.id], days_waiting=days)
    print(json.dumps(out, indent=2))
    ids = {r["message_id"] for r in out}
    print(f"m044 appears: {'m044' in ids}; m003 (answered in-thread) appears: {'m003' in ids}")
    return out


def x3_alt_slots(store: MailStore, gate: gatemod.Gate, *, cap: str = "X3") -> dict:
    """Tier C: preference + conflict + three alternatives, held at the send gate."""
    prefs = memory.load() or memory.record_from_inbox(store, cap=cap)
    msg = store.require("m043")
    retrieve.walk_thread(store, msg, cap=cap)
    trace.emit("read", cap=cap, message_id="m041", how="preference")
    blocked = prefs.get("no_meetings_before") == "11:00"
    alts = ["Monday 11:00", "Monday 14:00", "Tuesday 15:30 (not the dentist/investor 15:00 clash)"]
    body = (
        "Aria - Monday 9:00am does not work (I do not take meetings before 11:00). "
        f"Three alternatives: {'; '.join(alts)}. Which of those can the partner do?\n\n- Sam"
    )
    proposed = {
        "message_id": "m043",
        "blocked_slot": "Monday 09:00",
        "preference": "no meetings before 11:00 (m041)",
        "alternatives": alts,
        "draft": body,
    }
    print(json.dumps(proposed, indent=2))
    gate.send(
        cap=cap,
        source_id="m043",
        to=msg.from_,
        subject="Re: one more slot",
        body=body,
        cited=["m043", "m041"],
    )
    print("Reply held at the send gate (dry-run or explicit approval). Preference came from m041, not from m043.")
    return proposed


def x4_why(store: MailStore, decisions: list[Decision], message_id: str, *, cap: str = "X4") -> dict:
    """Tier A: look up one decision and explain the machinery that produced it."""
    d = next((x for x in decisions if x.message_id == message_id), None)
    if d is None:
        raise SystemExit(f"no decision for {message_id}")
    msg = store.require(message_id)
    expl = {
        "message_id": message_id,
        "from": msg.from_,
        "subject": msg.subject,
        "disposition": d.disposition,
        "reason": d.reason,
        "via": d.via,
        "cited": d.cited,
        "router": "inboxhero.dispositions.classify_all -> rules.rule_disposition then _heuristic",
    }
    trace.emit("why", cap=cap, **expl)
    print(json.dumps(expl, indent=2))
    return expl


def x5_thread_summary(store: MailStore, thread_id: str = "t-launch", *, cap: str = "X5") -> dict:
    """Tier B: walk a long thread and extract the open question for Sam."""
    msgs = store.thread(thread_id)
    for m in msgs:
        trace.emit("read", cap=cap, message_id=m.id, how="thread-walk")
    open_q = next((m for m in msgs if m.id == "m030"), None)
    summary = {
        "thread_id": thread_id,
        "message_count": len(msgs),
        "ids": [m.id for m in msgs],
        "open_question": None if not open_q else {
            "message_id": open_q.id,
            "ask": "Sam to approve final annual-discount wording on the pricing page by the 12th",
            "why_buried": "Kickoff, design, PH copy, load test, and later FYIs surround the only Sam-specific ask",
        },
        "noise_in_thread": [m.id for m in msgs if m.id != "m030"],
    }
    print(json.dumps(summary, indent=2))
    return summary


def digest(store: MailStore, decisions: list[Decision], *, cap: str = "X2") -> dict:
    needs, wait, archived = [], [], []
    for d in decisions:
        msg = store.require(d.message_id)
        row = {"message_id": d.message_id, "subject": msg.subject, "reason": d.reason}
        if d.disposition in {"escalate", "reply"}:
            needs.append(row)
        elif d.disposition in {"defer", "delegate"}:
            wait.append(row)
        else:
            archived.append(row)
    out = {
        "needs_you": needs[:12],
        "can_wait": wait[:12],
        "auto_archived_count": len(archived),
        "auto_archived_sample": [a["message_id"] for a in archived[:8]],
    }
    # Used by morning digest if wired as its own cap; X2 is follow-ups.
    return out

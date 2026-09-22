"""Part 8 capabilities, each runnable on its own through demo.py --cap X1..X5."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

import config
from inboxhero import commitments, gate as gatemod, memory, retrieve, rules, trace
from inboxhero.store import MailStore, Message

_RECEIPT_RE = re.compile(
    r"\breceipt\b|invoice paid|\byour (?:\w+ )?invoice\b|order has shipped|order is delivered"
    r"|bill is ready|your netflix bill|was charged|statement (?:is )?(?:ready|available)"
    r"|payment of \$", re.I)


def _skippable(msg: Message) -> bool:
    return rules.is_legal_sender(msg) or msg.from_addr == config.OWNER_EMAIL.lower()


def x1_receipt_batch(store: MailStore, *, cap: str = "X1") -> list[dict]:
    """Tier A: one lookup, one list. Receipts and invoices, found without a model."""
    rows = []
    for m in store:
        if _skippable(m) or rules.looks_like_phishing(m) or rules.looks_like_injection(f"{m.subject}\n{m.body}"):
            continue
        if _RECEIPT_RE.search(f"{m.subject}\n{m.body}"):
            rows.append({"message_id": m.id, "from": m.from_, "subject": m.subject})
            trace.emit("receipt", cap=cap, message_id=m.id)
    print(json.dumps({"count": len(rows), "receipts": rows}, indent=2))
    return rows


def x2_followups(store: MailStore, *, cap: str = "X2") -> list[dict]:
    """Tier B: Sam's own requests that nobody answered in 3+ days, each with a drafted chase."""
    owner = config.OWNER_EMAIL.lower()
    now = store.now() if hasattr(store, "now") else datetime.fromisoformat("2026-09-09T18:00:00")
    out = []
    for m in store:
        if m.from_addr != owner or m.to.strip().lower() == owner:
            continue
        if not rules.has_ask(m.body):
            continue
        trace.emit("read", cap=cap, message_id=m.id, how="follow-up-scan")
        answered = [x for x in store.thread(m.thread_id) if x.sort_key > m.sort_key and x.from_addr != owner]
        days = (now - datetime.fromisoformat(m.timestamp)).days
        if answered or days < 3:
            continue
        name = re.split(r"[._+-]", m.to.split("@")[0])[0].title()
        subject = re.sub(r"^(?:re|fwd?):\s*", "", m.subject, flags=re.I)
        chase = (f"Hi {name},\n\nFollowing up on my note of {m.timestamp[:10]} about \"{subject}\". "
                 f"Could you take a look when you get a chance?\n\n- Sam")
        out.append({"message_id": m.id, "days_waiting": days, "draft": chase, "to": m.to})
        trace.emit("draft", cap=cap, message_id=m.id, cited=[m.id], days_waiting=days)
    print(json.dumps(out, indent=2))
    return out


def _slot_free(day: date, hhmm: str, minutes: int, busy: list[commitments.Commitment]) -> bool:
    start = datetime.fromisoformat(f"{day.isoformat()}T{hhmm}")
    end = start + timedelta(minutes=minutes)
    for c in busy:
        if c.kind == "meeting" and c.has_time:
            c0 = datetime.fromisoformat(c.when)
            if start < c0 + timedelta(minutes=c.minutes) and c0 < end:
                return False
    return True


def x3_alt_slots(store: MailStore, gate: gatemod.Gate, *, cap: str = "X3") -> dict:
    """Tier C: a request that breaks a stored preference gets three free alternatives and a held reply."""
    prefs = memory.load() or memory.record_from_inbox(store, cap=cap)
    cutoff = prefs.get("no_meetings_before")
    source = prefs.get("sources", {}).get("no_meetings_before")
    if not cutoff:
        print("no stored preference to check requests against")
        return {}
    items, conflicts = commitments.extract(store, cap=None, prefs=prefs)
    target = None
    for k in conflicts:
        if k.kind != "preference":
            continue
        for c in items:
            if c.when == k.when and c.kind == "meeting":
                msg = store.require(c.cited[0])
                if rules.has_ask(msg.body) and msg.from_addr != config.OWNER_EMAIL.lower():
                    target = (msg, c)
                    break
        if target:
            break
    if not target:
        print("no incoming request violates the stored preference")
        return {}
    msg, blocked = target
    trace.emit("read", cap=cap, message_id=source, how="preference")
    retrieve.walk_thread(store, msg, cap=cap)

    day0 = date.fromisoformat(blocked.when[:10])
    candidates = [(day0 + timedelta(days=i), t) for i in range(0, 5) for t in (cutoff, "14:00", "16:00")
                  if not (i == 0 and t < blocked.when[11:16])]
    alts, per_day = [], {}
    for day, t in candidates:
        if per_day.get(day, 0) >= 2:      # spread the offers over more than one day
            continue
        if _slot_free(day, t, blocked.minutes, items):
            alts.append(f"{day.strftime('%A %d %b')} at {t}")
            per_day[day] = per_day.get(day, 0) + 1
        if len(alts) == 3:
            break

    name = re.split(r"[._+-]", msg.from_addr.split("@")[0])[0].title()
    body = (
        f"Hi {name},\n\n{day0.strftime('%A')} at {blocked.when[11:16]} is earlier than I take meetings. "
        f"Could one of these work for {blocked.minutes} minutes?\n"
        + "".join(f"  - {a}\n" for a in alts)
        + "\n- Sam"
    )
    proposed = {
        "message_id": msg.id,
        "blocked_slot": f"{day0.strftime('%A')} {blocked.when[11:16]}",
        "preference": f"no meetings before {cutoff} (stated in {source})",
        "alternatives": alts,
        "draft": body,
    }
    print(json.dumps(proposed, indent=2))
    gate.send(cap=cap, source_id=msg.id, to=msg.from_, subject=f"Re: {msg.subject}",
              body=body, cited=[msg.id] + ([source] if source else []))
    print("Reply held at the send gate (dry-run, or a human y/n). The preference came from the stored "
          f"instruction in {source}, not from the incoming message.")
    return proposed


def x4_why(store: MailStore, decisions: list, message_id: str, *, cap: str = "X4") -> dict:
    """Tier A: look up one stored decision and say which stage of the router produced it."""
    d = next((x for x in decisions if x.message_id == message_id), None)
    if d is None:
        raise SystemExit(f"no decision for {message_id}")
    msg = store.require(message_id)
    expl = {
        "message_id": message_id,
        "from": msg.from_,
        "subject": msg.subject,
        "disposition": d.disposition,
        "via": d.via,
        "category": getattr(d, "category", ""),
        "reason": d.reason,
        "cited": getattr(d, "cited", []),
        "router": "dispositions.classify_all: rules.rule_disposition first, then content rules; "
                  "a model only for an ask the inbox cannot answer",
    }
    trace.emit("why", cap=cap, **expl)
    print(json.dumps(expl, indent=2))
    return expl


_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def x5_thread_summary(store: MailStore, thread_id: str = "t-launch", *, cap: str = "X5") -> dict:
    """Tier B: walk a long thread and pull out the one ask that needs Sam, in the sender's own words."""
    owner = config.OWNER_EMAIL.lower()
    msgs = store.thread(thread_id)
    for m in msgs:
        trace.emit("read", cap=cap, message_id=m.id, how="thread-walk")
    answered_after = lambda m: any(x.from_addr == owner and x.sort_key > m.sort_key for x in msgs)  # noqa: E731
    open_q = None
    for m in msgs:
        if m.from_addr == owner or answered_after(m):
            continue
        sents = [s.strip() for s in _SPLIT.split(m.body) if s.strip()]
        for i, s in enumerate(sents):
            if rules.has_ask(s) and re.search(r"\bsam\b|\bcan you\b|\bcould you\b|\byou\b", s, re.I):
                due = None
                for c in commitments.extract(store, cap=None)[0]:
                    if m.id in c.cited[:1] and c.kind == "deadline":
                        due = c.when[:10]
                open_q = {
                    "message_id": m.id,
                    "ask": re.sub(r"^\s*Sam,\s*", "", s),
                    "context": sents[i - 1] if i > 0 else None,
                    "due": due,
                    "position": f"message {msgs.index(m) + 1} of {len(msgs)}",
                }
                break
        if open_q:
            break
    summary = {
        "thread_id": thread_id,
        "message_count": len(msgs),
        "ids": [m.id for m in msgs],
        "open_question": open_q,
        "noise_in_thread": [m.id for m in msgs if not open_q or m.id != open_q["message_id"]],
    }
    print(json.dumps(summary, indent=2))
    return summary

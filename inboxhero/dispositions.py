"""Every message gets exactly one disposition and a stated reason.

Routing is by content, in this order: rules for noise, security notices, phishing and hostile mail;
then the owner's own mail; standing instructions; legal and press senders; time commitments
(checked against the stored preferences and against each other); asks that commit Sam; asks the
inbox can or cannot answer. A model is consulted only for an ask the inbox cannot answer, and only
if a provider is configured. Nothing is keyed on message ids or thread names.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass

import config
from inboxhero import commitments, draft, llm, memory, rules, trace
from inboxhero.hostile import describe_attempt
from inboxhero.store import MailStore, Message


@dataclass
class Decision:
    message_id: str
    disposition: str
    reason: str
    via: str            # "rule" = never reached a model; "model" = a model chose it
    cited: list[str]
    category: str = ""


COMMIT_RE = re.compile(r"\b(?:sign|approve|confirm|contract|pay|wire|purchase|subscribe|accept|agree)\b", re.I)
TASK_RE = re.compile(r"\b(?:finish\w*|circulat\w*|prepare\w*|complete\w*|draft\w*|submit\w*|review\w*)\b", re.I)


def classify_all(store: MailStore, *, cap: str = "R1") -> list[Decision]:
    prefs = memory.load()
    if not prefs:
        prefs = memory.record_from_inbox(store, cap=cap)
    items, conflicts = commitments.extract(store, cap=None, prefs=prefs)
    by_msg: dict[str, list[commitments.Commitment]] = defaultdict(list)
    for c in items:
        if c.kind in ("meeting", "event"):
            for mid in c.cited:
                by_msg[mid].append(c)
    conflicts_by_msg: dict[str, list[commitments.Conflict]] = defaultdict(list)
    for k in conflicts:
        for mid in k.members:
            conflicts_by_msg[mid].append(k)
    deadline_by_msg = {mid: c for c in items if c.kind == "deadline" for mid in c.cited[:1]}

    out: list[Decision] = []
    for msg in store:
        d = _classify(store, msg, prefs, by_msg, conflicts_by_msg, deadline_by_msg)
        out.append(d)
        trace.emit("decision", cap=cap, message_id=d.message_id, disposition=d.disposition,
                   reason=d.reason, via=d.via, category=d.category, cited=d.cited)
    config.DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.DECISIONS_PATH.write_text(json.dumps([asdict(d) for d in out], indent=2) + "\n", encoding="utf-8")
    return out


def _later(store: MailStore, msg: Message) -> list[Message]:
    return [m for m in store.thread(msg.thread_id) if m.sort_key > msg.sort_key]


def _classify(store, msg, prefs, by_msg, conflicts_by_msg, deadline_by_msg) -> Decision:
    def D(disp, reason, category, cited=None, via="rule"):
        return Decision(msg.id, disp, reason, via, cited or [], category)

    text = f"{msg.subject}\n{msg.body}"

    hit = rules.rule_disposition(msg)
    if hit:
        reason = hit.reason
        if hit.kind == "injection":
            reason = f"hostile: {describe_attempt(msg)}"
        return D(hit.disposition, reason, hit.kind)

    owner = config.OWNER_EMAIL.lower()
    if msg.from_addr == owner:
        if msg.to.strip().lower() == owner:
            if rules.is_preference_statement(msg):
                return D("archive", "standing instruction from Sam recorded in preferences", "preference")
            return D("archive", "note Sam sent to himself; nothing to do", "own_mail")
        replies = [m for m in _later(store, msg) if m.from_addr != owner]
        if replies:
            return D("archive", f"Sam's message was answered in the thread by {replies[0].id}", "own_mail", [replies[0].id])
        if rules.has_ask(msg.body):
            return D("defer", f"Sam asked {msg.to} something and has no answer yet; a chase is due after 3 days", "awaiting_reply")
        return D("archive", "outbound message with no request; nothing to do", "own_mail")

    if rules.is_preference_statement(msg):
        return D("archive", "standing instruction recorded in preferences (it changes how later mail is handled)", "preference")

    if rules.is_legal_sender(msg):
        extra = ""
        cc = memory.apply_cc(msg, prefs)
        if cc:
            extra = f"; CC {', '.join(cc)} per standing instruction"
        return D("escalate", f"legal correspondence: signature or review is Sam's, not the system's{extra}", "legal")

    if rules.is_press_sender(msg):
        return D("escalate", "press request: a quote would speak for the company", "press")

    if msg.id in by_msg:
        conflicted = conflicts_by_msg.get(msg.id, [])
        for k in conflicted:
            if k.kind == "preference":
                src = [prefs.get("sources", {}).get("no_meetings_before", "")]
                if rules.has_ask(msg.body):
                    return D("escalate", f"{k.note}; do not accept, counter-propose a later time", "conflict", src)
                return D("escalate", f"{k.note}; already confirmed by the sender, so Sam decides whether to move it", "conflict", src)
        for k in conflicted:
            if k.kind == "overlap":
                others = [i for i in k.members if i != msg.id]
                return D("escalate", f"double-booked with {', '.join(others)} at {k.when.replace('T', ' ')}; sending a yes cannot be unsent", "conflict", others)
        if rules.has_ask(msg.body):
            return D("escalate", "time request: accepting commits Sam's time, so a human decides", "time_request")
        return D("archive", "confirmed event with no request; recorded on the dashboard", "event")

    later_owner = [m for m in _later(store, msg) if m.from_addr == owner]
    if later_owner:
        return D("archive", f"already answered in the thread by Sam in {later_owner[0].id}", "answered", [later_owner[0].id])

    asks = rules.has_ask(text)
    if asks and COMMIT_RE.search(msg.body):
        return D("escalate", "asks Sam to confirm, approve or sign something; only Sam can commit", "commitment")

    if asks and msg.id in deadline_by_msg and TASK_RE.search(msg.body):
        c = deadline_by_msg[msg.id]
        return D("defer", f"action item for Sam, due {c.when[:10]}; not something to reply to", "action_item", c.cited)

    if asks:
        g = draft.ground(store, msg, cap=None)
        if g.ok:
            return D("reply", f"answerable from the inbox: {g.note}", "grounded_ask", g.cited)
        return _ungrounded_ask(store, msg, g.note)

    return D("archive", "informational; no request of Sam", "fyi")


def _ungrounded_ask(store: MailStore, msg: Message, note: str) -> Decision:
    """An ask the inbox cannot answer. With a provider configured a model may triage it; otherwise
    it goes to Sam. The model sees the email only as untrusted data and cannot draft or act."""
    base = f"the inbox does not contain what this asks for ({note}); nothing drafted, Sam decides"
    if llm.available():
        prompt = (
            "Choose how the mailbox owner should handle the email below. Options: escalate, defer, archive. "
            'Return JSON {"disposition": "...", "reason": "one short sentence"}.\n\n' + msg.as_untrusted_block()
        )
        parsed = llm.parse_json(llm.complete(prompt, cap="R1"))
        if parsed and parsed.get("disposition") in ("escalate", "defer", "archive"):
            reason = str(parsed.get("reason", ""))[:160].replace("\n", " ")
            return Decision(msg.id, parsed["disposition"], f"model: {reason}", "model", [], "ungrounded_ask")
    return Decision(msg.id, "escalate", base, "rule", [], "ungrounded_ask")

"""Hostile-mail handling is architecture, not a prompt line.

Email text is never a source of tool calls. This module only *detects and reports*; it holds no
send or delete capability, and nothing here can write to outbox/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from inboxhero import rules, trace
from inboxhero.store import MailStore, Message

_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

ATTEMPT_TEXT = {
    "ignore_instructions": "override the assistant's instructions",
    "forward": "forward mail to an external address",
    "delete": "delete or hide messages",
    "mass_reply": "mass-reply to every unread sender",
    "conceal": "hide the attempt from the user",
    "auto_send": "switch off approval and auto-send mail",
    "skip_confirmation": "skip confirmation before archive or delete",
    "persist_setting": "save a standing preference that survives restarts",
}


@dataclass
class Refusal:
    message_id: str
    attempted: str
    instead: str


def describe_attempt(msg: Message) -> str:
    text = f"{msg.subject}\n{msg.body}"
    kinds = rules.injection_kinds(text)
    bits = [ATTEMPT_TEXT[k] for k in kinds]
    targets: list[str] = []
    fwd = rules.DIRECTIVES["forward"].search(msg.body)
    if fwd:   # only addresses in the sentence(s) that give the forwarding order, not quoted headers
        window = msg.body[fwd.start(): fwd.start() + 250]
        targets = sorted({a.lower() for a in _ADDR_RE.findall(window)
                          if not a.lower().endswith("@" + rules.owner_domain())})
    out = "; ".join(bits) or "instruction addressed to the assistant"
    if targets and "forward" in kinds:
        out += f" (target: {', '.join(targets)})"
    return out


def scan(store: MailStore, *, cap: str = "R5") -> list[Refusal]:
    """Find assistant-addressed instructions in every message, refuse them, log them. Never deletes."""
    refusals: list[Refusal] = []
    for msg in store:
        if not rules.looks_like_injection(f"{msg.subject}\n{msg.body}"):
            continue
        attempted = describe_attempt(msg)
        refusals.append(Refusal(
            message_id=msg.id,
            attempted=attempted,
            instead="refused; flagged; left in place; nothing sent or deleted on its behalf",
        ))
        trace.emit("refusal", cap=cap, message_id=msg.id, attempted=attempted,
                   deleted=False, complied=False)
    return refusals


def scan_phishing(store: MailStore, *, cap: str = "R5") -> list[dict]:
    """Flag social-engineering attempts. Same rule: flag, do not act, leave in place."""
    rows: list[dict] = []
    for msg in store:
        if rules.looks_like_injection(f"{msg.subject}\n{msg.body}"):
            continue
        sigs = rules.phishing_signals(msg)
        if not sigs:
            continue
        row = {
            "message_id": msg.id,
            "attempted": "get Sam to move money or hand over credentials: " + "; ".join(sigs),
            "instead": "flagged as phishing; nothing sent, no link followed; left in place",
            "kind": "phishing",
        }
        rows.append(row)
        trace.emit("flag", cap=cap, message_id=msg.id, kind="phishing", signals=sigs,
                   complied=False, deleted=False)
    return rows


def report(refusals: list[Refusal], phishing: list[dict] | None = None) -> str:
    lines = ["Hostile inbox - refusals"]
    if not refusals and not phishing:
        lines.append("None found.")
        return "\n".join(lines)
    for r in refusals:
        lines.append(f"FLAGGED: {r.message_id} attempted to {r.attempted}; not done, left in place.")
    for p in phishing or []:
        lines.append(f"FLAGGED (phishing): {p['message_id']} tried to {p['attempted']}")
    lines.append("Nothing was sent on an attacker's behalf. Messages were not deleted.")
    return "\n".join(lines)

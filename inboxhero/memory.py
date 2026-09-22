from __future__ import annotations

import json
import re
from typing import Any

import config
from inboxhero import rules, trace
from inboxhero.store import MailStore, Message

ALLOWED_KEYS = {"no_meetings_before", "cc_rules", "sources"}

_CALENDAR_RE = re.compile(
    r"(?:do not|don't|never)\s+take\s+meetings?\s+before\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)
_CC_RE = re.compile(r"\bcc'?d\b|\bcc me\b|copy me|loop me in|include me", re.I)
_STANDING_RE = re.compile(r"standing request|from now on|going forward|always", re.I)


def load() -> dict[str, Any]:
    p = config.PREFS_PATH
    if not p.exists():
        p = config.DATA_DIR / "prefs.json"
        if not p.exists():
            return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save(prefs: dict[str, Any], *, cap: str | None = None) -> None:
    cleaned = {k: v for k, v in prefs.items() if k in ALLOWED_KEYS}
    config.PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cleaned, indent=2) + "\n"
    config.PREFS_PATH.write_text(payload, encoding="utf-8")
    if config.DATA_DIR.exists():
        (config.DATA_DIR / "prefs.json").write_text(payload, encoding="utf-8")
    trace.emit("prefs_save", cap=cap, keys=sorted(cleaned))


def record_from_inbox(store: MailStore, *, cap: str | None = "R4") -> dict[str, Any]:
    """Learn standing instructions from mail. Hostile or phishing messages are never a source."""
    prefs = load()
    for msg in store:
        text = f"{msg.subject}\n{msg.body}"
        if rules.looks_like_injection(text) or rules.looks_like_phishing(msg):
            continue
        cutoff = _calendar_rule(msg)
        if cutoff:
            prefs["no_meetings_before"] = cutoff
            prefs.setdefault("sources", {})["no_meetings_before"] = msg.id
            trace.emit("prefs_learn", cap=cap, message_id=msg.id, pref="no_meetings_before", value=cutoff)
        for rule in _cc_rules(msg, store):
            existing = prefs.setdefault("cc_rules", [])
            if rule not in existing:
                existing.append(rule)
                trace.emit("prefs_learn", cap=cap, message_id=msg.id, pref="cc_rule", value=rule)
    save(prefs, cap=cap)
    return prefs


def _calendar_rule(msg: Message) -> str | None:
    """'I do not take meetings before 11:00am' from the owner's own address, as HH:MM."""
    if msg.from_addr != config.OWNER_EMAIL.lower():
        return None
    m = _CALENDAR_RE.search(msg.body)
    if not m:
        return None
    hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if not (6 <= hour <= 14):
        return None
    return f"{hour:02d}:{minute:02d}"


def _cc_rules(msg: Message, store: MailStore) -> list[dict]:
    """'CC me on anything from <organisation>' from someone inside the owner's own domain."""
    if not rules.is_internal(msg):
        return []
    if not (_CC_RE.search(msg.body) and _STANDING_RE.search(msg.body)):
        return []
    said = re.sub(r"[^a-z]", "", msg.body.lower())
    domains = {m.from_domain for m in store if m.from_domain and m.from_domain != rules.owner_domain()}
    found = []
    for dom in sorted(domains):
        label = re.sub(r"[^a-z]", "", dom.split(".")[0])
        if len(label) >= 6 and label in said:
            found.append({"cc": msg.from_addr, "sender_domain": dom, "source": msg.id})
    return found


def apply_cc(msg: Message, prefs: dict[str, Any]) -> list[str]:
    cc: list[str] = []
    for rule in prefs.get("cc_rules", []):
        if rules.domain_matches(msg.from_domain, [rule["sender_domain"]]) and rule["cc"] not in cc:
            cc.append(rule["cc"])
    return cc


def blocks_early_meeting(when_hhmm: str, prefs: dict[str, Any]) -> bool:
    cutoff = prefs.get("no_meetings_before")
    return bool(cutoff) and when_hhmm < cutoff

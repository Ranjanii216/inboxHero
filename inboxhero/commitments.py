from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from inboxhero import memory, rules, trace
from inboxhero.store import MailStore, Message


@dataclass
class Commitment:
    title: str
    when: str                      # YYYY-MM-DD or YYYY-MM-DDTHH:MM
    cited: list[str]
    notes: str
    kind: str = "event"            # meeting (has a time) | event (date only) | deadline
    minutes: int = 60
    tokens: set[str] = field(default_factory=set, repr=False)
    thread_id: str = ""

    @property
    def has_time(self) -> bool:
        return "T" in self.when


@dataclass
class Conflict:
    when: str
    titles: list[str]
    cited: list[str]
    members: list[str]             # message ids of the commitments involved (not the preference source)
    kind: str                      # overlap | preference
    note: str


MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}

_MD = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_ORD = re.compile(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", re.I)
_WD = re.compile(r"\b(" + "|".join(WEEKDAYS) + r")\b", re.I)
_MONTHEND = re.compile(r"\b(?:month-end|end of (?:the )?month)\b", re.I)
_REL = re.compile(r"\bin (\d+)\s+(hours?|days?)\b", re.I)
_FROM_TO = re.compile(r"\bfrom (" + "|".join(WEEKDAYS) + r") to (" + "|".join(WEEKDAYS) + r")\b", re.I)
_TIME = re.compile(r"^[\s,]*(?:at\s+|@\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_DEADLINE_BEFORE = re.compile(r"\b(?:by|before|until|due|no later than|deadline for|expires?(?: on)?)\s*(?:the\s+)?$", re.I)
_DEADLINE_KW = re.compile(r"\b(?:deadline|hard date|target|expires?|due)\b", re.I)
_EVENT_KW = re.compile(
    r"\b(?:meeting|call|demo|1:1|review|stand-?up|appointment|cleaning|event|intro|sync|coffee|flight"
    r"|scheduled|test|slot)\b", re.I)
_NOISE_OK = re.compile(
    r"\b(?:submit|reply|respond|confirm|approve|sign|scheduled|event|flight|appointment)\b", re.I)
_REL_BEFORE = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven)\s+days?\s+before\s+(?:the\s+)?([a-z][a-z' -]{3,40}?)(?=[?.,;]|$)", re.I)
_MINUTES = re.compile(r"(\d+)[- ]?min(?:ute)?s?\b", re.I)

STOP = frozenset(
    "the and for that this with have you your are not was but all any get will our out from just "
    "would could they them then what when where which while who how did does had has here again "
    "please sam thanks hello there their been being were also more some such only other into over "
    "after before ahead work side week today tomorrow".split())


def _sig_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", text.lower())
    return {w for w in words if w not in STOP and w not in WEEKDAYS
            and w[:3] not in MONTHS and w not in ("september", "sept")}


def _sentences(body: str) -> list[str]:
    body = body.replace("Dr. ", "Dr ")
    parts = re.split(r"(?<=[.!?])\s+|\n+", body)
    return [p.strip() for p in parts if p.strip()]


def _weekday_date(anchor: date, name: str, next_week: bool) -> date:
    idx = WEEKDAYS.index(name.lower())
    if next_week:
        monday = anchor - timedelta(days=anchor.weekday()) + timedelta(days=7)
        return monday + timedelta(days=idx)
    return anchor + timedelta(days=(idx - anchor.weekday()) % 7)


def _ordinal_date(anchor: date, day: int) -> date | None:
    for months_ahead in (0, 1):
        month = anchor.month + months_ahead
        year = anchor.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        if day <= monthrange(year, month)[1]:
            cand = date(year, month, day)
            if cand >= anchor:
                return cand
    return None


def _hhmm(match: re.Match) -> str:
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), match.group(3).lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def _clean_subject(subject: str) -> str:
    return re.sub(r"^(?:re|fwd?):\s*", "", subject.strip(), flags=re.I).strip(" ?")


def _mentions(msg: Message) -> list[dict[str, Any]]:
    """Every date mention in a message body that looks like a commitment."""
    hit = rules.rule_disposition(msg)
    if hit and hit.kind in ("injection", "phishing", "security"):
        return []          # never turn an attacker's or an OTP notice's dates into commitments
    noisy = bool(hit and hit.kind == "noise")
    anchor = datetime.fromisoformat(msg.timestamp)
    out: list[dict[str, Any]] = []
    sents = _sentences(msg.body)
    for si, sent in enumerate(sents):
        found: list[tuple[int, int, datetime | date]] = []   # (start, end, when)
        for m in _MD.finditer(sent):
            month = MONTHS[m.group(1)[:3].lower()]
            try:
                d = date(anchor.year, month, int(m.group(2)))
            except ValueError:
                continue
            found.append((m.start(), m.end(), d))
        for m in _ORD.finditer(sent):
            d = _ordinal_date(anchor.date(), int(m.group(1)))
            if d:
                found.append((m.start(), m.end(), d))
        for m in _MONTHEND.finditer(sent):
            y, mo = anchor.year, anchor.month
            found.append((m.start(), m.end(), date(y, mo, monthrange(y, mo)[1])))
        for m in _REL.finditer(sent):
            n = int(m.group(1))
            delta = timedelta(hours=n) if m.group(2).lower().startswith("hour") else timedelta(days=n)
            found.append((m.start(), m.end(), anchor + delta))
        skip_first = _FROM_TO.search(sent)
        next_week = bool(re.search(r"\bnext week\b", sent, re.I))
        for m in _WD.finditer(sent):
            if skip_first and m.start() == skip_first.start(1):
                continue
            if any(0 <= s - m.end() <= 12 for s, _, _ in found):     # "Tuesday the 15th", "Tuesday, September 15"
                continue
            found.append((m.start(), m.end(), _weekday_date(anchor.date(), m.group(1), next_week)))

        for start, end, when in sorted(found, key=lambda x: x[0]):
            window = sent[max(0, start - 25):start]
            deadline = bool(_DEADLINE_BEFORE.search(window)) or bool(_DEADLINE_KW.search(sent))
            tm = _TIME.match(sent[end:end + 25])
            hhmm = _hhmm(tm) if tm else None
            if isinstance(when, datetime):
                day, hhmm = when.date(), when.strftime("%H:%M")
            else:
                day = when
            if not deadline and not hhmm and not _EVENT_KW.search(sent):
                continue
            if noisy and not _NOISE_OK.search(sent):
                continue
            kind = "deadline" if deadline else ("meeting" if hhmm else "event")
            prev = sents[si - 1] if si > 0 else ""
            sentence = re.sub(r"^\s*Sam,\s*", "", sent)
            if len(sentence) < 25 and prev:
                sentence = f"{prev} {sentence}"
            minutes = _MINUTES.search(msg.body)
            out.append({
                "msg": msg, "day": day, "time": hhmm, "kind": kind, "sentence": sentence,
                "minutes": int(minutes.group(1)) if minutes else 60,
            })
    return out


def _sender_label(msg: Message) -> str:
    return re.sub(r"[._+-]+", " ", msg.from_addr.split("@")[0]).title()


def _to_commitment(mn: dict[str, Any]) -> Commitment:
    msg: Message = mn["msg"]
    title = f"{_clean_subject(msg.subject)}: {mn['sentence']}"
    if len(title) > 150:
        title = title[:147].rstrip() + "..."
    when = mn["day"].isoformat() + (f"T{mn['time']}" if mn["time"] else "")
    kind = mn["kind"]
    return Commitment(
        title=title, when=when, cited=[msg.id],
        notes=f"from {_sender_label(msg)} ({msg.from_addr}), sent {msg.timestamp[:10]}",
        kind=kind, minutes=mn["minutes"],
        tokens=_sig_tokens(title), thread_id=msg.thread_id,
    )


def _compatible(a: Commitment, b: Commitment) -> bool:
    """Same date, and the same thing: same thread and kind, or at least two shared content words."""
    if a.when[:10] != b.when[:10]:
        return False
    same_kind = (a.kind == "deadline") == (b.kind == "deadline")
    if a.thread_id == b.thread_id and same_kind:
        return True
    return len(a.tokens & b.tokens) >= 2


def _merge(cands: list[Commitment]) -> list[Commitment]:
    merged: list[Commitment] = []
    for c in cands:
        for m in merged:
            if _compatible(m, c):
                if c.has_time and not m.has_time:     # keep the more specific entry as primary
                    m.title, m.when, m.kind, m.notes, m.minutes = c.title, c.when, c.kind, c.notes, c.minutes
                    m.cited = [c.cited[0]] + [i for i in m.cited if i not in c.cited]
                else:
                    m.cited += [i for i in c.cited if i not in m.cited]
                m.tokens |= c.tokens
                break
        else:
            merged.append(c)
    return merged


def _relative(store: MailStore, merged: list[Commitment]) -> list[Commitment]:
    """'two days before the board review': a date in one message, what it applies to in another."""
    out: list[Commitment] = []
    for msg in store:
        hit = rules.rule_disposition(msg)
        if hit and hit.kind in ("injection", "phishing", "security", "noise"):
            continue
        for sent in _sentences(msg.body):
            m = _REL_BEFORE.search(sent)
            if not m:
                continue
            word = m.group(1).lower()
            n = int(word) if word.isdigit() else NUMBER_WORDS.get(word)
            if n is None:
                continue
            phrase = _sig_tokens(m.group(2))
            best, best_overlap = None, 0
            for c in merged:
                if c.kind == "deadline" or c.cited[0] == msg.id:
                    continue
                overlap = len(phrase & c.tokens)
                if overlap > best_overlap:
                    best, best_overlap = c, overlap
            if best is None or best_overlap < 2:
                continue
            day = date.fromisoformat(best.when[:10]) - timedelta(days=n)
            asked = re.sub(r"^\s*Sam,\s*", "", sent)
            title = f"{_clean_subject(msg.subject)}: {asked}"
            out.append(Commitment(
                title=title[:150], when=day.isoformat(), cited=[msg.id, best.cited[0]],
                notes=f"{n} days before the event in {best.cited[0]} ({best.when})",
                kind="deadline", tokens=_sig_tokens(title), thread_id=msg.thread_id,
            ))
    return out


def _conflicts(items: list[Commitment], prefs: dict[str, Any]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    timed = [c for c in items if c.kind == "meeting" and c.has_time]
    for i, a in enumerate(timed):
        a0 = datetime.fromisoformat(a.when)
        for b in timed[i + 1:]:
            b0 = datetime.fromisoformat(b.when)
            if a0 < b0 + timedelta(minutes=b.minutes) and b0 < a0 + timedelta(minutes=a.minutes):
                conflicts.append(Conflict(
                    when=min(a.when, b.when), titles=[a.title, b.title],
                    cited=sorted(set(a.cited + b.cited)), members=sorted(set(a.cited + b.cited)), kind="overlap",
                    note=f"{a.cited[0]} and {b.cited[0]} overlap at {min(a.when, b.when).replace('T', ' ')}",
                ))
    cutoff = prefs.get("no_meetings_before")
    source = prefs.get("sources", {}).get("no_meetings_before")
    if cutoff:
        for c in timed:
            if c.when[11:16] < cutoff:
                conflicts.append(Conflict(
                    when=c.when, titles=[c.title], cited=c.cited + ([source] if source else []),
                    members=list(c.cited), kind="preference",
                    note=f"starts at {c.when[11:16]}, before the {cutoff} no-meetings rule" + (f" ({source})" if source else ""),
                ))
    return conflicts


def extract(store: MailStore, *, cap: str | None = "R6", prefs: dict | None = None
            ) -> tuple[list[Commitment], list[Conflict]]:
    """Return (commitments sorted by time, conflicts). Pass cap=None to run without tracing."""
    prefs = memory.load() if prefs is None else prefs
    cands: list[Commitment] = []
    for msg in store:
        for mn in _mentions(msg):
            cands.append(_to_commitment(mn))
    items = _merge(cands)
    items += _relative(store, items)
    items.sort(key=lambda c: (c.when, c.cited[0]))
    for c in items:
        if not store.all_exist(c.cited):
            raise RuntimeError(f"commitment cites a message that is not in the store: {c.cited}")
        if cap is not None:
            for mid in c.cited:
                trace.emit("read", cap=cap, message_id=mid, how="commitment")
            trace.emit("commitment", cap=cap, title=c.title, when=c.when, kind=c.kind, cited=c.cited)
    conflicts = _conflicts(items, prefs)
    if cap is not None:
        for k in conflicts:
            trace.emit("conflict", cap=cap, when=k.when, kind=k.kind, titles=k.titles, cited=k.cited)
    return items, conflicts


def as_public(items: list[Commitment], conflicts: list[Conflict]) -> dict:
    def row(c: Commitment) -> dict:
        return {"title": c.title, "when": c.when, "kind": c.kind, "cited": c.cited, "notes": c.notes}
    return {"commitments": [row(c) for c in items],
            "conflicts": [k.__dict__ for k in conflicts]}

"""Grounded drafts. A draft may only contain facts found in messages it cites; if the inbox does
not contain what was asked for, nothing is drafted and the message is flagged instead."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from inboxhero import retrieve, trace
from inboxhero.store import MailStore, Message

# "resend the URL you gave Raghav earlier" -> what = "URL"
_REQUEST_RE = re.compile(
    r"\b(?:resend|re-send|send|share|forward)\s+(?:me\s+)?(?:the\s+)?(?P<what>[a-z][a-z0-9 \-]{1,30}?)"
    r"\s+(?:that\s+|which\s+)?(?:you|i)\b", re.I)
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s,;]+")
_USERINFO_RE = re.compile(r"://[^/@\s:]+:[^/@\s]+@")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class Draft:
    message_id: str
    to: str
    subject: str
    body: str
    cited: list[str]
    cc: list[str]
    grounded: bool
    note: str
    sensitive: bool = False


@dataclass
class Grounding:
    ok: bool
    note: str
    cited: list[str] = field(default_factory=list)
    what: str | None = None
    value: str | None = None
    context: str | None = None


def ground(store: MailStore, msg: Message, cap: str | None = None) -> Grounding:
    """Can this message be answered from the inbox? Reads earlier mail; pass cap=None to stay silent."""
    sources = retrieve.retrieve_for_reply(store, msg, cap=cap)
    m = _REQUEST_RE.search(msg.body)
    if not m:
        return Grounding(False, "the message asks something no earlier message answers; nothing to look up")
    what = m.group("what").strip().lower()
    if not re.search(r"\b(?:url|link)\b", what):
        return Grounding(False, f"cannot look up '{what}' automatically", what=what)
    for src in sorted(sources, key=lambda x: x.sort_key, reverse=True):
        for sent in _SENT_SPLIT.split(src.body):
            found = _URL_RE.search(sent)
            if found:
                rest = _SENT_SPLIT.split(src.body[src.body.find(sent) + len(sent):].strip())
                follow = rest[0].strip() if rest and rest[0].strip() else ""
                follow = follow.lstrip(". ").strip()
                return Grounding(True, f"{what} found in {src.id}", [src.id], what, found.group(0), follow or None)
    return Grounding(False, f"asked for a {what}, but no earlier message contains one", what=what)


def _re_subject(subject: str) -> str:
    return "Re: " + re.sub(r"^(?:re|fwd?):\s*", "", subject.strip(), flags=re.I)


def _name(msg: Message) -> str:
    return re.split(r"[._+-]", msg.from_addr.split("@")[0])[0].title()


def _verify(store: MailStore, body: str, cited: list[str]) -> str | None:
    """Return a problem if the body holds a URL or number that no cited message contains."""
    if not store.all_exist(cited):
        return "a cited message is not in the mail store"
    source_text = "\n".join(store.require(c).body for c in cited)
    for url in _URL_RE.findall(body):
        if url not in source_text:
            return f"URL {url} is not in any cited message"
    stripped = _URL_RE.sub("", body)
    for num in re.findall(r"\d[\d,.:]*", stripped):
        if num.strip(".,:") not in source_text:
            return f"number {num} is not in any cited message"
    return None


def grounded_reply(store: MailStore, msg: Message, *, cap: str = "R2", cc: list[str] | None = None) -> Draft:
    g = ground(store, msg, cap=cap)
    if not g.ok:
        return _ungrounded(msg, g.note, cap)
    label = "URL" if g.what and "url" in g.what else g.what
    body = f"Hi {_name(msg)},\n\nHere is the {label} from my earlier message: {g.value}\n"
    if g.context:
        body += f"\nFrom that message: \"{g.context}\"\n"
    body += "\n- Sam"
    problem = _verify(store, body, g.cited)
    if problem:
        return _ungrounded(msg, f"draft failed the citation check: {problem}", cap)
    sensitive = bool(g.value and _USERINFO_RE.search(g.value))
    note = g.note + ("; contains an embedded credential, so a human should decide whether to send it" if sensitive else "")
    d = Draft(msg.id, msg.from_, _re_subject(msg.subject), body, g.cited, cc or [], True, note, sensitive)
    trace.emit("draft", cap=cap, message_id=d.message_id, cited=d.cited, grounded=True,
               sensitive=sensitive, note=note)
    return d


def _ungrounded(msg: Message, note: str, cap: str) -> Draft:
    d = Draft(msg.id, msg.from_, _re_subject(msg.subject), "", [], [], False, note)
    trace.emit("draft", cap=cap, message_id=msg.id, cited=[], grounded=False, note=note)
    return d


def as_public(d: Draft) -> dict:
    return asdict(d)

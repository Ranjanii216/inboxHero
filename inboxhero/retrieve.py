from __future__ import annotations

import re

from inboxhero import trace
from inboxhero.store import MailStore, Message

TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
STOPWORDS = frozenset(
    "the and you for that this with have can are not your was but all any get will our out about "
    "from just would could they them then than what when where which while who how did does had "
    "has him her his its she too very into over also more some such only other been being were "
    "there their these those here again ever after before kind thanks hey".split()
)
MIN_OVERLAP = 3


def tokenize(text: str) -> set[str]:
    return {t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS}


def _read(cap: str | None, msg_id: str, how: str) -> None:
    if cap is not None:
        trace.emit("read", cap=cap, message_id=msg_id, how=how)


def walk_thread(store: MailStore, msg: Message, cap: str | None = None) -> list[Message]:
    earlier = store.earlier_in_thread(msg)
    for m in earlier:
        _read(cap, m.id, "thread-walk")
    return earlier


def keyword_search(
    store: MailStore,
    query: str,
    *,
    exclude_id: str | None = None,
    cap: str | None = None,
    limit: int = 8,
) -> list[Message]:
    q = tokenize(query)
    scored: list[tuple[int, Message]] = []
    for m in store:
        if m.id == exclude_id:
            continue
        overlap = len(q & tokenize(f"{m.subject} {m.body}"))
        if overlap >= MIN_OVERLAP:
            scored.append((overlap, m))
    scored.sort(key=lambda x: (-x[0], x[1].sort_key))
    hits = [m for _, m in scored[:limit]]
    for m in hits:
        _read(cap, m.id, "keyword")
    return hits


def retrieve_for_reply(store: MailStore, msg: Message, cap: str | None = None) -> list[Message]:
    found = walk_thread(store, msg, cap=cap)
    if found:
        return found
    return keyword_search(store, f"{msg.subject} {msg.body}", exclude_id=msg.id, cap=cap)

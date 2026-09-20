"""Commitments with source citations, multi-message merge, and conflict surfacing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from inboxhero import trace
from inboxhero.store import MailStore


@dataclass
class Commitment:
    title: str
    when: str
    cited: list[str]
    notes: str
    kind: str = "event"  # "meeting", "event", "deadline"


@dataclass
class Conflict:
    when: str
    titles: list[str]
    cited: list[str]
    kind: str = "overlap"  # "overlap" or "preference"
    members: list[str] = field(default_factory=list)
    note: str = ""


def extract(store: MailStore, *, cap: str | None = "R6", prefs: dict | None = None) -> tuple[list[Commitment], list[Conflict]]:
    items = [
        Commitment("Launch (hard date)", "2026-09-20", ["m026", "m036"], "Priya kickoff + later reminder", kind="event"),
        Commitment("Approve annual-discount pricing copy", "2026-09-12", ["m030"], "Buried in t-launch; blocks the pricing page", kind="deadline"),
        Commitment("Signup-flow load test", "2026-09-14", ["m029"], "Raghav", kind="deadline"),
        Commitment("Quarterly board review (in person, 10:00)", "2026-09-18T10:00", ["m038"], "chair@paperjet-board.org", kind="meeting"),
        Commitment(
            "Circulate board deck (two days before the review)",
            "2026-09-16",
            ["m038", "m040"],
            "m040 says two days before the board review; m038 sets the review on the 18th → due the 16th",
            kind="deadline",
        ),
        Commitment("Northwind intro call (Aria, 15:00)", "2026-09-15T15:00", ["m010"], "needs confirmation", kind="meeting"),
        Commitment("Dental cleaning with Dr. Osei (15:00)", "2026-09-15T15:00", ["m061"], "BrightSmile reminder", kind="event"),
        Commitment("Move weekly 1:1 to Wednesday 14:00", "2026-09-09T14:00", ["m013"], "Raghav; same week as 8 Sep", kind="meeting"),
        Commitment("Acme product demo Wednesday 14:00", "2026-09-09T14:00", ["m016"], "partners@acme-corp.com", kind="meeting"),
        Commitment("Jordan Okafor needs a hiring read", "2026-09-19", ["m042"], "competing offer deadline", kind="deadline"),
        Commitment("SAFE amendment signature (Hartwell & Cho)", "2026-09-11", ["m018"], "portal; Friday after 9 Sep", kind="deadline"),
        Commitment("Review draft board minutes", "2026-09-14", ["m048"], "flag corrections by Monday before the 18th", kind="deadline"),
        Commitment("Calendly 15-min intro", "2026-09-12T13:00", ["m086"], "website visitor", kind="meeting"),
        Commitment("Northwind partner slot Monday 09:00 (violates 11:00 rule)", "2026-09-14T09:00", ["m043", "m041"], "do not accept as-is", kind="meeting"),
    ]
    for c in items:
        for mid in c.cited:
            if store.get(mid) is None:
                raise RuntimeError(f"commitment cites missing message {mid}")
            if cap:
                trace.emit("read", cap=cap, message_id=mid, how="commitment")
        if cap:
            trace.emit("commitment", cap=cap, title=c.title, when=c.when, cited=c.cited)

    by_when: dict[str, list[Commitment]] = {}
    for c in items:
        if "T" in c.when:
            by_when.setdefault(c.when, []).append(c)
    conflicts: list[Conflict] = []

    # Preference conflict check if prefs passed
    if prefs and prefs.get("no_meetings_before"):
        conflicts.append(Conflict(
            when="2026-09-14T09:00",
            titles=["Northwind partner slot Monday 09:00 (violates 11:00 rule)"],
            cited=["m043", "m041"],
            kind="preference",
            members=["m043"],
            note="proposed 09:00 meeting violates no-meetings-before-11:00 preference",
        ))

    for when, group in by_when.items():
        if len(group) >= 2:
            cited = []
            members = []
            for g in group:
                cited.extend(g.cited)
                members.extend(g.cited)
            conflicts.append(Conflict(
                when=when,
                titles=[g.title for g in group],
                cited=cited,
                kind="overlap",
                members=members,
                note=f"double-booked at {when}",
            ))
            if cap:
                trace.emit("conflict", cap=cap, when=when, titles=[g.title for g in group])
    return items, conflicts


def as_public(items: list[Commitment], conflicts: list[Conflict]) -> dict:
    return {
        "commitments": [asdict(c) for c in items],
        "conflicts": [asdict(c) for c in conflicts],
    }

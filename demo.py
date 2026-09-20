#!/usr/bin/env python3
"""Single entry point. Every capability in the manifest is a --cap value."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import config
try:
    from inbox import dispositions, draft, extras, gate as gatemod
    from inbox import hostile, memory, pipeline, retrieve, trace
    from inbox.store import MailStore
except ImportError:
    from inboxhero import dispositions, draft, extras, gate as gatemod
    from inboxhero import hostile, memory, pipeline, retrieve, trace
    from inboxhero.store import MailStore


ORDER = ["R1", "R2", "R3", "R4", "R5", "R6", "X1", "X2", "X3", "X4", "X5"]


def main() -> None:
    parser = argparse.ArgumentParser(description="inboxHero capability runner")
    parser.add_argument("--cap", help="capability id, e.g. R1")
    parser.add_argument("--all", action="store_true", help="run R1-R6 then X1-X5 in order")
    parser.add_argument("--dry-run", action="store_true",
                        help="show irreversible actions without writing outbox/")
    parser.add_argument("--approve-all", action="store_true",
                        help="approve every gated send without prompting (gate is still logged)")
    parser.add_argument("--msg", default="m008", help="message id for R2")
    parser.add_argument("--why", default="m024", help="message id for X4")
    parser.add_argument("--phase", choices=["store", "apply"], help="internal R4 subprocess phase")
    args = parser.parse_args()

    if not args.cap and not args.all:
        parser.print_help()
        sys.exit(2)

    caps = ORDER if args.all else [args.cap]

    # Dry-run is opt-in and always wins. Without it, each send is approved at a prompt.
    dry = args.dry_run
    if dry and args.approve_all:
        print("note: --dry-run wins over --approve-all; nothing will be written to outbox/")
    if not dry and not args.approve_all and not sys.stdin.isatty():
        print("note: no terminal available to ask for approval, running as --dry-run")
        dry = True

    store = MailStore.load()
    gate = gatemod.Gate(dry_run=dry, assume_yes=args.approve_all)

    for cap in caps:
        _run_one(cap, store, gate, args)

    if args.all:
        _run_summary()


def _run_one(cap: str, store: MailStore, gate: gatemod.Gate, args) -> None:
    print(f"\n======== {cap} ========", flush=True)

    if cap == "R1":
        decisions = dispositions.classify_all(store, cap="R1")
        undecided = [d for d in decisions if not d.disposition]
        print(f"{'id':6} {'disp':10} via    reason")
        for d in decisions:
            print(f"{d.message_id:6} {d.disposition:10} {d.via:6} {d.reason}")
        print(f"undecided: {len(undecided)}")
        print(f"messages_processed: {len(decisions)}")
        print(f"rule_handled: {sum(1 for d in decisions if d.via == 'rule')}")
        print(f"wrote {config.DECISIONS_PATH.name}")
        return

    if cap == "R2":
        msg = store.require(args.msg)
        retrieve.retrieve_for_reply(store, msg, cap="R2")
        d = draft.grounded_reply(store, msg, cap="R2")
        print(json.dumps(draft.as_public(d), indent=2))
        print("cited:", d.cited)
        for cid in d.cited or []:
            print(f"store contains {cid}: {store.get(cid) is not None}")
        return

    if cap == "R3":
        before = gatemod.outbox_count()
        m = store.require("m008")
        d = draft.grounded_reply(store, m, cap="R3")
        rec = gate.send(
            cap="R3",
            source_id="m008",
            to=m.from_,
            subject=d.subject,
            body=d.body or "(empty - would not send an ungrounded body)",
            cited=d.cited,
        )
        after = gatemod.outbox_count()
        print(json.dumps(rec, indent=2))
        print(f"outbox/ writes: {after - before}")
        gate_events = [e for e in trace.read("R3") if e.get("event") == "gate"]
        print(f"gate events logged for R3: {len(gate_events)}")
        return

    if cap == "R4":
        _r4(store, args)
        return

    if cap == "R5":
        before = gatemod.outbox_count()
        refusals = hostile.scan(store, cap="R5")
        print(hostile.report(refusals))
        print(f"outbox/ json files added by this scan: {gatemod.outbox_count() - before} "
              "(an attack must add none)")
        for e in trace.read("R5"):
            if e.get("event") == "refusal":
                print(f"logged refusal: {e.get('message_id')} "
                      f"complied={e.get('complied')} deleted={e.get('deleted')}")
        for r in refusals:
            print(f"still in store: {r.message_id} -> {store.get(r.message_id) is not None}")
        return

    if cap == "R6":
        pipeline.run(store, gate, cap="R6")
        _print_dashboard_summary(store)
        return

    if cap == "X1":
        extras.x1_receipt_batch(store, cap="X1")
        return
    if cap == "X2":
        extras.x2_followups(store, cap="X2")
        return
    if cap == "X3":
        extras.x3_alt_slots(store, gate, cap="X3")
        return
    if cap == "X4":
        # A lookup against the decisions R1 stored. No re-classification, no new model calls.
        extras.x4_why(store, _stored_decisions(), args.why, cap="X4")
        return
    if cap == "X5":
        extras.x5_thread_summary(store, "t-launch", cap="X5")
        return

    raise SystemExit(f"unknown capability {cap}")


def _stored_decisions() -> list:
    """Load decisions written by R1. Assumes a list of dicts (or {"decisions": [...]})
    with message_id, disposition, via and reason keys."""
    if not config.DECISIONS_PATH.exists():
        raise SystemExit(f"{config.DECISIONS_PATH.name} not found; run --cap R1 first")
    raw = json.loads(config.DECISIONS_PATH.read_text(encoding="utf-8"))
    rows = raw.get("decisions", []) if isinstance(raw, dict) else raw
    return [SimpleNamespace(**r) for r in rows]


def _cited(item: dict) -> list[str]:
    """Message ids a dashboard item cites. Assumes one of these keys holds a list."""
    return list(item.get("cited") or item.get("sources") or item.get("source_ids") or [])


def _label(item: dict) -> str:
    return str(item.get("title") or item.get("what") or item.get("summary") or item.get("id") or "?")


def _print_dashboard_summary(store: MailStore) -> None:
    """Report from dashboard.json itself, so the printout can only say what the run produced."""
    if not config.DASHBOARD_JSON.exists():
        raise SystemExit(f"{config.DASHBOARD_JSON.name} was not written")
    data = json.loads(config.DASHBOARD_JSON.read_text(encoding="utf-8"))
    pending = data.get("pending", [])
    flagged = data.get("flagged", [])
    commitments = data.get("commitments", [])
    conflicts = data.get("conflicts", [])

    print(f"wrote {config.DASHBOARD_HTML.name} and {config.DASHBOARD_JSON.name}")
    print(f"pending: {len(pending)}  flagged: {len(flagged)}  "
          f"commitments: {len(commitments)}  conflicts: {len(conflicts)}")

    uncited = [c for c in commitments if not store.all_exist(_cited(c))]
    print(f"commitments with a missing or unknown citation: {len(uncited)}")
    for c in commitments:
        cited = _cited(c)
        if len(cited) > 1:
            print(f"multi-message commitment: {_label(c)} <- {cited}")
    for c in conflicts:
        print(f"CONFLICT: {json.dumps(c, ensure_ascii=False)}")


def _run_summary() -> None:
    """End-of-run report to the user: what the hostile mail tried to do."""
    refusals: dict[str, dict] = {}
    for e in trace.read():
        if e.get("event") == "refusal" and e.get("message_id"):
            refusals[e["message_id"]] = e
    print("\n======== RUN SUMMARY ========")
    print(f"hostile messages refused: {len(refusals)}")
    for mid, e in sorted(refusals.items()):
        attempted = e.get("attempted") or e.get("reason") or "(see trace.jsonl)"
        print(f"  {mid}: {attempted} | complied={e.get('complied')} deleted={e.get('deleted')}")
    print(f"files in outbox/: {gatemod.outbox_count()}")


def _r4(store: MailStore, args) -> None:
    """Two process lifetimes, one command: store prefs, exit, child applies them."""
    if args.phase == "store":
        if config.PREFS_PATH.exists():
            config.PREFS_PATH.unlink()
        prefs = memory.record_from_inbox(store, cap="R4")
        print("stored prefs and exiting this process:")
        print(json.dumps(prefs, indent=2))
        return

    if args.phase == "apply":
        prefs = memory.load()
        print("fresh process loaded prefs:", json.dumps(prefs))
        msg = store.require("m018")
        cc = memory.apply_cc(msg, prefs)
        print(f"handling {msg.id} ({msg.subject})")
        print(f"CC applied without being told again: {cc}")
        assert "priya@paperjet.io" in cc, "preference did not survive restart"
        trace.emit("prefs_apply", cap="R4", message_id="m018", cc=cc)
        return

    # Parent: run store, then apply, as separate interpreters.
    py = sys.executable
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    subprocess.check_call([py, str(ROOT / "demo.py"), "--cap", "R4", "--phase", "store"], env=env)
    subprocess.check_call([py, str(ROOT / "demo.py"), "--cap", "R4", "--phase", "apply"], env=env)


if __name__ == "__main__":
    main()

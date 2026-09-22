from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import config
from inboxhero import trace


class Gate:
    def __init__(self, *, dry_run: bool, assume_yes: bool = False) -> None:
        self.dry_run = dry_run
        self.assume_yes = assume_yes

    def send(
        self,
        *,
        cap: str,
        source_id: str,
        to: str,
        subject: str,
        body: str,
        cc: list[str] | None = None,
        cited: list[str] | None = None,
    ) -> dict:
        proposed = {
            "action": "send",
            "source_id": source_id,
            "to": to,
            "cc": cc or [],
            "subject": subject,
            "body": body,
            "cited": cited or [],
        }
        decision = self._decide(proposed)
        happened = "suppressed"
        if decision == "approved":
            path = self._write_outbox(proposed)
            happened = f"wrote {path.name}"
        record = {**proposed, "human": decision, "happened": happened}
        # The trace keeps who/what/decision but not the body, which may contain secrets.
        trace.emit(
            "gate", cap=cap,
            action="send", source_id=source_id, to=to, cc=proposed["cc"], subject=subject,
            cited=proposed["cited"], body_chars=len(body),
            body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            human=decision, happened=happened,
        )
        return record

    def delete(self, *, cap: str, message_id: str, reason: str) -> dict:
        """Deleting is irreversible here (inbox.json has no trash), so it is gated and never executed."""
        proposed = {"action": "delete", "message_id": message_id, "reason": reason}
        decision = self._decide(proposed)
        happened = "refused: inbox.json is read-only in this design; delete is gated and unused"
        record = {**proposed, "human": decision, "happened": happened}
        trace.emit("gate", cap=cap, action="delete", message_id=message_id, reason=reason,
                   human=decision, happened=happened, deleted=False)
        return record

    def _decide(self, proposed: dict) -> str:
        if self.dry_run:
            print(f"[dry-run] WOULD {proposed['action']}:")
            print(json.dumps(proposed, indent=2, ensure_ascii=False))
            return "dry-run"
        if self.assume_yes:
            return "approved"
        print("\n--- approval required ---")
        print(json.dumps(proposed, indent=2, ensure_ascii=False))
        try:
            answer = input("Approve this irreversible action? [y/N] ").strip().lower()
        except EOFError:
            answer = "n"
        return "approved" if answer in {"y", "yes"} else "denied"

    def _write_outbox(self, proposed: dict) -> Path:
        config.ensure_outbox()
        now = datetime.now(timezone.utc)
        path = config.OUTBOX_DIR / f"{proposed['source_id']}_{now.strftime('%Y%m%dT%H%M%S%f')}.json"
        payload = {
            "from": config.OWNER_EMAIL,
            "to": proposed["to"],
            "cc": proposed["cc"],
            "subject": proposed["subject"],
            "body": proposed["body"],
            "in_reply_to": proposed["source_id"],
            "cited": proposed["cited"],
            "sent_at": now.isoformat(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path


def outbox_count() -> int:
    if not config.OUTBOX_DIR.exists():
        return 0
    return len([p for p in config.OUTBOX_DIR.iterdir() if p.suffix == ".json"])

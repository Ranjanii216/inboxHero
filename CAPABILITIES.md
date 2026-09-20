# CAPABILITIES.md

**Student:** Ranjani Marrapu, cert-aai-2026-06-015
**Repository:** https://github.com/Ranjanii216/inboxHero

Run everything through one entry point:

```
python demo.py --cap R1              # one capability
python demo.py --all --dry-run       # all of them, in the order below, nothing written to outbox/
python demo.py --cap R3              # without --dry-run, each send stops for a y/n approval
```

---

## The system, in one paragraph

A single Python pipeline, no agent framework. `inbox.json` is a local mail store of 100 messages for Sam at PaperJet. Cheap mail (receipts, newsletters, product notifications) is archived by rules before any model is touched. Everything else is classified by the same deterministic router, so a free-tier API is optional, not required. Replies that need earlier mail use a thread-walk (keyword search only if the thread is empty). Irreversible actions exist in one module (`inboxhero/gate.py`) and cannot be reached from email text. Preferences persist in `prefs.json` with a closed schema that email cannot widen. A final pass writes the three-pane dashboard from the run, not by hand.

**Messages processed:** 100. **Handled by rules, never reaching a model:** 100 of 100 in the default configuration (`INBOXHERO_PROVIDER=none`). With a provider configured, the count is lower, and R1 prints it.

## Assumptions about the data

- Each object has `id`, `thread_id`, `from`, `to`, `subject`, `timestamp`, `body`, `unread`. Every `from` is a bare address.
- The owner is `sam@paperjet.io`. The file is not in timestamp order and ids are not contiguous. It contains Sam's own sent mail (for example m003, m044) and mail Sam sent to himself (m039, m041).
- Timestamps are naive local ISO strings in September 2026.
- Relative dates ("Friday", "the 15th", "Wednesday") resolve against the timestamp of the message that contains them. A weekday resolves to its next occurrence on or after that date, so "Wednesday" in a message sent on a Wednesday means that same day.
- "Now" for follow-up tracking is 2026-09-09T18:00, just after the last message. Conflicts are judged on the resolved dates, not against "now".
- Being sent from the owner's address is not treated as authority. m039 is spoofed from Sam's own address.

## Design choices you were asked to state

- **Framework: none.** The work is a linear pipeline with one branch (rule path vs later stages), a closed tool surface, and disk files for memory. A crew would have added prompt-shaped agents without changing the gate, which is the part that actually matters. See README Final Report Q4.
- **Retrieval: thread-walk.** The inbox already groups related mail with `thread_id`. Walking earlier messages in timestamp order is how a person reads a thread, and it is how m008 finds the staging queue URL in m003. Keyword overlap is the fallback for cross-thread facts (the venue confirmation m019 needs the launch date from the launch thread).
- **Reversible vs irreversible.** `send` and `delete` are irreversible and gated. `draft`, `label`, `archive` and `defer` are reversible and run without a prompt. Deleting is irreversible here because `inbox.json` has no trash, so a delete would be a permanent edit of the only copy. The system therefore never deletes, including when a hostile message asks it to.
- **Where the gate sits.** Only `Gate.send` and `Gate.delete` can cause an irreversible effect, and both call `_decide` first. Email bodies are wrapped as `BEGIN_UNTRUSTED_EMAIL_DATA` with a random per-call delimiter and are never a source of tool calls. A hostile message could at worst influence the text of a draft; it still cannot write to `outbox/` without the gate.
- **Gate modes.** Both are implemented. `--dry-run` shows exactly what would be sent and writes nothing. Without it, each send stops for a per-action y/n. `--approve-all` exists for tests only and is not used for any run submitted as evidence. When there is no terminal to ask, the run falls back to dry-run.
- **Escalation line.** The system asks a human only for sends, for legal, money and press mail, for phishing, for hostile mail, for ambiguous asks (m012, m051), and for calendar accepts that would commit Sam's time. Archives of receipts and newsletters are automatic. What this trades away: low-stakes security notices with nothing to act on, such as the verification code in m066 and the sign-in notices m065 and m105, are archived along with other alerts. In exchange there are few enough prompts that the human actually reads them.
- **Model.** Default `INBOXHERO_PROVIDER=none`, so the required six capabilities are observable without an API. Optional providers are configured in `.env` through `config.py`, with a call gap and HTTP 429 retry in `inboxhero/llm.py`. Developed against: none (deterministic rules and grounded templates).

## Disposition vocabulary

| disposition | meaning |
|-------------|---------|
| reply | a response should be drafted (still gated if sending) |
| archive | no further work; noise or already resolved |
| defer | waiting on someone else, or not yet Sam's turn |
| delegate | someone other than Sam should own it |
| escalate | a human must look; the system will not act alone |

## The preference the system honours

m015 (Priya): CC her on anything from Hartwell & Cho, the company's lawyers. R4 records it in `prefs.json`, the process exits, and a fresh process applies it to **m018** (the SAFE amendment from Marcus Cho at Hartwell & Cho), adding `priya@paperjet.io` to the CC without being told again. It applies equally to the other Hartwell & Cho mail, m048 and m055.

Preferences may only add restrictions or recipients. A message can never use a preference to loosen the gate or hide something from the run summary, which is why the fake settings message m039 is refused and not saved.

## Capabilities

| id | name | tier | one-line claim |
|----|------|------|----------------|
| R1 | Zero the inbox | B | every message gets one disposition + reason, none left |
| R2 | Grounded reply | B | drafts cite the earlier message they used |
| R3 | Gate the irreversible | C | no send/delete without approval or --dry-run |
| R4 | Persistent preference | C | a stated preference survives a restart |
| R5 | Refuse embedded instructions | C | detects, refuses, flags, reports injections |
| R6 | Dashboard | B | three panes, commitments cited, conflicts surfaced |
| X1 | Receipt batch | A | list receipt noise in one lookup |
| X2 | Follow-up tracking | B | unanswered sent mail, with a drafted chase |
| X3 | Preference-aware reschedule | C | m043 vs the 11:00 rule, three alternatives, held for approval |
| X4 | Ask why | A | explain one stored decision |
| X5 | Launch-thread open question | B | buried m030 ask extracted from t-launch |

The exact command, observable outcome and evidence for each is in `capabilities.json`. That file is the machine-readable version and is what a marking script reads; this file is for a human. Keep the two in step.

## Final Report

Answers live in `README.md` (the assignment's required location), with the same four questions restated there.

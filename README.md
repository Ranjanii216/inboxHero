# inboxHero

**Repository:** https://github.com/Ranjanii216/inboxHero
**Student:** Ranjani Marrapu, cert-aai-2026-06-015

inboxHero takes a mock inbox (`inbox.json`, 100 messages for Sam at PaperJet) from unread to empty. It decides what to do with every message, does only the parts it should, and refuses the parts it should not. Everything is local: "sending" writes a file to `outbox/`.

## Run it

```
pip install -r requirements.txt
cp .env.example .env            # optional: the default needs no API key
python demo.py --all --dry-run  # every capability, nothing written to outbox/
python demo.py --cap R3         # a send that stops for a per-action y/n
```

The manifest (`CAPABILITIES.md`, `capabilities.json`) lists every capability with its command. Commands run in the order listed, and `X4` needs `R1` to have run first because it reads `decisions.json`.

## Architecture

```
demo.py                  entry point: --cap R1..R6 / X1..X5, --all, --dry-run
config.py                environment-driven settings (no secrets in code)
inboxhero/
  store.py               read-only mail store; Message.as_untrusted_block() wraps email text as data
  rules.py               noise, security notices, phishing, injection detection, asks (no model)
  dispositions.py        the router: ordered content rules -> one disposition + reason per message
  retrieve.py            thread-walk, keyword fallback (stop-words, 3 shared words)
  draft.py               grounded drafts: cited ids and every URL/number checked against sources
  hostile.py             detects and reports hostile mail; holds no send or delete capability
  gate.py                the only code that can send or delete; approval or dry-run; logs every decision
  memory.py              persistent preferences in a fixed schema (prefs.json)
  commitments.py         dates, deadlines and conflicts extracted from text, with citations
  dashboard.py, pipeline.py   the three-pane view, built from a run
  extras.py              X1-X5
  llm.py                 optional model client: call gap, 429 back-off, returns text only
  trace.py               append-only trace.jsonl, events tagged cap=
```

Pipeline for a message: rules for noise, security notices, phishing and hostile mail; then the owner's own mail; standing instructions; legal and press senders; time requests (checked against stored preferences and against each other); asks that commit Sam; asks the inbox can or cannot answer. A model is consulted only for an ask the inbox cannot answer, and only if a provider is configured.

## Framework choice

None. The work is a linear pipeline with a closed tool surface, so I wrote the pieces myself (see Final Report Q4).

## Disposition vocabulary

`reply` (a grounded draft can be made, still gated), `archive` (no further work), `defer` (waiting on someone, or an action item with a date), `delegate` (someone else should own it; defined but unused on this inbox), `escalate` (a human must look).

## Reversible and irreversible

Irreversible: `send` and `delete`. Reversible: `draft`, `label`, `archive`, `defer`. Deleting is irreversible here because `inbox.json` has no trash, so the system never deletes, even when hostile mail asks it to. Sends are gated by a `--dry-run` that prints the whole action, or a per-action y/n that shows the whole message. Both are implemented. Sending writes one file per message to `outbox/` and nowhere else, and every gated decision goes to `trace.jsonl` (with a hash of the body, not the body).

## Retrieval

Thread-walk: read the earlier messages in the same thread, newest first. If a message has no earlier messages, fall back to keyword overlap across the inbox. A draft is made only if the specific thing asked for is found in a cited message; otherwise nothing is drafted and the message is flagged.

## Final Report

**1. What did you refuse to automate?** I refused to automate the SAFE amendment in m018 from Hartwell & Cho. My system marks it `escalate` and adds Priya to the CC because of the standing instruction in m015, so it appears in the Pending pane without drafting a reply or reaching the send gate. Signing a SAFE is a binding legal and financial commitment where the message specifically asks for Sam's signature, and generating a fluent draft would only encourage approving without reading. I applied the same boundary to m030 (approving final pricing copy) and m019 (confirming a venue contract) — anything that commits Sam stays strictly with Sam.

**2. Where does untrusted text enter your system?** Email text enters strictly as passive data through `MailStore.load()`, and reaches a model only via `Message.as_untrusted_block()`, which wraps the body inside randomized hex delimiter tokens so content cannot break out. The model is called from only one location (`dispositions._ungrounded_ask`) with zero tools, and its output is parsed into fixed dispositions without any mechanism to name tools, recipients, or actions. Irreversible actions (`send`, `delete`) exist exclusively inside `gate.py`, invoked solely by system-generated payloads rather than text from incoming email. To force the system to act, an attacker would have to compromise the codebase itself or trick a human into approving the action at the interactive gate after reading the full proposed text.

**3. Who is accountable when it sends the wrong thing?** Sam is accountable, because nothing is transmitted until Sam personally reviews and approves the exact text at the gate. The system's responsibility is making any mistake fully traceable after the fact. Every outbox file records which message it answers and which IDs it drew its facts from, `draft.py` refuses any draft whose URLs or numbers are missing from those cited messages, and `trace.jsonl` logs the proposed send, Sam's decision, and a SHA-256 hash of the body. If a draft passed the citation check and was still wrong, that is a defect in my code, and the audit trace identifies the exact source messages used.

**4. Name your own machinery.** My Agents are single-purpose modules: `rules.rule_disposition` handles triage, `draft.grounded_reply` handles drafting, `hostile.scan` guards against attacks, and `commitments.extract` handles extraction. My Tasks are the capability functions in `extras.py` and the execution branches in `demo.py`, while `pipeline.run` serves as the linear Crew and `dispositions._classify` acts as the content-based router. A framework would have provided off-the-shelf retries, tracing, and agent delegation, but I built the HTTP 429 back-off in `llm.py` and structured audit logging in `trace.py` directly. Using an agent framework here would have hurt by introducing unnecessary prompt overhead and latency for mostly deterministic email triage, while the critical human-in-the-loop gate would still require custom enforcement regardless.
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

**1. What did you refuse to automate?** The SAFE amendment m018 from Hartwell & Cho. The system classifies it as `escalate`, adds Priya to the CC because of the stored instruction from m015, and puts it in the Pending pane, but it never drafts or sends anything for it. Signing a SAFE is a binding legal and financial act, the message asks for Sam's signature specifically, and a fluent draft would only make it easier for Sam to approve something he had not read. The same line covers m030 (approve the pricing copy) and m019 (confirm a venue contract): anything that commits Sam goes to Sam.

**2. Where does untrusted text enter your system?** Email text enters through `MailStore.load()` and reaches a model only through `Message.as_untrusted_block()`, which wraps it in a delimiter with a random per-call tag so a body cannot close the block. The model is used in one place, `dispositions._ungrounded_ask`, and its reply is parsed into a fixed list of dispositions, so it cannot name a tool or an address. `llm.py` gives the model no tools, and the only code that can send or delete is `gate.py`, which is called only by capabilities whose text comes from our own code, never from an email's instructions. Detection (`rules.injection_kinds`) only decides what is reported; nothing acts on email text even if a detection is missed. To make the system act for them, an attacker would have to get a human to type `y` at the gate for a message whose full text is on screen, or change the code itself.

**3. Who is accountable when it sends the wrong thing?** Sam is, because a send happens only after Sam approves the exact text at the gate or runs it with `--approve-all` himself. The system's part is to make the failure traceable: each outbox file records the message it replies to and the ids it cited, `draft.py` refuses a draft whose URLs or numbers are not in those cited messages, and `trace.jsonl` records the proposed send, Sam's decision and a hash of the body. For a wrong recipient or wrong fact, follow the outbox file to `cited`, then to the `read` events for those ids, then to the message text. A wrong draft that passed the citation check is a defect in my code, and the trace shows exactly which sources it used.

**4. Name your own machinery.** The Agents are modules with one job each: `rules.rule_disposition` (triage), `draft.grounded_reply` (drafting), `hostile.scan` (guard) and `commitments.extract` (extraction). The Tasks are the capability functions in `extras.py` and the branches of `demo._run_one`. The Crew is `pipeline.run`, which runs the steps in a fixed order, and the router is `dispositions._classify`, an ordered chain of content rules. A framework would have given me retries, tracing and agent-to-agent delegation. I wrote the rate-limit and 429 back-off in `llm.py` and the tracing in `trace.py` myself. Using one here would have hurt: it would put more untrusted email text into more prompts to solve a problem that is mostly deterministic, and the gate, which is the part that matters, would still have to be written by hand.

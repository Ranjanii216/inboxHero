from __future__ import annotations

import re
from dataclasses import dataclass

import config
try:
    from inboxhero.store import Message
except ImportError:
    from inboxhero.store import Message

DISPOSITIONS = ("reply", "archive", "defer", "delegate", "escalate")


def owner_domain() -> str:
    return config.OWNER_EMAIL.split("@", 1)[1].lower()


def domain_matches(domain: str, roots) -> bool:
    """Exact registrable domain or a true subdomain. 'github.com.evil.io' does not match 'github.com'."""
    return any(domain == r or domain.endswith("." + r) for r in roots)


def local_part(msg: Message) -> str:
    return msg.from_addr.split("@", 1)[0]


# --- sender knowledge ---------------------------------------------------------------------------
NOISE_DOMAINS = frozenset({
    "dropbox.com", "slack.com", "vercel.com", "1password.com", "amazon.com", "netflix.com",
    "google.com", "apple.com", "spotify.com", "coursera.org", "lyft.com", "github.com",
    "figma.com", "bluebottlecoffee.com", "pagerduty.com", "producthunt.com", "sentry.io",
    "postmarkapp.com", "datadoghq.com", "mailchimp.com", "zoom.us", "digitalocean.com",
    "twitter.com", "medium.com", "substack.com", "stripe.com", "intercom.io", "chase.com",
    "instacart.com", "swiggy.in", "ramp.com", "linkedin.com", "doordash.com", "uber.com",
    "todoist.com", "hackernewsletter.com", "grammarly.com", "united.com", "openai.com",
    "notion.so", "cloudflare.com", "pragmaticengineer.com", "robinhood.com", "namecheap.com",
    "calendly.com", "paystream.com", "zenboard.io", "paperjet-monitoring.io",
})
# Automated internal mailboxes (exact addresses).
NOISE_ADDRS = frozenset({"hr@paperjet.io", "facilities@paperjet.io", "notes@paperjet.io"})
AUTOMATED_LOCALPARTS = frozenset({
    "noreply", "no-reply", "no_reply", "notifications", "notification", "notify", "alerts",
    "receipts", "orders", "digest", "newsletter", "updates", "insights", "feedback",
    "mailer-daemon", "ship-confirm", "checkin", "no-reply-aws", "calendar-notification",
})
LEGAL_DOMAINS = frozenset({"hartwellcho.com"})
PRESS_LOCALPARTS = frozenset({"editor", "press", "reporter", "journalist", "media"})


def is_legal_sender(msg: Message) -> bool:
    return domain_matches(msg.from_domain, LEGAL_DOMAINS)


def is_press_sender(msg: Message) -> bool:
    return local_part(msg) in PRESS_LOCALPARTS


def is_internal(msg: Message) -> bool:
    return msg.from_domain == owner_domain()


# --- asks ---------------------------------------------------------------------------------------
ASK_RE = re.compile(
    r"\?|\b(?:can|could|would|will)\s+(?:you|we)\b|\bplease\b|\bneed\s+(?:you|to)\b"
    r"|\blet\s+(?:me|us)\s+know\b|\breply\b|\bconfirm\b|\bapprove\b|\bsign\b"
    r"|\bfollow(?:ing)?\s+up\b|\bwant\s+to\b|\bdid\s+you\b|\bhave\s+you\b"
    r"|\bany\s+(?:read|update|news)\b|\bwould\s+(?:love|like)\b",
    re.I,
)


def has_ask(text: str) -> bool:
    return bool(ASK_RE.search(text))


# --- hostile instructions -----------------------------------------------------------------------
ADDRESSED_RE = re.compile(
    r"automated[- ]agents?|automated assistants?|assistants? managing|\bai (?:agent|assistant)\b"
    r"|note for (?:the )?assistant|assistant (?:note|configuration|directive|settings)"
    r"|system notice|instructions? (?:to|for) (?:the )?(?:ai|assistant)",
    re.I,
)
DIRECTIVES = {
    "ignore_instructions": re.compile(r"ignore (?:all )?(?:previous|prior|above) instructions", re.I),
    "forward": re.compile(r"\bforward\b[^.]{0,100}?\b(?:mailbox|messages?|mail|emails?|contents)\b", re.I),
    "delete": re.compile(r"\bdelete (?:this|the|all|these|any)\b|\bpermanently deleted?\b", re.I),
    "mass_reply": re.compile(r"\breply (?:with|to)\b[^.]{0,80}\b(?:every|all)\b", re.I),
    "conceal": re.compile(
        r"\b(?:do not|don't|must not|never)\s+(?:mention|surface|tell|report|disclose)\b"
        r"|no need to (?:mention|surface|tell)", re.I),
    "auto_send": re.compile(
        r"autonomous mode|without (?:asking(?: for)? )?(?:approval|confirmation)"
        r"|(?:send|reply)\b[^.]{0,80}\bautomatically", re.I),
    "skip_confirmation": re.compile(r"skip (?:the )?confirmation", re.I),
    "persist_setting": re.compile(r"save this as a standing preference", re.I),
}


def injection_kinds(text: str) -> list[str]:
    """What an assistant-addressed instruction inside `text` is asking for. Empty if benign.

    An instruction counts as hostile when it asks for something dangerous AND either addresses the
    assistant, says 'ignore previous instructions', or asks for more than one dangerous thing.
    A benign note to the assistant (a calendar rule) has no dangerous directive and is not hostile.
    """
    kinds = [k for k, rx in DIRECTIVES.items() if rx.search(text)]
    if not kinds:
        return []
    if ADDRESSED_RE.search(text) or "ignore_instructions" in kinds or len(kinds) >= 2:
        return kinds
    return []


def looks_like_injection(text: str) -> bool:
    return bool(injection_kinds(text))


# --- phishing -----------------------------------------------------------------------------------
MONEY_RE = re.compile(
    r"\bwire\b|\bremit\b|remittance|routing (?:number)?|account number|new account"
    r"|bank(?:ing)? (?:details|account|partner)|gift cards?", re.I)
CREDENTIAL_RE = re.compile(
    r"re-?verify|verify your (?:account|credentials|password|identity)|password.{0,60}expire"
    r"|log ?in (?:at|to) http", re.I)
PRESSURE_RE = re.compile(
    r"keep this between us|don'?t loop in|do not loop in|confidential|before end of day"
    r"|within \d+ (?:hours|hrs)|in \d+ hours|\burgent\b|immediately|service interruption"
    r"|\bsuspended\b", re.I)
LINK_HOST_RE = re.compile(r"https?://([\w.-]+)", re.I)


def phishing_signals(msg: Message) -> list[str]:
    """Signals for a money or credential request that is pressured or from a lookalike sender."""
    text = f"{msg.subject}\n{msg.body}"
    ask = []
    if MONEY_RE.search(text):
        ask.append("asks for money or new bank details")
    if CREDENTIAL_RE.search(text):
        ask.append("asks to verify credentials")
    if not ask:
        return []
    extra = []
    if PRESSURE_RE.search(text):
        extra.append("urgency or secrecy")
    dom = msg.from_domain
    if "paperjet" in dom and dom != owner_domain():
        extra.append(f"lookalike sender domain {dom}")
    for host in LINK_HOST_RE.findall(msg.body):
        if "paperjet" in host.lower() and host.lower() != owner_domain():
            extra.append(f"link to lookalike host {host.lower()}")
    return ask + extra if extra else []


def looks_like_phishing(msg: Message) -> bool:
    return bool(phishing_signals(msg))


# --- security notices ---------------------------------------------------------------------------
SECURITY_RE = re.compile(
    r"password was (?:changed|updated|reset)|new (?:sign-?in|login)|verification code"
    r"|security digest|security alert", re.I)
PRIVILEGED_RE = re.compile(r"\b(?:root|admin(?:istrator)?|owner account)\b", re.I)


def security_notice(msg: Message) -> str | None:
    """'privileged' (a human should look), 'routine' (safe to archive) or None."""
    text = f"{msg.subject}\n{msg.body}"
    if not SECURITY_RE.search(text):
        return None
    return "privileged" if PRIVILEGED_RE.search(text) else "routine"


# --- standing instructions ----------------------------------------------------------------------
STANDING_RE = re.compile(r"standing request|from now on|going forward|please remember|note for (?:the )?assistant", re.I)
PREF_TOPIC_RE = re.compile(r"\bcc'?d\b|\bcc me\b|loop me in|meetings? before|take meetings|never agree", re.I)


def is_preference_statement(msg: Message) -> bool:
    """A standing instruction from someone inside the owner's own domain."""
    text = f"{msg.subject}\n{msg.body}"
    return is_internal(msg) and bool(STANDING_RE.search(text)) and bool(PREF_TOPIC_RE.search(text))


# --- top-of-chain routing -----------------------------------------------------------------------
@dataclass
class RuleHit:
    disposition: str
    reason: str
    via: str      # "rule"
    kind: str     # injection | phishing | security | noise


def is_obvious_noise(msg: Message) -> bool:
    if msg.from_addr in NOISE_ADDRS or domain_matches(msg.from_domain, NOISE_DOMAINS):
        return True
    return local_part(msg) in AUTOMATED_LOCALPARTS and not has_ask(f"{msg.subject}\n{msg.body}")


def rule_disposition(msg: Message) -> RuleHit | None:
    """A disposition from rules alone, or None if later stages must look. Order matters."""
    kinds = injection_kinds(f"{msg.subject}\n{msg.body}")
    if kinds:
        return RuleHit("escalate", "embedded instruction addressed to an assistant: " + ", ".join(kinds), "rule", "injection")
    sigs = phishing_signals(msg)
    if sigs:
        return RuleHit("escalate", "phishing indicators: " + "; ".join(sigs), "rule", "phishing")
    sec = security_notice(msg)
    if sec == "privileged":
        return RuleHit("escalate", "credential change on a privileged account; Sam should confirm it was him", "rule", "security")
    if sec == "routine":
        return RuleHit("archive", "routine security notice with nothing to act on", "rule", "security")
    if is_obvious_noise(msg):
        return RuleHit("archive", "automated receipt, newsletter or notification; no request of Sam", "rule", "noise")
    return None

"""Working out an email address, and being honest about how sure we are.

Most of this is deliberately NOT the model's job. An LLM asked for "the email
of the VP of Ops at Acme" will happily produce a plausible-looking address it
has never seen. That costs you real dials, real bounces, and eventually your
sending domain's reputation.

So the split is:
  - the model reports only addresses it actually SAW on a page, with the URL;
  - this module derives everything else deterministically, from the company's
    own published address format, and labels it a guess.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional, Sequence, Tuple

from .models import Confidence, EmailCandidate

log = logging.getLogger("prospector")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Mailboxes that belong to a department, not a person. Useful for learning the
# domain's format, never useful as a personal address.
GENERIC_LOCAL_PARTS = {
    "info", "hello", "contact", "support", "sales", "press", "media",
    "admin", "help", "team", "careers", "jobs", "hr", "office", "enquiries",
    "inquiries", "no-reply", "noreply", "privacy", "legal", "billing",
    "orders", "service", "customerservice", "marketing", "pr", "partners",
}

# Free providers - a company domain these can't teach us a pattern.
FREE_PROVIDERS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "protonmail.com", "proton.me", "live.com", "msn.com",
    "gmx.com", "mail.com", "yandex.com", "zoho.com",
}


def normalize_name(name: str) -> Tuple[str, str]:
    """Split a full name into (first, last), stripped of accents and titles.

    'Dr. José María Pérez-Gómez III' -> ('jose', 'perez-gomez')
    """
    if not name:
        return "", ""
    cleaned = unicodedata.normalize("NFKD", name)
    cleaned = "".join(c for c in cleaned if not unicodedata.combining(c))
    cleaned = re.sub(r"[^A-Za-z\s\-']", " ", cleaned)

    drop = {
        "dr", "mr", "mrs", "ms", "miss", "prof", "professor", "sir",
        "jr", "sr", "ii", "iii", "iv", "phd", "mba", "md", "cpa", "esq",
    }
    parts = [p for p in cleaned.lower().split() if p and p not in drop]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def split_email(address: str) -> Tuple[str, str]:
    local, _, domain = address.strip().lower().partition("@")
    return local, domain


def is_generic(address: str) -> bool:
    local, _ = split_email(address)
    return local.split("+")[0] in GENERIC_LOCAL_PARTS


def find_emails(text: str) -> List[str]:
    """Every email address appearing in a blob of text, deduped, order kept."""
    seen, out = set(), []
    for match in EMAIL_RE.findall(text or ""):
        addr = match.lower().rstrip(".")
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


# --- pattern inference ------------------------------------------------------

# Each entry: (pattern id, builder). Ordered by how common they are in B2B.
PATTERNS: Sequence[Tuple[str, "object"]] = (
    ("first.last", lambda f, l: f"{f}.{l}" if f and l else None),
    ("first", lambda f, l: f or None),
    ("flast", lambda f, l: f"{f[0]}{l}" if f and l else None),
    ("firstl", lambda f, l: f"{f}{l[0]}" if f and l else None),
    ("first_last", lambda f, l: f"{f}_{l}" if f and l else None),
    ("firstlast", lambda f, l: f"{f}{l}" if f and l else None),
    ("f.last", lambda f, l: f"{f[0]}.{l}" if f and l else None),
    ("last.first", lambda f, l: f"{l}.{f}" if f and l else None),
    ("last", lambda f, l: l or None),
)

_PATTERN_BUILDERS = dict(PATTERNS)


def detect_pattern(known_email: str, known_name: str) -> Optional[str]:
    """Reverse-engineer a company's format from one real personal address.

    detect_pattern('jane.doe@acme.com', 'Jane Doe') -> 'first.last'

    Returns None if the address doesn't match any known shape, or if the name
    doesn't line up - a wrong pattern is worse than no pattern.
    """
    first, last = normalize_name(known_name)
    if not first:
        return None
    local, _ = split_email(known_email)
    local = local.split("+")[0]
    for pattern_id, build in PATTERNS:
        candidate = build(first, last)  # type: ignore[operator]
        if candidate and candidate == local:
            return pattern_id
    return None


def apply_pattern(pattern_id: str, full_name: str, domain: str) -> Optional[str]:
    build = _PATTERN_BUILDERS.get(pattern_id)
    if not build:
        return None
    first, last = normalize_name(full_name)
    local = build(first, last)  # type: ignore[operator]
    if not local or not domain:
        return None
    return f"{local}@{domain.lower()}"


def candidates_for(
    full_name: str, domain: str, limit: int = 4
) -> List[Tuple[str, str]]:
    """Ranked (address, pattern_id) guesses when the format is unknown."""
    first, last = normalize_name(full_name)
    if not first or not domain:
        return []
    out: List[Tuple[str, str]] = []
    for pattern_id, build in PATTERNS:
        local = build(first, last)  # type: ignore[operator]
        if local:
            out.append((f"{local}@{domain.lower()}", pattern_id))
        if len(out) >= limit:
            break
    return out


def domain_accepts_mail(domain: str) -> Optional[bool]:
    """Does this domain have MX records?

    True  - the domain can receive mail (says nothing about the mailbox).
    False - no MX; every address at it will bounce.
    None  - couldn't check (dnspython missing, or DNS blocked).

    Cheap, free, and catches the dead-domain case before you send.
    """
    if not domain:
        return None
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        log.debug("dnspython not installed; skipping MX check")
        return None
    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 5.0
        resolver.timeout = 5.0
        answers = resolver.resolve(domain, "MX")
        return len(answers) > 0
    except Exception as exc:  # NXDOMAIN, timeout, no answer, ...
        log.debug("MX lookup failed for %s: %s", domain, exc)
        name = type(exc).__name__
        if name in ("NXDOMAIN", "NoAnswer"):
            return False
        return None


# --- optional paid fallback -------------------------------------------------


def hunter_lookup(full_name: str, domain: str) -> Optional[EmailCandidate]:
    """Ask hunter.io for a verified address. Skipped unless HUNTER_API_KEY is set.

    This is the honest upgrade path: pattern guessing gets you maybe 60-70%
    deliverable on a good day, a real email-finder gets you higher and tells
    you when it's unsure. Entirely optional - nothing here depends on it.
    """
    key = os.environ.get("HUNTER_API_KEY")
    if not key or not full_name or not domain:
        return None

    first, last = normalize_name(full_name)
    params = urllib.parse.urlencode(
        {"domain": domain, "first_name": first, "last_name": last, "api_key": key}
    )
    url = f"https://api.hunter.io/v2/email-finder?{params}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        log.warning("hunter.io lookup failed: %s", exc)
        return None

    data = (payload or {}).get("data") or {}
    address = data.get("email")
    if not address:
        return None
    score = data.get("score")
    # Hunter's score is 0-100 confidence that the address is deliverable.
    confidence = (
        Confidence.VERIFIED
        if isinstance(score, int) and score >= 90
        else Confidence.PATTERN_HIGH
    )
    source = None
    sources = data.get("sources") or []
    if sources and isinstance(sources[0], dict):
        source = sources[0].get("uri")
    return EmailCandidate(
        address=address.lower(),
        confidence=confidence,
        basis=f"hunter.io email-finder, confidence score {score}",
        source_url=source,
    )


# --- the thing the pipeline calls ------------------------------------------


def enrich_emails(
    *,
    full_name: Optional[str],
    domain: Optional[str],
    found: List[EmailCandidate],
    other_emails_at_domain: Sequence[str] = (),
    max_guesses: int = 3,
) -> Tuple[List[EmailCandidate], List[str]]:
    """Combine what the researcher saw with what we can safely infer.

    Returns (candidates_best_first, warnings).
    """
    warnings: List[str] = []
    domain = (domain or "").lower().lstrip("@").strip()
    if domain.startswith("www."):
        domain = domain[4:]

    # Keep every address the researcher actually saw on a page.
    results: List[EmailCandidate] = []
    seen: set[str] = set()
    for cand in found:
        addr = cand.address.lower().strip()
        if not EMAIL_RE.fullmatch(addr) or addr in seen:
            continue
        seen.add(addr)
        results.append(cand)

    personal_verified = [
        c for c in results if not is_generic(c.address) and c.confidence == Confidence.VERIFIED
    ]

    if not domain:
        # Last resort: borrow the domain from any address we did find.
        for cand in results:
            _, dom = split_email(cand.address)
            if dom and dom not in FREE_PROVIDERS:
                domain = dom
                break

    if not domain:
        warnings.append("No company domain found - cannot infer an email address.")
        return results, warnings

    mx_ok = domain_accepts_mail(domain)
    if mx_ok is False:
        warnings.append(
            f"{domain} has no MX records - mail to this domain will bounce. "
            "Check the domain is right before sending anything."
        )

    # If we already have a real personal address, we're done - don't add noise.
    if personal_verified:
        return results, warnings

    # Try the paid finder before guessing.
    if full_name:
        hunted = hunter_lookup(full_name, domain)
        if hunted and hunted.address not in seen:
            seen.add(hunted.address)
            results.insert(0, hunted)
            return results, warnings

    if not full_name:
        warnings.append(
            "No person name found, so no address could be inferred. "
            "The company's general inbox is the only route here."
        )
        return results, warnings

    # Learn the company's format from any real same-domain address.
    pattern_id: Optional[str] = None
    pattern_evidence: Optional[str] = None
    pool = list(other_emails_at_domain) + [c.address for c in results]
    for addr in pool:
        local, dom = split_email(addr)
        if dom != domain or is_generic(addr):
            continue
        # We can only learn a pattern from an address whose owner we know.
        detected = detect_pattern(addr, full_name)
        if detected:
            pattern_id, pattern_evidence = detected, addr
            break

    if pattern_id:
        address = apply_pattern(pattern_id, full_name, domain)
        if address and address not in seen:
            results.append(
                EmailCandidate(
                    address=address,
                    confidence=Confidence.PATTERN_HIGH,
                    basis=(
                        f"'{pattern_id}' format, confirmed by the real address "
                        f"{pattern_evidence} at the same domain"
                    ),
                )
            )
            return results, warnings

    # Nothing to learn from - fall back to ranked guesses, clearly labelled.
    guesses = candidates_for(full_name, domain, limit=max_guesses)
    for address, pattern_id in guesses:
        if address in seen:
            continue
        seen.add(address)
        results.append(
            EmailCandidate(
                address=address,
                confidence=Confidence.PATTERN_GUESS,
                basis=f"unverified '{pattern_id}' guess - no published address found",
            )
        )
    if guesses:
        warnings.append(
            f"No published address for {full_name}. "
            f"{len(guesses)} pattern guesses added - verify before a real send."
        )
    return results, warnings

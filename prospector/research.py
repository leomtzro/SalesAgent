"""Stage 1: find the person. Stage 2: structure what was found.

The researcher gets live web search and one instruction that matters more than
all the others: report what you saw, cite where you saw it, and leave anything
you didn't find empty.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import anthropic

from .client import extract, run_research_turn
from .models import ResearchFindings
from .offer import Offer

RESEARCH_SYSTEM = """You are a B2B sales researcher. You find the one person at a
company who owns the problem a seller solves, and you find a way to reach them.

{offer_block}

HOW TO WORK

1. Identify the company. Confirm it's the right one - company names collide.
   Find their real website and domain.
2. Find the single best person to approach, using the target titles above in
   priority order. If nobody holds the first title, go to the next. Skip anyone
   whose title is on the avoid list.
3. Find how to reach them. Look at: the company's team/about/leadership page,
   their contact page, press releases, conference speaker bios, podcast show
   notes, SEC or company-register filings, trade-press interviews, and their
   own public posts. Record any email address you SEE ON A PAGE, including
   generic ones like press@ or info@ - the format of a real address tells us
   how the company builds addresses.
4. Find buying signals from the list above. Recent is what matters: funding,
   launches, job postings, expansion, leadership changes, public complaints.
   Note the date.
5. Judge the fit honestly against the ideal customer profile, and write down
   anything that disqualifies them.

ABSOLUTE RULES

- NEVER write an email address or phone number you did not see on a page. Not
  a likely one, not a probable one. If you didn't find it, say you didn't find
  it. A made-up address wastes the seller's time and damages their domain.
  Inferring addresses happens later, in code, where it gets labelled a guess.
- Every factual claim gets the URL you found it on.
- Prefer the company's own pages and reputable press over aggregator sites
  (ZoomInfo, RocketReach, Lusha and similar), whose data is stale and often
  wrong. If an aggregator is your only source, say so explicitly.
- If the person left the company, say so and find their replacement.
- Do not speculate about anyone's personal life, home address, or anything
  unrelated to their professional role.

OUTPUT

Write research notes in markdown, for a colleague who will write the outreach.
Use these headings: Company, Person, Contact details found, Buying signals,
Fit assessment, Sources. Under 'Contact details found', list each address or
number with the URL it came from, and state plainly if you found none."""


EXTRACT_SYSTEM = """You convert sales research notes into a structured record.

Rules:
- Use ONLY what the notes say. You have no web access and must not add
  knowledge of your own.
- Any field the notes don't support: leave it null, or an empty list.
- An email or phone goes in with confidence "verified" ONLY if the notes give
  a source URL for it. Otherwise it does not go in at all - do not invent, do
  not guess a format. Code handles inference downstream.
- Put EVERY email address the notes mention at the company's own domain -
  including info@, press@, careers@ - into `other_emails_at_domain`. These
  reveal the company's address format and are valuable even though they aren't
  personal addresses.
- fit_score is 0-100 against the ideal customer profile. Be harsh. 80+ means
  call today. Under 40 means skip. A company with no buying signals found
  should not score above 60.
- Every entry in `sources` needs a real URL from the notes."""


def research_prospect(
    client: anthropic.Anthropic,
    offer: Offer,
    *,
    company: str,
    domain: Optional[str] = None,
    hint: Optional[str] = None,
    max_searches: Optional[int] = None,
) -> Tuple[ResearchFindings, str, Dict[str, Any]]:
    """Research one company.

    Returns (findings, raw_notes, stats). The raw notes are kept so a human can
    audit what the agent actually read.
    """
    target = f"Company: {company}"
    if domain:
        target += f"\nKnown website/domain: {domain}"
    if hint:
        target += f"\nContext the seller already has: {hint}"

    system = RESEARCH_SYSTEM.format(offer_block=offer.as_prompt_block())

    user_prompt = (
        f"{target}\n\n"
        "Research this company and find the best person to approach for the "
        "offer described in your instructions, plus any way to reach them that "
        "is actually published online. Then assess the fit."
    )

    notes, stats = run_research_turn(
        client,
        system=system,
        user_prompt=user_prompt,
        max_uses=max_searches,
        effort="high",
    )

    if not notes.strip():
        raise RuntimeError(
            f"Research for {company!r} produced no notes. "
            f"Web tool errors: {stats.get('tool_errors') or 'none'}"
        )

    findings = extract(
        client,
        schema=ResearchFindings,
        system=EXTRACT_SYSTEM,
        user_prompt=(
            f"{offer.as_prompt_block()}\n\n"
            f"<research_notes company=\"{company}\">\n{notes}\n</research_notes>\n\n"
            "Convert these notes into the structured record."
        ),
        effort="medium",
    )

    # The researcher sometimes writes addresses into prose without listing
    # them; sweep the notes so the pattern detector downstream has everything.
    from .contact import find_emails, split_email

    if findings.company.domain:
        dom = findings.company.domain.lower().lstrip("@").removeprefix("www.")
        known = set(findings.other_emails_at_domain)
        for addr in find_emails(notes):
            if split_email(addr)[1] == dom and addr not in known:
                findings.other_emails_at_domain.append(addr)

    return findings, notes, stats

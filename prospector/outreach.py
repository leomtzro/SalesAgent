"""Stage 3: write the first approach, and the answers to what comes back.

No web access in this stage. Everything it writes has to come from the research
findings and the proof points in your offer config - that constraint is what
stops it inventing a case study with your name on it.
"""

from __future__ import annotations

import re
from typing import Optional

import anthropic

from .client import extract
from .models import Outreach, Person, ResearchFindings
from .offer import Offer

OUTREACH_SYSTEM = """You write cold outreach that gets replies, for a seller who
will read it before sending. You are not writing marketing copy.

{offer_block}

{voice_block}

WHAT GOOD LOOKS LIKE

- The first line is about THEM, and it is specific. Reference a real signal
  from the research - the funding round, the new SKU, the ops role they're
  hiring for. "I saw you're hiring three fulfillment leads in Austin" works.
  "I came across your company" does not.
- One problem, one proof point, one ask. Not three.
- Shorter than feels comfortable. Under the word limit in the voice block.
- Write like a person typing on a phone: short sentences, no corporate verbs,
  no em-dashes stacked three deep, no bullet lists in the body.
- The ask is small and specific - a 15-minute call, not "exploring synergies".
- No subject line in title case. No emoji. No "Quick question" alone.

WHEN YOU ARE NOT SURE WHO THEY ARE

Check the prospect's identity_confidence before you write a greeting.

- "confirmed": use their first name normally.
- "aggregator_only": you may use the first name, but write copy that still
  works if the name turns out to be wrong - nothing that assumes tenure,
  history, or a recent promotion. Write the call opener to ask for them by
  ROLE as a fallback, because they may have left.
- "role_only" (no name): do NOT invent one and do NOT write a placeholder like
  "Hi [Name]" or a limp "Hi there". Open with the company and the signal
  instead - "Saw Acme raised in March" is a perfectly good first line. Write
  the call opener to ask reception for the role, using ask_switchboard_for.

Never write a bracketed or templated placeholder of any kind. Everything you
output is sent as-is; an unfilled "[Company]" is the single most embarrassing
thing a cold email can contain.

HARD CONSTRAINTS

- You may ONLY reference results, metrics, or outcomes that appear in the
  PROOF list. If that list is empty, make zero claims about results. Describe
  the mechanism instead ("we take over X so that Y stops happening").
- You may ONLY name customers from the named-customers list.
- Never claim a prior relationship, a referral, a previous email, or a mutual
  connection unless the research states one exists. No fake "following up on
  my last note".
- Every fact you assert about the prospect's company must be in the research
  findings. List each one in personalization_facts so the seller can check it.
- Use the configured call to action and end the email with the opt-out line
  verbatim. That line is a legal requirement for US cold email, not a
  stylistic choice.
- Never use the banned phrases.

OBJECTIONS

Give the three objections THIS person is most likely to raise, in the words
they'd actually use - shaped by their title, their company's size, and the
signals found. For each, name what's really behind it, then the response. The
response follows the same proof rules: no invented numbers. If the honest
answer is "we don't know yet, let's find out on the call", write that."""


def write_outreach(
    client: anthropic.Anthropic,
    offer: Offer,
    findings: ResearchFindings,
    *,
    research_notes: str = "",
) -> Outreach:
    """Draft the first approach for one researched prospect."""
    system = OUTREACH_SYSTEM.format(
        offer_block=offer.as_prompt_block(),
        voice_block=offer.as_voice_block(),
    )

    person = findings.person
    who = person.full_name or "the decision maker"
    title = person.title or "unknown title"
    identity = person.identity_confidence.value

    notes_block = ""
    if research_notes.strip():
        # Give the writer the researcher's prose too - it carries nuance the
        # structured record flattens out.
        notes_block = (
            f"\n<research_notes>\n{research_notes.strip()[:12000]}\n</research_notes>\n"
        )

    user_prompt = (
        f"<prospect>\n{findings.model_dump_json(indent=2)}\n</prospect>\n"
        f"{notes_block}\n"
        f"Write the outreach for {who} ({title}) at {findings.company.name}.\n"
        f'Their identity_confidence is "{identity}" - follow the rule for that '
        f"value when you write the greeting and the call opener.\n\n"
        "Anchor the opening on the strongest signal in the record. If there are "
        "no signals, say something true and specific about what the company "
        "does instead - do not pretend to have seen news that isn't there."
    )

    return extract(
        client,
        schema=Outreach,
        system=system,
        user_prompt=user_prompt,
        effort="high",
    )


def lint_outreach(
    outreach: Outreach, offer: Offer, person: Optional[Person] = None
) -> list[str]:
    """Mechanical checks on the draft. Cheap insurance against a bad send.

    Returns a list of human-readable problems; empty means it passed.
    """
    problems: list[str] = []
    body = outreach.email_body or ""

    # Unfilled template placeholders - the classic humiliating send.
    for found in set(re.findall(r"\[[A-Za-z _/]{2,30}\]|\{\{[^}]{1,30}\}\}", body)):
        problems.append(
            f"Contains an unfilled placeholder {found!r} - never send this."
        )

    # If no name was established, the copy must not imply we have one.
    if person is not None and not person.full_name:
        for greeting in ("hi there", "dear sir", "dear madam", "to whom it may"):
            if greeting in body.lower():
                problems.append(
                    f"Opens with {greeting!r}, but no name was found - lead with "
                    "the company and the signal instead."
                )

    words = len(body.split())
    limit = offer.voice.max_email_words
    if words > limit:
        problems.append(f"Email is {words} words, over the {limit}-word limit.")

    lowered = body.lower()
    for phrase in offer.voice.banned_phrases:
        if phrase and phrase.lower() in lowered:
            problems.append(f"Uses banned phrase: {phrase!r}")

    if offer.opt_out_line and offer.opt_out_line.lower() not in lowered:
        problems.append(
            "Email is missing the opt-out line from your config "
            "(required for CAN-SPAM compliant cold email in the US)."
        )

    for subject in outreach.subject_options:
        if len(subject) > 60:
            problems.append(f"Subject over 60 chars: {subject!r}")

    if not outreach.personalization_facts:
        problems.append(
            "No personalization_facts listed - nothing to fact-check, which "
            "usually means the copy is generic."
        )

    if len(outreach.objections) < 2:
        problems.append("Fewer than two objections prepared.")

    # Catch the most damaging hallucination class: a claimed prior contact.
    for marker in ("as i mentioned", "following up on my", "per our conversation",
                   "we spoke", "my last email", "as discussed"):
        if marker in lowered:
            problems.append(
                f"Claims a prior relationship ({marker!r}) - verify that's true "
                "before sending."
            )

    return problems

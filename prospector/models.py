"""Schemas for everything the agent produces.

These are Pydantic models, and they are handed straight to the Claude API as
the required output shape. That matters: the model cannot return a field that
isn't declared here, and anything it can't find comes back as null rather than
as an invented value. If you want a new field in your briefs, add it here
first.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class Confidence(str, Enum):
    """How much to trust a contact detail.

    VERIFIED      - published somewhere public, with a source URL.
    PATTERN_HIGH  - inferred, but the company's email pattern is known from a
                    real same-domain address we found.
    PATTERN_GUESS - inferred from a common pattern. Deliverability unknown.
    UNKNOWN       - no basis at all.
    """

    VERIFIED = "verified"
    PATTERN_HIGH = "pattern_high"
    PATTERN_GUESS = "pattern_guess"
    UNKNOWN = "unknown"


class Source(BaseModel):
    """Where a claim came from. No source, no claim."""

    url: str = Field(description="The page this was found on.")
    what_it_supports: str = Field(
        description="Which specific fact this page backs up, in a few words."
    )


class EmailCandidate(BaseModel):
    address: str
    confidence: Confidence
    basis: str = Field(
        description="Why we think this is the address - e.g. 'listed on the "
        "team page', or 'first.last pattern confirmed by press@ contact'."
    )
    source_url: Optional[str] = None


class PhoneCandidate(BaseModel):
    number: str = Field(description="E.164 format where possible, e.g. +14155550123.")
    kind: str = Field(description="One of: direct, mobile, switchboard, unknown.")
    confidence: Confidence
    basis: str
    source_url: Optional[str] = None


class Person(BaseModel):
    """The human you are going to contact."""

    full_name: Optional[str] = None
    title: Optional[str] = None
    seniority: Optional[str] = Field(
        default=None,
        description="One of: founder, c_level, vp, director, manager, ic, unknown.",
    )
    linkedin_url: Optional[str] = None
    location: Optional[str] = None
    why_this_person: Optional[str] = Field(
        default=None,
        description="Why this person owns the problem being sold into. One "
        "sentence, grounded in something found online.",
    )
    reports_to: Optional[str] = Field(
        default=None, description="Their boss, if found - useful for escalation."
    )


class Company(BaseModel):
    name: str
    domain: Optional[str] = None
    what_they_do: Optional[str] = None
    employee_estimate: Optional[str] = None
    revenue_estimate: Optional[str] = None
    hq_location: Optional[str] = None
    industry: Optional[str] = None


class Signal(BaseModel):
    """A reason to call them this week rather than never."""

    signal: str = Field(description="What happened, factually.")
    why_it_matters: str = Field(description="Why it creates a need for the offer.")
    date: Optional[str] = Field(default=None, description="When, if known (YYYY-MM).")
    source_url: Optional[str] = None


class Objection(BaseModel):
    """A predicted pushback and what to say back."""

    objection: str = Field(description="In the prospect's own likely words.")
    why_it_comes_up: str = Field(description="What's really behind it.")
    response: str = Field(
        description="What to say. Grounded only in the proof points from the "
        "offer config - no invented results."
    )


class FollowUp(BaseModel):
    send_on_day: int = Field(
        description="Days after the first email. E.g. 3 means send on day 3."
    )
    channel: str = Field(description="One of: email, call, linkedin.")
    body: str


class Outreach(BaseModel):
    """The first approach, and the response to what comes back."""

    subject_options: List[str] = Field(
        description="Three subject lines, under 50 characters each, no clickbait."
    )
    email_body: str = Field(
        description="The first-touch email. Plain text, no greeting fluff, "
        "anchored to a specific signal, ending in the configured call to action "
        "and opt-out line."
    )
    followups: List[FollowUp] = Field(
        description="Two or three follow-ups across channels, each adding new "
        "information rather than 'just bumping this'."
    )
    call_opener: str = Field(
        description="The first 20 seconds of a cold call: who you are, why "
        "you're calling them specifically, and a question."
    )
    voicemail: str = Field(description="Under 20 seconds, spoken aloud.")
    linkedin_dm: str = Field(description="Under 400 characters.")
    objections: List[Objection] = Field(
        description="The three most likely objections from THIS person, with responses."
    )
    personalization_facts: List[str] = Field(
        description="Every specific claim about this company used in the copy "
        "above, so a human can fact-check it before sending."
    )


class Prospect(BaseModel):
    """One complete, actionable prospect record."""

    company: Company
    person: Person
    emails: List[EmailCandidate] = Field(default_factory=list)
    phones: List[PhoneCandidate] = Field(default_factory=list)
    signals: List[Signal] = Field(default_factory=list)
    fit_score: int = Field(
        description="0-100. How well this matches the ICP in the offer config. "
        "Be harsh: 80+ means call today, under 40 means skip."
    )
    fit_rationale: str
    disqualifiers: List[str] = Field(
        default_factory=list,
        description="Concrete reasons this may be a waste of time.",
    )
    sources: List[Source] = Field(default_factory=list)
    outreach: Optional[Outreach] = None

    # Filled in by the pipeline, not the model.
    researched_at: Optional[str] = None
    model_used: Optional[str] = None
    notes: Optional[str] = None

    @property
    def best_email(self) -> Optional[EmailCandidate]:
        order = {
            Confidence.VERIFIED: 0,
            Confidence.PATTERN_HIGH: 1,
            Confidence.PATTERN_GUESS: 2,
            Confidence.UNKNOWN: 3,
        }
        return min(self.emails, key=lambda e: order[e.confidence], default=None)

    @property
    def best_phone(self) -> Optional[PhoneCandidate]:
        order = {"direct": 0, "mobile": 1, "switchboard": 2, "unknown": 3}
        return min(self.phones, key=lambda p: order.get(p.kind, 3), default=None)

    @property
    def slug(self) -> str:
        import re

        base = f"{self.company.name}-{self.person.full_name or 'unknown'}"
        return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:80]

    def stamp(self, model: str) -> "Prospect":
        self.researched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.model_used = model
        return self


# --- Intermediate shapes used between pipeline stages -----------------------


class ResearchFindings(BaseModel):
    """What the extraction step pulls out of the researcher's raw notes.

    Deliberately separate from Prospect: outreach is written in a later call,
    once contact details have been checked.
    """

    company: Company
    person: Person
    emails: List[EmailCandidate] = Field(default_factory=list)
    phones: List[PhoneCandidate] = Field(default_factory=list)
    signals: List[Signal] = Field(default_factory=list)
    fit_score: int
    fit_rationale: str
    disqualifiers: List[str] = Field(default_factory=list)
    sources: List[Source] = Field(default_factory=list)
    other_emails_at_domain: List[str] = Field(
        default_factory=list,
        description="Any other real email addresses seen at this company's "
        "domain, even generic ones like info@ or press@. These reveal the "
        "company's address format.",
    )

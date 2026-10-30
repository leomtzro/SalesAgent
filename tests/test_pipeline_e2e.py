"""End-to-end test with a faked Claude API.

This never touches the network. It stands in a fake client that returns the
same shapes the real API returns, which proves the wiring: research notes ->
structured extraction -> contact enrichment -> outreach -> brief on disk.

It also pins the behaviour that matters most: an address the researcher never
saw must come out labelled a guess, not as fact.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from prospector import pipeline, store
from prospector.models import (
    Company,
    Confidence,
    EmailCandidate,
    FollowUp,
    Objection,
    Outreach,
    Person,
    ResearchFindings,
    Signal,
    Source,
)
from prospector.offer import load_offer

NOTES = """## Company
Acme Supplements (acmesupplements.com) makes creatine and electrolytes.

## Person
Jane Doe, VP of Operations. https://linkedin.com/in/janedoe

## Contact details found
- info@acmesupplements.com (https://acmesupplements.com/contact)
- No personal address published for Jane Doe.

## Buying signals
- Raised a $12M Series A in 2026-03 (https://techcrunch.example/acme-a)
- Hiring two fulfillment leads (https://acmesupplements.com/careers)

## Fit assessment
Strong fit: 120 employees, supplements, recent raise.

## Sources
- https://acmesupplements.com/team
"""


class FakeBlock:
    def __init__(self, type_, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


class FakeUsage:
    input_tokens = 4200
    output_tokens = 900


class FakeResponse:
    def __init__(self, content, stop_reason="end_turn", parsed=None):
        self.content = content
        self.stop_reason = stop_reason
        self.stop_details = None
        self.usage = FakeUsage()
        self.parsed_output = parsed


FINDINGS = ResearchFindings(
    company=Company(
        name="Acme Supplements",
        domain="acmesupplements.com",
        what_they_do="Creatine and electrolyte brand.",
        employee_estimate="120",
        hq_location="Austin, TX",
        industry="Supplements",
    ),
    person=Person(
        full_name="Jane Doe",
        title="VP of Operations",
        seniority="vp",
        linkedin_url="https://linkedin.com/in/janedoe",
        why_this_person="Owns fulfillment and 3PL relationships.",
    ),
    emails=[
        EmailCandidate(
            address="info@acmesupplements.com",
            confidence=Confidence.VERIFIED,
            basis="contact page",
            source_url="https://acmesupplements.com/contact",
        )
    ],
    phones=[],
    signals=[
        Signal(
            signal="Raised a $12M Series A",
            why_it_matters="New capital usually means scaling fulfillment",
            date="2026-03",
            source_url="https://techcrunch.example/acme-a",
        )
    ],
    fit_score=86,
    fit_rationale="Supplements brand, right size, just raised.",
    disqualifiers=[],
    sources=[
        Source(url="https://acmesupplements.com/team", what_it_supports="Jane's title")
    ],
    other_emails_at_domain=["info@acmesupplements.com"],
)

OUTREACH = Outreach(
    subject_options=["fulfillment after the raise", "3PL mis-ships", "quick one, Jane"],
    email_body=(
        "Jane - saw Acme raised the $12M in March and you're hiring two "
        "fulfillment leads.\n\n"
        "Usually when a supplement brand scales that fast the 3PL starts "
        "mis-shipping and chargebacks follow. We took one $12M creatine brand "
        "from 4.1% mis-ships to 0.6% in 90 days.\n\n"
        "Worth a 15-minute call this week to see if the numbers line up?\n\n"
        "If this isn't relevant, reply 'no' and I won't follow up."
    ),
    followups=[
        FollowUp(send_on_day=3, channel="call", body="Call the switchboard, ask for Jane."),
        FollowUp(send_on_day=7, channel="email", body="Send the mis-ship teardown."),
    ],
    call_opener="Jane, it's Leo. You raised in March and you're hiring fulfillment leads - are mis-ships already showing up in your returns?",
    voicemail="Jane, Leo here about fulfillment after your raise. Calling back Thursday.",
    linkedin_dm="Saw the Series A and the fulfillment hires. We cut mis-ships for a brand your size from 4.1% to 0.6%. Worth 15 minutes?",
    objections=[
        Objection(
            objection="We just signed with our 3PL.",
            why_it_comes_up="Switching cost fear, and they don't want to admit it's bad.",
            response="Not asking you to switch. Asking what your mis-ship rate is.",
        ),
        Objection(
            objection="Send me something.",
            why_it_comes_up="Polite brush-off.",
            response="I'll send the teardown. What's the one number you'd want it to move?",
        ),
    ],
    personalization_facts=[
        "Acme raised a $12M Series A in March 2026",
        "Acme is hiring two fulfillment leads",
    ],
)


class FakeMessages:
    def __init__(self):
        self.create_calls = []
        self.parse_calls = []

    def create(self, **kw):
        self.create_calls.append(kw)
        return FakeResponse(
            [
                FakeBlock("server_tool_use", name="web_search"),
                FakeBlock("web_search_tool_result", content=[{"title": "Acme"}]),
                FakeBlock("server_tool_use", name="web_fetch"),
                FakeBlock("text", text=NOTES),
            ]
        )

    def parse(self, **kw):
        self.parse_calls.append(kw)
        schema = kw["output_format"]
        parsed = FINDINGS if schema is ResearchFindings else OUTREACH
        return FakeResponse([FakeBlock("text", text="{}")], parsed=parsed)


class FakeClient:
    def __init__(self):
        self.messages = FakeMessages()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect all output to a temp dir so tests never touch outputs/."""
    monkeypatch.setattr(store, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(store, "JSONL_PATH", tmp_path / "prospects.jsonl")
    monkeypatch.setattr(store, "CSV_PATH", tmp_path / "pipeline.csv")
    monkeypatch.setattr(store, "BRIEF_DIR", tmp_path / "briefs")
    # No DNS in tests - keep it deterministic and offline.
    monkeypatch.setattr(
        "prospector.contact.domain_accepts_mail", lambda domain: True
    )
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    return tmp_path


@pytest.fixture
def offer():
    return load_offer("config/offer.yaml")


class TestRunOne:
    def test_produces_a_complete_prospect(self, sandbox, offer):
        client = FakeClient()
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements", domain="acmesupplements.com"),
            client=client,
            offer=offer,
        )

        assert result.ok, result.error
        p = result.prospect
        assert p.company.name == "Acme Supplements"
        assert p.person.full_name == "Jane Doe"
        assert p.fit_score == 86
        assert p.outreach is not None
        assert p.researched_at and p.model_used

    def test_research_call_declares_web_tools(self, sandbox, offer):
        client = FakeClient()
        pipeline.run_one(
            pipeline.Target(company="Acme Supplements"), client=client, offer=offer
        )
        kw = client.messages.create_calls[0]
        tool_types = [t["type"] for t in kw["tools"]]
        assert "web_search_20260209" in tool_types
        assert "web_fetch_20260209" in tool_types
        assert kw["output_config"]["effort"] == "high"

    def test_extraction_has_no_web_access(self, sandbox, offer):
        """The extraction and writing calls must not be able to search.

        If they could, they could 'find' details the researcher never cited.
        """
        client = FakeClient()
        pipeline.run_one(
            pipeline.Target(company="Acme Supplements"), client=client, offer=offer
        )
        for kw in client.messages.parse_calls:
            assert not kw.get("tools"), "extraction must run without tools"

    def test_unpublished_address_is_labelled_a_guess(self, sandbox, offer):
        """The behaviour that protects the sending domain.

        The notes only had info@ - a generic address. So Jane's personal
        address cannot be verified, and must come back as a guess.
        """
        client = FakeClient()
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"), client=client, offer=offer
        )
        personal = [
            e
            for e in result.prospect.emails
            if not e.address.startswith("info@")
        ]
        assert personal, "should still offer a route to Jane"
        assert all(e.confidence == Confidence.PATTERN_GUESS for e in personal)
        assert any("verify" in w.lower() for w in result.warnings)

    def test_verified_only_drops_the_guesses(self, sandbox, offer):
        client = FakeClient()
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=client,
            offer=offer,
            verified_only=True,
        )
        assert all(
            e.confidence != Confidence.PATTERN_GUESS for e in result.prospect.emails
        )

    def test_brief_is_written_and_readable(self, sandbox, offer):
        client = FakeClient()
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"), client=client, offer=offer
        )
        assert result.brief_path.exists()
        text = result.brief_path.read_text()
        assert "Jane Doe" in text
        assert "VP of Operations" in text
        assert "Raised a $12M Series A" in text
        assert "We just signed with our 3PL." in text  # objection handling
        assert "Fact-check before sending" in text
        assert "GUESS" in text  # confidence is surfaced, not hidden

    def test_record_appended_to_jsonl(self, sandbox, offer):
        client = FakeClient()
        pipeline.run_one(
            pipeline.Target(company="Acme Supplements"), client=client, offer=offer
        )
        records = list(store.read_all(store.JSONL_PATH))
        assert len(records) == 1
        assert records[0].person.full_name == "Jane Doe"

    def test_no_copy_flag_skips_outreach(self, sandbox, offer):
        client = FakeClient()
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=client,
            offer=offer,
            write_outreach_copy=False,
        )
        assert result.prospect.outreach is None
        assert len(client.messages.parse_calls) == 1  # extraction only

    def test_min_fit_gate(self, sandbox, offer):
        client = FakeClient()
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=client,
            offer=offer,
            min_fit_for_outreach=95,  # above the fixture's 86
        )
        assert result.prospect.outreach is None
        assert any("below the" in w for w in result.warnings)

    def test_stats_are_counted(self, sandbox, offer):
        client = FakeClient()
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"), client=client, offer=offer
        )
        assert result.stats["searches"] == 1
        assert result.stats["fetches"] == 1
        assert result.stats["input_tokens"] > 0

    def test_api_failure_is_contained(self, sandbox, offer):
        """One bad company must not take down a batch."""

        class Broken(FakeClient):
            def __init__(self):
                super().__init__()
                self.messages.create = self._boom

            def _boom(self, **kw):
                raise RuntimeError("API exploded")

        result = pipeline.run_one(
            pipeline.Target(company="Broken Co"), client=Broken(), offer=offer
        )
        assert not result.ok
        assert "API exploded" in result.error


class TestPauseTurn:
    def test_paused_research_resumes(self, sandbox, offer):
        """A paused server-tool turn must be resumed, not silently truncated."""

        class Pausing(FakeClient):
            def __init__(self):
                super().__init__()
                self.n = 0
                self.messages.create = self._create

            def _create(self, **kw):
                self.n += 1
                if self.n == 1:
                    return FakeResponse(
                        [FakeBlock("text", text="partial...")], stop_reason="pause_turn"
                    )
                return FakeResponse([FakeBlock("text", text=NOTES)])

        client = Pausing()
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"), client=client, offer=offer
        )
        assert result.ok, result.error
        assert client.n == 2, "should have resumed once"
        assert result.stats["restarts"] == 1


class TestCSV:
    def test_csv_written_with_confidence_column(self, sandbox, offer):
        client = FakeClient()
        pipeline.run_one(
            pipeline.Target(company="Acme Supplements"), client=client, offer=offer
        )
        store.write_csv(store.read_all(store.JSONL_PATH), store.CSV_PATH)
        text = store.CSV_PATH.read_text()
        assert "email_confidence" in text
        assert "Acme Supplements" in text
        assert "Jane Doe" in text


class TestLint:
    def test_good_draft_passes(self, offer):
        from prospector.outreach import lint_outreach

        problems = lint_outreach(OUTREACH, offer)
        assert problems == [], problems


# --- identity confidence ----------------------------------------------------
#
# The live research run that motivated these: the decision maker's name was
# only on a third-party directory, and the org structure had to be read out of
# job postings. Both are the normal case, so both get pinned here.


def _findings_with(**person_overrides) -> ResearchFindings:
    data = FINDINGS.model_dump()
    data["person"].update(person_overrides)
    return ResearchFindings.model_validate(data)


class IdentityClient(FakeClient):
    """A fake whose extraction returns a chosen identity_confidence."""

    def __init__(self, findings: ResearchFindings):
        super().__init__()
        self._findings = findings
        self.messages.parse = self._parse

    def _parse(self, **kw):
        self.messages.parse_calls.append(kw)
        schema = kw["output_format"]
        parsed = self._findings if schema is ResearchFindings else OUTREACH
        return FakeResponse([FakeBlock("text", text="{}")], parsed=parsed)


class TestIdentityConfidence:
    def test_aggregator_only_warns_about_staleness(self, sandbox, offer):
        from prospector.models import IdentityConfidence

        findings = _findings_with(
            identity_confidence=IdentityConfidence.AGGREGATOR_ONLY.value,
            identity_source_url="https://theorg.example/acme",
        )
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=IdentityClient(findings),
            offer=offer,
        )
        assert result.ok, result.error
        assert any(
            "third-party directory" in w for w in result.warnings
        ), result.warnings
        brief = result.brief_path.read_text()
        assert "DIRECTORY ONLY" in brief

    def test_role_only_still_gives_a_play(self, sandbox, offer):
        """No name is not a dead end - the switchboard route must survive."""
        from prospector.models import IdentityConfidence

        findings = _findings_with(
            full_name=None,
            identity_confidence=IdentityConfidence.ROLE_ONLY.value,
            role_confirmed_by="A Quality PM posting reports to the VP of Ops",
            ask_switchboard_for="whoever runs fulfillment and 3PL",
        )
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=IdentityClient(findings),
            offer=offer,
        )
        assert result.ok, result.error
        assert any("ask for" in w.lower() for w in result.warnings), result.warnings

        brief = result.brief_path.read_text()
        assert "ROLE ONLY" in brief
        assert "whoever runs fulfillment and 3PL" in brief
        assert "Quality PM posting" in brief

    def test_confirmed_identity_raises_no_warning(self, sandbox, offer):
        from prospector.models import IdentityConfidence

        findings = _findings_with(
            identity_confidence=IdentityConfidence.CONFIRMED.value,
            identity_source_url="https://acmesupplements.com/team",
            evidence_date="2026-09",
        )
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=IdentityClient(findings),
            offer=offer,
        )
        assert not any(
            "directory" in w.lower() or "unverified" in w.lower()
            for w in result.warnings
        ), result.warnings
        brief = result.brief_path.read_text()
        assert "CONFIRMED" in brief
        assert "2026-09" in brief

    def test_unsourced_name_is_flagged(self, sandbox, offer):
        """A name with no stated provenance must not read as a fact."""
        findings = _findings_with(identity_confidence="unknown")
        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=IdentityClient(findings),
            offer=offer,
        )
        assert any("unverified" in w.lower() for w in result.warnings), result.warnings

    def test_identity_reaches_the_writer(self, sandbox, offer):
        """The writer must be told how sure the name is, or it can't comply."""
        from prospector.models import IdentityConfidence

        findings = _findings_with(
            identity_confidence=IdentityConfidence.ROLE_ONLY.value, full_name=None
        )
        client = IdentityClient(findings)
        pipeline.run_one(
            pipeline.Target(company="Acme Supplements"), client=client, offer=offer
        )
        outreach_call = client.messages.parse_calls[-1]
        prompt = outreach_call["messages"][0]["content"]
        assert "role_only" in prompt
        assert "identity_confidence" in outreach_call["system"]

    def test_csv_carries_identity_columns(self, sandbox, offer):
        from prospector.models import IdentityConfidence

        findings = _findings_with(
            full_name=None,
            identity_confidence=IdentityConfidence.ROLE_ONLY.value,
            ask_switchboard_for="whoever runs ops",
        )
        pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=IdentityClient(findings),
            offer=offer,
        )
        store.write_csv(store.read_all(store.JSONL_PATH), store.CSV_PATH)
        text = store.CSV_PATH.read_text()
        assert "identity_confidence" in text
        assert "role_only" in text
        assert "whoever runs ops" in text


class TestDraftLinting:
    def test_placeholder_is_caught(self, offer):
        from prospector.outreach import lint_outreach

        draft = OUTREACH.model_copy(
            update={"email_body": "Hi [First Name], saw {{company}} raised."}
        )
        problems = lint_outreach(draft, offer)
        assert sum("placeholder" in p for p in problems) == 2, problems

    def test_limp_greeting_caught_only_when_nameless(self, offer):
        from prospector.outreach import lint_outreach

        draft = OUTREACH.model_copy(
            update={"email_body": "Hi there, saw the raise. " + OUTREACH.email_body}
        )
        nameless = lint_outreach(draft, offer, Person())
        named = lint_outreach(draft, offer, Person(full_name="Jane Doe"))
        assert any("hi there" in p.lower() for p in nameless)
        assert not any("hi there" in p.lower() for p in named)

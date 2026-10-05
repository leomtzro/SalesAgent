"""Tests for the contact inference - the part that must never invent an address."""

from prospector.contact import (
    apply_pattern,
    candidates_for,
    detect_pattern,
    domain_accepts_mail,
    enrich_emails,
    find_emails,
    is_generic,
    normalize_name,
)
from prospector.models import Confidence, EmailCandidate


class TestNormalizeName:
    def test_simple(self):
        assert normalize_name("Jane Doe") == ("jane", "doe")

    def test_strips_accents(self):
        assert normalize_name("José Pérez") == ("jose", "perez")

    def test_strips_titles_and_suffixes(self):
        assert normalize_name("Dr. Jane Doe III") == ("jane", "doe")
        assert normalize_name("Jane Doe, PhD") == ("jane", "doe")

    def test_middle_name_ignored(self):
        assert normalize_name("Jane Marie Doe") == ("jane", "doe")

    def test_hyphenated_surname_kept(self):
        assert normalize_name("Ana Pérez-Gómez") == ("ana", "perez-gomez")

    def test_single_name(self):
        assert normalize_name("Cher") == ("cher", "")

    def test_empty(self):
        assert normalize_name("") == ("", "")
        assert normalize_name("   ") == ("", "")


class TestPatternDetection:
    def test_first_last(self):
        assert detect_pattern("jane.doe@acme.com", "Jane Doe") == "first.last"

    def test_flast(self):
        assert detect_pattern("jdoe@acme.com", "Jane Doe") == "flast"

    def test_first_only(self):
        assert detect_pattern("jane@acme.com", "Jane Doe") == "first"

    def test_underscore(self):
        assert detect_pattern("jane_doe@acme.com", "Jane Doe") == "first_last"

    def test_mismatched_name_returns_none(self):
        # Critically: a wrong pattern is worse than no pattern.
        assert detect_pattern("bob.smith@acme.com", "Jane Doe") is None

    def test_unrecognised_shape_returns_none(self):
        assert detect_pattern("jd27x@acme.com", "Jane Doe") is None

    def test_roundtrip(self):
        pattern = detect_pattern("jane.doe@acme.com", "Jane Doe")
        assert apply_pattern(pattern, "Sam Smith", "acme.com") == "sam.smith@acme.com"

    def test_apply_lowercases_domain(self):
        assert apply_pattern("first", "Sam Smith", "ACME.COM") == "sam@acme.com"


class TestGenerics:
    def test_detects_generic(self):
        for addr in ("info@acme.com", "press@acme.com", "no-reply@acme.com"):
            assert is_generic(addr)

    def test_personal_is_not_generic(self):
        assert not is_generic("jane.doe@acme.com")


class TestFindEmails:
    def test_extracts_and_dedupes(self):
        text = "Reach us at Info@Acme.com or jane.doe@acme.com. Again: info@acme.com."
        assert find_emails(text) == ["info@acme.com", "jane.doe@acme.com"]

    def test_strips_trailing_period(self):
        assert find_emails("write to jane@acme.com.") == ["jane@acme.com"]

    def test_no_emails(self):
        assert find_emails("nothing here") == []
        assert find_emails("") == []


class TestEnrichEmails:
    def test_keeps_verified_and_adds_no_guesses(self):
        found = [
            EmailCandidate(
                address="jane.doe@acme.com",
                confidence=Confidence.VERIFIED,
                basis="team page",
                source_url="https://acme.com/team",
            )
        ]
        emails, warnings = enrich_emails(
            full_name="Jane Doe", domain="acme.com", found=found
        )
        assert [e.address for e in emails] == ["jane.doe@acme.com"]
        assert all(e.confidence == Confidence.VERIFIED for e in emails)

    def test_learns_pattern_from_generic_sibling(self):
        # We know a real address at the domain belonging to a known person.
        emails, _ = enrich_emails(
            full_name="Sam Smith",
            domain="acme.com",
            found=[],
            other_emails_at_domain=["sam.smith@acme.com"],
        )
        # The person's own address was in the pool, so it is returned as high
        # confidence rather than a blind guess.
        assert emails[0].confidence == Confidence.PATTERN_HIGH
        assert emails[0].address == "sam.smith@acme.com"

    def test_falls_back_to_labelled_guesses(self):
        emails, warnings = enrich_emails(
            full_name="Sam Smith", domain="acme.com", found=[]
        )
        assert emails, "should produce candidates"
        assert all(e.confidence == Confidence.PATTERN_GUESS for e in emails)
        assert any("verify" in w.lower() for w in warnings)

    def test_no_name_means_no_inference(self):
        emails, warnings = enrich_emails(full_name=None, domain="acme.com", found=[])
        assert emails == []
        assert any("no person name" in w.lower() for w in warnings)

    def test_no_domain_warns(self):
        emails, warnings = enrich_emails(full_name="Sam Smith", domain=None, found=[])
        assert any("domain" in w.lower() for w in warnings)

    def test_borrows_domain_from_found_address(self):
        found = [
            EmailCandidate(
                address="info@acme.com",
                confidence=Confidence.VERIFIED,
                basis="contact page",
                source_url="https://acme.com/contact",
            )
        ]
        emails, _ = enrich_emails(full_name="Sam Smith", domain=None, found=found)
        # info@ is generic, so it must still infer a personal address.
        assert any(e.address.endswith("@acme.com") and not is_generic(e.address)
                   for e in emails)

    def test_strips_www_prefix(self):
        emails, _ = enrich_emails(
            full_name="Sam Smith", domain="www.acme.com", found=[]
        )
        assert all("@acme.com" in e.address for e in emails)

    def test_dedupes(self):
        found = [
            EmailCandidate(
                address="sam@acme.com", confidence=Confidence.UNKNOWN, basis="x"
            )
        ]
        emails, _ = enrich_emails(
            full_name="Sam Smith", domain="acme.com", found=found
        )
        addresses = [e.address for e in emails]
        assert len(addresses) == len(set(addresses))


class TestCandidates:
    def test_ranked_and_capped(self):
        out = candidates_for("Jane Doe", "acme.com", limit=3)
        assert len(out) == 3
        assert out[0] == ("jane.doe@acme.com", "first.last")

    def test_needs_name_and_domain(self):
        assert candidates_for("", "acme.com") == []
        assert candidates_for("Jane Doe", "") == []


class TestMX:
    def test_real_domain(self):
        # None means DNS was unavailable in this environment - not a failure.
        assert domain_accepts_mail("google.com") in (True, None)

    def test_nonexistent_domain(self):
        assert domain_accepts_mail("this-domain-does-not-exist-99881.com") in (
            False,
            None,
        )

    def test_empty(self):
        assert domain_accepts_mail("") is None

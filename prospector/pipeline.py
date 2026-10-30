"""Orchestration: company name in, finished brief out.

    research_prospect()   -> who they are, what's happening, what was published
    enrich_emails()       -> an address, with an honest confidence label
    write_outreach()      -> the approach and the objection responses
    store                 -> jsonl + csv + brief
"""

from __future__ import annotations

import csv
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import anthropic

from . import store
from .client import _model, build_client
from .contact import enrich_emails
from .models import IdentityConfidence, Prospect
from .offer import Offer, load_offer
from .outreach import lint_outreach, write_outreach
from .research import research_prospect

log = logging.getLogger("prospector")


@dataclass
class Target:
    company: str
    domain: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class Result:
    target: Target
    prospect: Optional[Prospect] = None
    brief_path: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)
    lint: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.prospect is not None


def load_targets(path: Path | str) -> List[Target]:
    """Read a targets CSV.

    Accepts a header row with any of: company, domain, notes. A single-column
    file with no header is treated as a list of company names.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No target list at {path}")

    text = path.read_text()
    if not text.strip():
        return []

    sample = text.splitlines()[0].lower()
    has_header = "company" in sample

    targets: List[Target] = []
    if has_header:
        for row in csv.DictReader(text.splitlines()):
            name = (row.get("company") or "").strip()
            if not name:
                continue
            targets.append(
                Target(
                    company=name,
                    domain=(row.get("domain") or "").strip() or None,
                    notes=(row.get("notes") or "").strip() or None,
                )
            )
    else:
        for row in csv.reader(text.splitlines()):
            if row and row[0].strip():
                targets.append(Target(company=row[0].strip()))
    return targets


def run_one(
    target: Target,
    *,
    client: Optional[anthropic.Anthropic] = None,
    offer: Optional[Offer] = None,
    offer_path: Optional[str] = None,
    write_outreach_copy: bool = True,
    verified_only: bool = False,
    max_searches: Optional[int] = None,
    keep_notes: bool = True,
    min_fit_for_outreach: int = 0,
) -> Result:
    """Research one company and produce a brief."""
    client = client or build_client()
    offer = offer or load_offer(offer_path or "config/offer.yaml")
    result = Result(target=target)

    try:
        findings, notes, stats = research_prospect(
            client,
            offer,
            company=target.company,
            domain=target.domain,
            hint=target.notes,
            max_searches=max_searches,
        )
        result.stats = stats

        # Contact enrichment is deliberately code, not model.
        emails, warnings = enrich_emails(
            full_name=findings.person.full_name,
            domain=findings.company.domain or target.domain,
            found=findings.emails,
            other_emails_at_domain=findings.other_emails_at_domain,
        )
        if verified_only:
            from .models import Confidence

            dropped = [e for e in emails if e.confidence == Confidence.PATTERN_GUESS]
            emails = [e for e in emails if e.confidence != Confidence.PATTERN_GUESS]
            if dropped:
                warnings.append(
                    f"--verified-only dropped {len(dropped)} guessed address(es)."
                )
        # A name from a third-party directory is the normal case, not an
        # exception - so say so on every brief rather than burying it.
        ident = findings.person.identity_confidence
        if ident == IdentityConfidence.AGGREGATOR_ONLY:
            warnings.append(
                f"{findings.person.full_name or 'This name'} comes only from a "
                "third-party directory. Confirm the name and that they still "
                "hold the role before you use it - directory data goes stale."
            )
        elif ident == IdentityConfidence.ROLE_ONLY:
            ask = findings.person.ask_switchboard_for or findings.person.title
            warnings.append(
                f"No name found, but the role is real. Call the main line and "
                f"ask for {ask!r}."
            )
        elif ident == IdentityConfidence.UNKNOWN and findings.person.full_name:
            warnings.append(
                "The research did not establish where this name came from. "
                "Treat it as unverified."
            )
        result.warnings = warnings

        prospect = Prospect(
            company=findings.company,
            person=findings.person,
            emails=emails,
            phones=findings.phones,
            signals=findings.signals,
            fit_score=findings.fit_score,
            fit_rationale=findings.fit_rationale,
            disqualifiers=findings.disqualifiers,
            sources=findings.sources,
            notes=target.notes,
        ).stamp(_model())

        if write_outreach_copy and prospect.fit_score >= min_fit_for_outreach:
            prospect.outreach = write_outreach(
                client, offer, findings, research_notes=notes
            )
            result.lint = lint_outreach(
                prospect.outreach, offer, prospect.person
            )
        elif write_outreach_copy:
            result.warnings.append(
                f"Fit score {prospect.fit_score} is below the "
                f"--min-fit threshold of {min_fit_for_outreach}; no copy written."
            )

        result.prospect = prospect

        brief = store.render_brief(
            prospect,
            warnings=result.warnings,
            lint=result.lint,
            notes=notes if keep_notes else None,
        )
        result.brief_path = store.write_brief(prospect, brief)
        store.append_jsonl(prospect)

    except Exception as exc:  # one bad company must not kill a batch
        log.exception("failed on %s", target.company)
        result.error = f"{type(exc).__name__}: {exc}"

    return result


def run_batch(
    targets: List[Target],
    *,
    concurrency: int = 3,
    skip_existing: bool = True,
    on_result=None,
    offer_path: Optional[str] = None,
    **kwargs,
) -> List[Result]:
    """Research many companies, a few at a time.

    Concurrency is threads, not async: the SDK call is blocking I/O, so threads
    are enough and keep the code readable. Three at a time is a sane default -
    higher risks API rate limits.
    """
    client = build_client()
    offer = load_offer(offer_path or "config/offer.yaml")

    if skip_existing:
        seen_companies = {c for c, _ in store.existing_keys()}
        before = len(targets)
        targets = [t for t in targets if t.company.strip().lower() not in seen_companies]
        if before != len(targets):
            log.info("skipping %d already-researched company(ies)", before - len(targets))

    results: List[Result] = []
    if not targets:
        return results

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {
            pool.submit(run_one, t, client=client, offer=offer, **kwargs): t
            for t in targets
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if on_result:
                on_result(result)

    # Rewrite the CSV from the full archive so it always reflects everything.
    store.write_csv(sorted(store.read_all(), key=lambda p: -p.fit_score))
    return results

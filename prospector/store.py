"""Where the output goes: a JSONL record, a CSV row, and a brief to read.

Three formats because they serve three different moments: the JSONL is the
archive, the CSV is what you paste into a CRM or a sheet, and the brief is what
you actually read in the 60 seconds before you dial.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

from .models import Confidence, Prospect

OUTPUT_DIR = Path("outputs")
JSONL_PATH = OUTPUT_DIR / "prospects.jsonl"
CSV_PATH = OUTPUT_DIR / "pipeline.csv"
BRIEF_DIR = OUTPUT_DIR / "briefs"

CSV_COLUMNS = [
    "researched_at", "company", "domain", "person", "title", "seniority",
    "linkedin", "best_email", "email_confidence", "best_phone", "phone_kind",
    "fit_score", "top_signal", "subject", "disqualifiers", "brief",
]

CONFIDENCE_LABEL = {
    Confidence.VERIFIED: "VERIFIED (published, with a source)",
    Confidence.PATTERN_HIGH: "LIKELY (format confirmed from a real address)",
    Confidence.PATTERN_GUESS: "GUESS (unverified - check before sending)",
    Confidence.UNKNOWN: "UNKNOWN",
}


def _ensure_dirs() -> None:
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)


# NOTE: the path arguments below default to None rather than to the module
# constants. A default argument is evaluated once, when the module is imported,
# so writing `path: Path = JSONL_PATH` would freeze the value and silently
# ignore any later change to it - including a test redirecting output to a temp
# directory. Resolving inside the function keeps these honestly configurable.
def _jsonl(path: Optional[Path]) -> Path:
    return path if path is not None else JSONL_PATH


def _csv(path: Optional[Path]) -> Path:
    return path if path is not None else CSV_PATH


def existing_keys(path: Optional[Path] = None) -> set[tuple[str, str]]:
    """(company, person) pairs already on disk, for deduping a re-run."""
    path = _jsonl(path)
    keys: set[tuple[str, str]] = set()
    if not path.exists():
        return keys
    for record in read_all(path):
        keys.add(
            (
                (record.company.name or "").strip().lower(),
                (record.person.full_name or "").strip().lower(),
            )
        )
    return keys


def read_all(path: Optional[Path] = None) -> Iterator[Prospect]:
    path = _jsonl(path)
    if not path.exists():
        return
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield Prospect.model_validate_json(line)
            except Exception:
                continue  # a partially written line shouldn't kill a report


def append_jsonl(prospect: Prospect, path: Optional[Path] = None) -> None:
    path = _jsonl(path)
    _ensure_dirs()
    with path.open("a") as fh:
        fh.write(prospect.model_dump_json() + "\n")


def write_csv(prospects: Iterable[Prospect], path: Optional[Path] = None) -> None:
    """Rewrite the whole CSV. Small files; simpler than patching rows."""
    path = _csv(path)
    _ensure_dirs()
    rows: List[dict] = []
    for p in prospects:
        email = p.best_email
        phone = p.best_phone
        rows.append(
            {
                "researched_at": p.researched_at or "",
                "company": p.company.name,
                "domain": p.company.domain or "",
                "person": p.person.full_name or "",
                "title": p.person.title or "",
                "seniority": p.person.seniority or "",
                "linkedin": p.person.linkedin_url or "",
                "best_email": email.address if email else "",
                "email_confidence": email.confidence.value if email else "none",
                "best_phone": phone.number if phone else "",
                "phone_kind": phone.kind if phone else "",
                "fit_score": p.fit_score,
                "top_signal": p.signals[0].signal if p.signals else "",
                "subject": (
                    p.outreach.subject_options[0]
                    if p.outreach and p.outreach.subject_options
                    else ""
                ),
                "disqualifiers": "; ".join(p.disqualifiers),
                "brief": str(BRIEF_DIR / f"{p.slug}.md"),
            }
        )
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def render_brief(
    prospect: Prospect,
    *,
    warnings: Optional[List[str]] = None,
    lint: Optional[List[str]] = None,
    notes: Optional[str] = None,
) -> str:
    """The one-pager you read before you dial."""
    p = prospect
    out: List[str] = []
    w = out.append

    w(f"# {p.person.full_name or 'Unknown contact'} - {p.company.name}")
    w("")
    w(f"**Fit score: {p.fit_score}/100** - {p.fit_rationale}")
    w("")

    w("## Who")
    w(f"- **Name:** {p.person.full_name or '_not found_'}")
    w(f"- **Title:** {p.person.title or '_not found_'}")
    if p.person.seniority:
        w(f"- **Seniority:** {p.person.seniority}")
    if p.person.location:
        w(f"- **Location:** {p.person.location}")
    if p.person.linkedin_url:
        w(f"- **LinkedIn:** {p.person.linkedin_url}")
    if p.person.reports_to:
        w(f"- **Reports to:** {p.person.reports_to} (escalation path)")
    if p.person.why_this_person:
        w(f"- **Why them:** {p.person.why_this_person}")
    w("")

    w("## Company")
    w(f"- **{p.company.name}**" + (f" - {p.company.domain}" if p.company.domain else ""))
    if p.company.what_they_do:
        w(f"- {p.company.what_they_do}")
    for label, value in (
        ("Employees", p.company.employee_estimate),
        ("Revenue", p.company.revenue_estimate),
        ("HQ", p.company.hq_location),
        ("Industry", p.company.industry),
    ):
        if value:
            w(f"- **{label}:** {value}")
    w("")

    w("## How to reach them")
    if p.emails:
        w("")
        w("| Email | Confidence | Basis |")
        w("|---|---|---|")
        for e in p.emails:
            basis = e.basis.replace("|", "/")
            if e.source_url:
                basis += f" ([source]({e.source_url}))"
            w(f"| `{e.address}` | {CONFIDENCE_LABEL[e.confidence]} | {basis} |")
    else:
        w("- **No email found.**")
    w("")
    if p.phones:
        w("| Phone | Type | Confidence | Basis |")
        w("|---|---|---|---|")
        for ph in p.phones:
            basis = ph.basis.replace("|", "/")
            if ph.source_url:
                basis += f" ([source]({ph.source_url}))"
            w(f"| `{ph.number}` | {ph.kind} | {CONFIDENCE_LABEL[ph.confidence]} | {basis} |")
    else:
        w("- **No phone number found.**")
    w("")

    if warnings:
        w("## ⚠ Before you send")
        for item in warnings:
            w(f"- {item}")
        w("")

    if p.signals:
        w("## Why now")
        for s in p.signals:
            date = f" _({s.date})_" if s.date else ""
            src = f" [source]({s.source_url})" if s.source_url else ""
            w(f"- **{s.signal}**{date} - {s.why_it_matters}.{src}")
        w("")

    if p.disqualifiers:
        w("## Reasons to skip")
        for d in p.disqualifiers:
            w(f"- {d}")
        w("")

    if p.outreach:
        o = p.outreach
        w("## The first approach")
        w("")
        w("**Subject lines:**")
        for s in o.subject_options:
            w(f"- {s}")
        w("")
        w("**Email:**")
        w("")
        w("```text")
        w(o.email_body.strip())
        w("```")
        w("")
        w("**Cold call opener:**")
        w("")
        w(f"> {o.call_opener.strip()}")
        w("")
        w("**Voicemail:**")
        w("")
        w(f"> {o.voicemail.strip()}")
        w("")
        w("**LinkedIn DM:**")
        w("")
        w(f"> {o.linkedin_dm.strip()}")
        w("")

        if o.followups:
            w("**Follow-up sequence:**")
            w("")
            for f in sorted(o.followups, key=lambda x: x.send_on_day):
                w(f"- **Day {f.send_on_day} ({f.channel}):** {f.body.strip()}")
            w("")

        w("## When they push back")
        w("")
        for i, obj in enumerate(o.objections, 1):
            w(f"**{i}. \"{obj.objection}\"**")
            w("")
            w(f"_What's behind it:_ {obj.why_it_comes_up}")
            w("")
            w(f"_Say:_ {obj.response}")
            w("")

        if o.personalization_facts:
            w("## Fact-check before sending")
            w("")
            w("Every claim the copy makes about them. Confirm these are true:")
            w("")
            for fact in o.personalization_facts:
                w(f"- [ ] {fact}")
            w("")

    if lint:
        w("## Draft warnings")
        for item in lint:
            w(f"- {item}")
        w("")

    if p.sources:
        w("## Sources")
        for s in p.sources:
            w(f"- [{s.what_it_supports}]({s.url})")
        w("")

    if notes:
        w("<details>")
        w("<summary>Raw research notes (what the agent actually read)</summary>")
        w("")
        w(notes.strip())
        w("")
        w("</details>")
        w("")

    w("---")
    w(
        f"_Researched {p.researched_at or 'unknown'} by {p.model_used or 'unknown model'}. "
        "Verify contact details before sending._"
    )
    return "\n".join(out)


def write_brief(prospect: Prospect, content: str) -> Path:
    _ensure_dirs()
    path = BRIEF_DIR / f"{prospect.slug}.md"
    path.write_text(content)
    return path

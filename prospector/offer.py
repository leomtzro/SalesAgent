"""Loads config/offer.yaml and turns it into prompt text.

Kept separate so that changing what you sell never means touching agent code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

DEFAULT_OFFER_PATH = Path("config/offer.yaml")

PLACEHOLDERS = {"Your Company", "Your Name", "you@yourcompany.com"}


class OfferConfigError(Exception):
    pass


@dataclass
class Voice:
    sender_name: str = "Your Name"
    sender_title: str = ""
    sender_phone: str = ""
    sender_email: str = ""
    tone: str = "direct"
    max_email_words: int = 110
    banned_phrases: List[str] = field(default_factory=list)


@dataclass
class Offer:
    company_name: str
    what_you_sell: str
    problems_you_solve: List[str]
    ideal_customer: Dict[str, Any]
    buying_signals: List[str]
    disqualifying_signals: List[str]
    proof_points: List[str]
    reference_customers: List[str]
    voice: Voice
    call_to_action: str
    opt_out_line: str

    @property
    def target_titles(self) -> List[str]:
        return list(self.ideal_customer.get("target_titles") or [])

    @property
    def avoid_titles(self) -> List[str]:
        return list(self.ideal_customer.get("avoid_titles") or [])

    def unfilled_placeholders(self) -> List[str]:
        """Which template values the user hasn't replaced yet."""
        found = []
        if self.company_name in PLACEHOLDERS:
            found.append("company_name")
        if self.voice.sender_name in PLACEHOLDERS:
            found.append("voice.sender_name")
        if self.voice.sender_email in PLACEHOLDERS:
            found.append("voice.sender_email")
        if "Example:" in self.what_you_sell or self.what_you_sell.strip().startswith(
            "One or two sentences"
        ):
            found.append("what_you_sell")
        return found

    # --- prompt rendering ---------------------------------------------------

    def as_prompt_block(self) -> str:
        """The offer, rendered for the system prompt.

        This string is stable across every prospect in a batch, which is what
        makes it cacheable - see client.py.
        """
        ic = self.ideal_customer

        def bullets(items: List[str], empty: str = "(none provided)") -> str:
            items = [i for i in (items or []) if str(i).strip()]
            return "\n".join(f"  - {i}" for i in items) if items else f"  {empty}"

        return f"""<offer>
SELLER: {self.company_name}

WHAT THEY SELL:
  {self.what_you_sell.strip()}

PROBLEMS IT SOLVES:
{bullets(self.problems_you_solve)}

IDEAL CUSTOMER:
  Industries: {", ".join(ic.get("industries") or []) or "any"}
  Company size: {ic.get("company_size") or "any"}
  Revenue: {ic.get("revenue_range") or "any"}
  Geographies: {", ".join(ic.get("geographies") or []) or "any"}
  Target titles, in priority order:
{bullets(self.target_titles)}
  Titles to avoid:
{bullets(self.avoid_titles, "(none)")}

BUYING SIGNALS - search for these; they raise fit_score:
{bullets(self.buying_signals)}

DISQUALIFYING SIGNALS - these lower fit_score and belong in disqualifiers:
{bullets(self.disqualifying_signals, "(none)")}

PROOF THE SELLER IS ALLOWED TO CLAIM - you may not claim any result, metric,
or outcome that is not in this list. If the list is empty, make no claims:
{bullets(self.proof_points, "(no proof points - make NO results claims)")}

CUSTOMERS WHO MAY BE NAMED:
{bullets(self.reference_customers, "(none - do not name any customer)")}
</offer>"""

    def as_voice_block(self) -> str:
        banned = ", ".join(f'"{p}"' for p in self.voice.banned_phrases) or "(none)"
        return f"""<voice>
Signed by: {self.voice.sender_name}{f", {self.voice.sender_title}" if self.voice.sender_title else ""}
Reply-to: {self.voice.sender_email or "(not set)"}
Phone: {self.voice.sender_phone or "(not set)"}
Tone: {self.voice.tone}
Hard limit on the first email: {self.voice.max_email_words} words.
NEVER use these phrases: {banned}
The ask, used verbatim or very close to it: "{self.call_to_action}"
Every email ends with this opt-out line, verbatim: "{self.opt_out_line}"
</voice>"""


def load_offer(path: Path | str = DEFAULT_OFFER_PATH) -> Offer:
    path = Path(path)
    if not path.exists():
        raise OfferConfigError(
            f"No offer config at {path}. Copy the template from "
            f"config/offer.yaml and fill it in."
        )
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise OfferConfigError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise OfferConfigError(f"{path} should be a mapping of keys to values.")

    required = ["company_name", "what_you_sell", "ideal_customer"]
    missing = [k for k in required if not raw.get(k)]
    if missing:
        raise OfferConfigError(
            f"{path} is missing required keys: {', '.join(missing)}"
        )

    ic = raw.get("ideal_customer") or {}
    if not isinstance(ic, dict):
        raise OfferConfigError("ideal_customer should be a mapping.")
    if not ic.get("target_titles"):
        raise OfferConfigError(
            "ideal_customer.target_titles is required - the agent needs to know "
            "which job titles to look for."
        )

    voice_raw = raw.get("voice") or {}
    known = {f.name for f in Voice.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    voice = Voice(**{k: v for k, v in voice_raw.items() if k in known})

    return Offer(
        company_name=str(raw["company_name"]),
        what_you_sell=str(raw["what_you_sell"]),
        problems_you_solve=list(raw.get("problems_you_solve") or []),
        ideal_customer=ic,
        buying_signals=list(raw.get("buying_signals") or []),
        disqualifying_signals=list(raw.get("disqualifying_signals") or []),
        proof_points=list(raw.get("proof_points") or []),
        reference_customers=list(raw.get("reference_customers") or []),
        voice=voice,
        call_to_action=str(raw.get("call_to_action") or "Open to a short call?"),
        opt_out_line=str(
            raw.get("opt_out_line")
            or "If this isn't relevant, reply 'no' and I won't follow up."
        ),
    )

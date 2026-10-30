"""The Claude API layer: client setup, the web-research loop, and structured
extraction.

Two things in here are worth understanding, because they're the whole reason
this works:

1. WEB SEARCH IS SERVER-SIDE. `web_search` and `web_fetch` are tools that run
   on Anthropic's infrastructure, not here. We declare them and the model
   searches the live web by itself - no scraper to maintain, no search API key.

2. STRUCTURED OUTPUT. The extraction call is handed a Pydantic model as its
   required output shape, so the result is a validated Python object, not a
   blob of text we have to regex. Fields the model can't find come back null.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

import anthropic
from pydantic import BaseModel

log = logging.getLogger("prospector")

DEFAULT_MODEL = "claude-opus-5-5"

# The dynamic-filtering web tools. These require Opus 4.6+ / Sonnet 4.6+,
# which the default model satisfies.
WEB_SEARCH_TOOL = "web_search_20260209"
WEB_FETCH_TOOL = "web_fetch_20260209"

T = TypeVar("T", bound=BaseModel)


class ProspectorError(Exception):
    pass


class MissingAPIKey(ProspectorError):
    pass


def _model() -> str:
    return os.environ.get("PROSPECTOR_MODEL", DEFAULT_MODEL)


def _extract_model() -> str:
    return os.environ.get("PROSPECTOR_EXTRACT_MODEL", _model())


def _max_searches() -> int:
    try:
        return max(1, int(os.environ.get("PROSPECTOR_MAX_SEARCHES", "12")))
    except ValueError:
        return 12


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env reader, so there's no extra dependency to install.

    Values already in the environment win - that way `ANTHROPIC_API_KEY=... python
    -m prospector` still overrides the file.
    """
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def build_client() -> anthropic.Anthropic:
    """Create the API client.

    An unset ANTHROPIC_API_KEY is not automatically fatal: the SDK also picks up
    ANTHROPIC_AUTH_TOKEN or a profile saved by `ant auth login`. We only fail
    loudly when none of those is present.
    """
    load_dotenv()
    has_creds = bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or os.path.isdir(os.path.expanduser("~/.config/anthropic"))
    )
    if not has_creds:
        raise MissingAPIKey(
            "No Anthropic credentials found.\n\n"
            "Fix it one of two ways:\n"
            "  1. cp .env.example .env  and put your key in it\n"
            "     (get one at https://console.anthropic.com/settings/keys)\n"
            "  2. export ANTHROPIC_API_KEY=sk-ant-...\n"
        )
    # 10 minutes is the SDK default; research turns with many searches can be
    # slow, so give them room rather than racing a timeout.
    return anthropic.Anthropic(timeout=900.0, max_retries=3)


def web_tools(max_uses: Optional[int] = None) -> List[Dict[str, Any]]:
    """The two server-side tools the researcher gets.

    `max_uses` is a real cost control: each search is billed, so capping it
    bounds the spend per prospect.
    """
    uses = max_uses or _max_searches()
    return [
        {"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": uses},
        # web_fetch can only open URLs already in the conversation, which in
        # practice means URLs that web_search just returned.
        {"type": WEB_FETCH_TOOL, "name": "web_fetch", "max_uses": uses},
    ]


def _server_tool_errors(content: List[Any]) -> List[str]:
    """Pull out web tool failures.

    Server tools do not raise. A failed search returns HTTP 200 with a result
    block whose `content` is an error object instead of a list - so a silent
    'I couldn't find anything' is often really a quota error. We surface it.
    """
    errors: List[str] = []
    for block in content:
        btype = getattr(block, "type", None)
        if btype not in ("web_search_tool_result", "web_fetch_tool_result"):
            continue
        inner = getattr(block, "content", None)
        # Success is a list of results; an error is a single object.
        if isinstance(inner, list):
            continue
        code = getattr(inner, "error_code", None)
        if code:
            errors.append(f"{btype}: {code}")
    return errors


def _text_of(content: List[Any]) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text")


def run_research_turn(
    client: anthropic.Anthropic,
    *,
    system: str,
    user_prompt: str,
    max_uses: Optional[int] = None,
    effort: str = "high",
    max_tokens: int = 16000,
    max_restarts: int = 6,
) -> Tuple[str, Dict[str, Any]]:
    """Let the model research on the live web until it's done talking.

    Returns (notes_text, stats).

    The loop exists for one reason: a long research turn can come back with
    `stop_reason == "pause_turn"`, meaning the server-side tool run was
    interrupted partway. The fix is to send the partial assistant turn back so
    it resumes. Without this, you silently get half an answer.
    """
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    stats: Dict[str, Any] = {
        "restarts": 0,
        "searches": 0,
        "fetches": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "tool_errors": [],
    }

    for attempt in range(max_restarts + 1):
        response = client.messages.create(
            model=_model(),
            max_tokens=max_tokens,
            system=system,
            messages=messages,  # type: ignore[arg-type]
            tools=web_tools(max_uses),  # type: ignore[arg-type]
            output_config={"effort": effort},
        )

        usage = getattr(response, "usage", None)
        if usage:
            stats["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
            stats["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "server_tool_use":
                name = getattr(block, "name", "")
                if name == "web_search":
                    stats["searches"] += 1
                elif name == "web_fetch":
                    stats["fetches"] += 1
        stats["tool_errors"].extend(_server_tool_errors(response.content))

        # A safety refusal is an HTTP 200, so check before reading content.
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            raise ProspectorError(
                "The model declined this research request "
                f"(category: {getattr(detail, 'category', 'unknown')}). "
                "This usually means the target or the prompt looked abusive - "
                "check config/offer.yaml and the company name."
            )

        if response.stop_reason == "pause_turn":
            # Mid-turn pause: hand the partial turn back so it picks up.
            messages = [
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": response.content},
            ]
            stats["restarts"] = attempt + 1
            log.info("research turn paused, resuming (%s)", stats["restarts"])
            continue

        if response.stop_reason == "max_tokens":
            log.warning("research notes hit max_tokens; they may be truncated")

        text = _text_of(response.content)
        if stats["tool_errors"]:
            log.warning("web tool errors: %s", ", ".join(stats["tool_errors"]))
        return text, stats

    raise ProspectorError(
        f"Research turn still paused after {max_restarts} restarts. "
        "Try a lower PROSPECTOR_MAX_SEARCHES."
    )


def preflight(client: anthropic.Anthropic) -> Dict[str, Any]:
    """One minimal real API call, to prove the whole path works.

    Checks in a single request that the key is accepted, the model is
    available, the server-side web search tool is enabled for this account,
    and a search actually returns something. Costs a fraction of a cent -
    far less than discovering any of this halfway through a 200-company batch.
    """
    out: Dict[str, Any] = {
        "model": _model(),
        "searched": False,
        "search_results": 0,
        "tool_errors": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "answer": "",
    }
    response = client.messages.create(
        model=_model(),
        max_tokens=1024,
        system=(
            "You are checking that web search works. Run exactly one search, "
            "then reply with a single short sentence."
        ),
        messages=[
            {
                "role": "user",
                "content": "Search the web for Anthropic's headquarters city, "
                "then tell me the city in one sentence.",
            }
        ],
        tools=[{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 1}],
        output_config={"effort": "low"},
    )

    usage = getattr(response, "usage", None)
    if usage:
        out["input_tokens"] = getattr(usage, "input_tokens", 0) or 0
        out["output_tokens"] = getattr(usage, "output_tokens", 0) or 0

    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "server_tool_use" and getattr(block, "name", "") == "web_search":
            out["searched"] = True
        if btype == "web_search_tool_result":
            inner = getattr(block, "content", None)
            if isinstance(inner, list):
                out["search_results"] += len(inner)

    out["tool_errors"] = _server_tool_errors(response.content)
    out["stop_reason"] = response.stop_reason
    out["answer"] = _text_of(response.content).strip()
    return out


def extract(
    client: anthropic.Anthropic,
    *,
    schema: Type[T],
    system: str,
    user_prompt: str,
    effort: str = "medium",
    max_tokens: int = 16000,
) -> T:
    """Turn text into a validated instance of `schema`.

    No tools here on purpose - this call only reshapes text that the research
    step already gathered. Giving it search access is how invented details get
    in.
    """
    response = client.messages.parse(
        model=_extract_model(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=schema,
        output_config={"effort": effort},
    )
    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        raise ProspectorError(
            f"Model declined to extract (category: {getattr(detail, 'category', 'unknown')})."
        )
    parsed = response.parsed_output
    if parsed is None:
        raise ProspectorError(
            "Structured extraction returned nothing. The research notes were "
            "probably empty - check for web tool errors above."
        )
    return parsed

"""Runs the real pipeline against a local server that speaks the Messages API.

The mocked test in test_pipeline_e2e.py substitutes the client object, so it
never exercises the SDK itself. This one does: requests go through the real
anthropic client, get serialized by the real SDK, and arrive here as JSON we
can inspect.

That makes it the test that catches a malformed request - a tool type the API
would reject, an output_config the SDK drops, a schema too deep to serialize -
without spending a cent or needing a key.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import anthropic
import pytest

from prospector.models import Outreach, ResearchFindings

# Captured request bodies, in order.
REQUESTS: list[dict] = []

RESEARCH_NOTES = """## Company
Acme Supplements (acmesupplements.com), creatine brand.

## Person
Jane Doe, VP of Operations.

## Contact details found
- info@acmesupplements.com (https://acmesupplements.com/contact)

## Buying signals
- Raised $12M Series A 2026-03 (https://news.example/acme)

## Sources
- https://acmesupplements.com/team
"""

FINDINGS_JSON = {
    "company": {
        "name": "Acme Supplements",
        "domain": "acmesupplements.com",
        "what_they_do": "Creatine brand",
        "employee_estimate": "120",
        "revenue_estimate": None,
        "hq_location": "Austin, TX",
        "industry": "Supplements",
    },
    "person": {
        "full_name": "Jane Doe",
        "title": "VP of Operations",
        "seniority": "vp",
        "linkedin_url": None,
        "location": None,
        "why_this_person": "Owns fulfillment.",
        "reports_to": None,
    },
    "emails": [
        {
            "address": "info@acmesupplements.com",
            "confidence": "verified",
            "basis": "contact page",
            "source_url": "https://acmesupplements.com/contact",
        }
    ],
    "phones": [],
    "signals": [
        {
            "signal": "Raised $12M Series A",
            "why_it_matters": "Scaling fulfillment",
            "date": "2026-03",
            "source_url": "https://news.example/acme",
        }
    ],
    "fit_score": 86,
    "fit_rationale": "Right size, right industry, recent raise.",
    "disqualifiers": [],
    "sources": [
        {"url": "https://acmesupplements.com/team", "what_it_supports": "Jane's title"}
    ],
    "other_emails_at_domain": ["info@acmesupplements.com"],
}

OUTREACH_JSON = {
    "subject_options": ["fulfillment after the raise", "3PL mis-ships", "quick one"],
    "email_body": (
        "Jane - saw the $12M in March.\n\nWhen supplement brands scale that fast "
        "the 3PL starts mis-shipping. We cut one brand from 4.1% to 0.6% in 90 "
        "days.\n\nWorth a 15-minute call this week to see if the numbers line up?"
        "\n\nIf this isn't relevant, reply 'no' and I won't follow up."
    ),
    "followups": [
        {"send_on_day": 3, "channel": "call", "body": "Call the switchboard."},
        {"send_on_day": 7, "channel": "email", "body": "Send the teardown."},
    ],
    "call_opener": "Jane, it's Leo - you raised in March, are mis-ships showing up yet?",
    "voicemail": "Jane, Leo here about fulfillment. Calling Thursday.",
    "linkedin_dm": "Saw the Series A. We cut mis-ships 4.1% to 0.6%. Worth 15 minutes?",
    "objections": [
        {
            "objection": "We just signed with our 3PL.",
            "why_it_comes_up": "Switching cost fear.",
            "response": "Not asking you to switch. What's your mis-ship rate?",
        },
        {
            "objection": "Send me something.",
            "why_it_comes_up": "Brush-off.",
            "response": "Will do. What number would you want it to move?",
        },
    ],
    "personalization_facts": ["Acme raised $12M in March 2026"],
}


def _message(content_blocks, stop_reason="end_turn"):
    return {
        "id": "msg_wiretest",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 4200, "output_tokens": 900},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output clean
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        REQUESTS.append(body)

        fmt = (body.get("output_config") or {}).get("format")
        if fmt:
            # A structured-output call. Decide which schema was asked for by
            # looking at the schema the SDK actually serialized.
            props = set((fmt.get("schema") or {}).get("properties", {}))
            payload = (
                OUTREACH_JSON if "subject_options" in props else FINDINGS_JSON
            )
            reply = _message([{"type": "text", "text": json.dumps(payload)}])
        else:
            reply = _message(
                [
                    {
                        "type": "server_tool_use",
                        "id": "srvtoolu_1",
                        "name": "web_search",
                        "input": {"query": "Acme Supplements VP operations"},
                    },
                    {
                        "type": "web_search_tool_result",
                        "tool_use_id": "srvtoolu_1",
                        "content": [
                            {
                                "type": "web_search_result",
                                "title": "Acme team",
                                "url": "https://acmesupplements.com/team",
                                "encrypted_content": "x",
                                "page_age": None,
                            }
                        ],
                    },
                    {"type": "text", "text": RESEARCH_NOTES},
                ]
            )

        raw = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture
def live(server, tmp_path, monkeypatch):
    """A real anthropic client pointed at the local server."""
    from prospector import store

    REQUESTS.clear()
    monkeypatch.setattr(store, "JSONL_PATH", tmp_path / "p.jsonl")
    monkeypatch.setattr(store, "CSV_PATH", tmp_path / "p.csv")
    monkeypatch.setattr(store, "BRIEF_DIR", tmp_path / "briefs")
    monkeypatch.setattr("prospector.contact.domain_accepts_mail", lambda d: True)
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    return anthropic.Anthropic(api_key="sk-ant-wiretest", base_url=server)


class TestRealSerialization:
    def test_pipeline_completes_over_the_wire(self, live):
        from prospector import pipeline
        from prospector.offer import load_offer

        result = pipeline.run_one(
            pipeline.Target(company="Acme Supplements", domain="acmesupplements.com"),
            client=live,
            offer=load_offer("config/offer.yaml"),
        )
        assert result.ok, result.error
        assert result.prospect.person.full_name == "Jane Doe"
        assert result.prospect.outreach is not None
        assert result.brief_path.exists()

    def test_research_request_is_well_formed(self, live):
        from prospector import pipeline
        from prospector.offer import load_offer

        pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=live,
            offer=load_offer("config/offer.yaml"),
        )
        req = REQUESTS[0]
        assert req["model"] == "claude-opus-5-5"

        # Tools serialized with the right types and the cost cap intact.
        by_type = {t["type"]: t for t in req["tools"]}
        assert "web_search_20260209" in by_type
        assert "web_fetch_20260209" in by_type
        assert by_type["web_search_20260209"]["name"] == "web_search"
        assert isinstance(by_type["web_search_20260209"]["max_uses"], int)

        # Effort survived serialization - it is easy to pass this in a way the
        # SDK silently drops.
        assert req["output_config"]["effort"] == "high"

        # No thinking param on opus-5-5: budget_tokens is rejected there, and
        # an explicit disable is a 400.
        assert "thinking" not in req or req["thinking"] is None

        # The offer must actually reach the model.
        assert "VP of Operations" in req["system"]
        assert "NEVER write an email address" in req["system"]

    def test_structured_calls_send_a_json_schema(self, live):
        from prospector import pipeline
        from prospector.offer import load_offer

        pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=live,
            offer=load_offer("config/offer.yaml"),
        )
        structured = [r for r in REQUESTS if (r.get("output_config") or {}).get("format")]
        assert len(structured) == 2, "extraction and outreach should both be structured"

        for req in structured:
            fmt = req["output_config"]["format"]
            assert fmt["type"] == "json_schema"
            schema = fmt["schema"]
            assert schema["type"] == "object"
            # The API requires a closed schema for structured output.
            assert schema.get("additionalProperties") is False
            # No $ref/$defs left dangling - nested models must be inlined or
            # defined, or the API rejects the schema.
            if "$defs" in json.dumps(schema):
                assert "$defs" in schema, "refs present but no definitions"
            # Structured-output calls must not carry web tools.
            assert not req.get("tools")

    def test_both_schemas_serialize_without_truncation(self, live):
        """The schemas are deep. Confirm the whole shape arrives."""
        from prospector import pipeline
        from prospector.offer import load_offer

        pipeline.run_one(
            pipeline.Target(company="Acme Supplements"),
            client=live,
            offer=load_offer("config/offer.yaml"),
        )
        schemas = [
            r["output_config"]["format"]["schema"]
            for r in REQUESTS
            if (r.get("output_config") or {}).get("format")
        ]
        blob = json.dumps(schemas)
        # Fields from the deepest nesting in each model.
        for field in ("other_emails_at_domain", "why_this_person", "objections",
                      "why_it_comes_up", "send_on_day", "personalization_facts"):
            assert field in blob, f"{field} missing from serialized schema"

    def test_pause_turn_resumes_over_the_wire(self, server, tmp_path, monkeypatch):
        """A paused research turn must resume through the real client."""
        from prospector import store
        from prospector.client import run_research_turn

        REQUESTS.clear()
        state = {"n": 0}
        original = Handler.do_POST

        def paused_once(self):
            state["n"] += 1
            if state["n"] == 1:
                length = int(self.headers.get("content-length", 0))
                REQUESTS.append(json.loads(self.rfile.read(length) or b"{}"))
                raw = json.dumps(
                    _message([{"type": "text", "text": "partial"}], "pause_turn")
                ).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            original(self)

        monkeypatch.setattr(Handler, "do_POST", paused_once)
        client = anthropic.Anthropic(api_key="sk-ant-wiretest", base_url=server)
        notes, stats = run_research_turn(
            client, system="s", user_prompt="find Acme"
        )
        assert stats["restarts"] == 1
        assert "Acme Supplements" in notes
        # The resumed request must carry the paused assistant turn back.
        assert REQUESTS[1]["messages"][-1]["role"] == "assistant"


# --- preflight --------------------------------------------------------------
#
# `doctor --live` is the first thing a new user runs against the real API, so
# its failure detection has to be right: a silent pass when web search is off
# would send them into a batch that researches nothing.


def _serve(handler_body):
    """Run a one-off server whose POST handler is `handler_body(self)`."""
    cls = type("OneOff", (BaseHTTPRequestHandler,), {
        "do_POST": handler_body,
        "log_message": lambda self, *a: None,
    })
    httpd = HTTPServer(("127.0.0.1", 0), cls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _respond(self, blocks, stop_reason="end_turn"):
    length = int(self.headers.get("content-length", 0))
    REQUESTS.append(json.loads(self.rfile.read(length) or b"{}"))
    raw = json.dumps(_message(blocks, stop_reason)).encode()
    self.send_response(200)
    self.send_header("content-type", "application/json")
    self.send_header("content-length", str(len(raw)))
    self.end_headers()
    self.wfile.write(raw)


SEARCH_OK = [
    {"type": "server_tool_use", "id": "s1", "name": "web_search",
     "input": {"query": "Anthropic headquarters"}},
    {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [
        {"type": "web_search_result", "title": "Anthropic",
         "url": "https://anthropic.com", "encrypted_content": "x",
         "page_age": None}]},
    {"type": "text", "text": "San Francisco."},
]


class TestPreflight:
    def test_detects_a_healthy_setup(self, tmp_path):
        from prospector.client import preflight

        REQUESTS.clear()
        httpd = _serve(lambda self: _respond(self, SEARCH_OK))
        try:
            client = anthropic.Anthropic(
                api_key="sk-ant-t",
                base_url=f"http://127.0.0.1:{httpd.server_port}",
            )
            out = preflight(client)
        finally:
            httpd.shutdown()

        assert out["searched"] is True
        assert out["search_results"] == 1
        assert out["tool_errors"] == []
        assert "San Francisco" in out["answer"]
        assert out["input_tokens"] > 0

        # One search only - the check must stay cheap.
        req = REQUESTS[0]
        assert req["tools"][0]["max_uses"] == 1
        assert req["output_config"]["effort"] == "low"
        assert req["max_tokens"] <= 1024

    def test_notices_when_search_never_ran(self, tmp_path):
        """The silent failure that would waste a whole batch."""
        from prospector.client import preflight

        REQUESTS.clear()
        httpd = _serve(
            lambda self: _respond(
                self, [{"type": "text", "text": "I think it's San Francisco."}]
            )
        )
        try:
            client = anthropic.Anthropic(
                api_key="sk-ant-t",
                base_url=f"http://127.0.0.1:{httpd.server_port}",
            )
            out = preflight(client)
        finally:
            httpd.shutdown()

        assert out["searched"] is False, "must not report a search that never ran"
        assert out["search_results"] == 0

    def test_surfaces_a_server_tool_error(self, tmp_path):
        """Web tool errors arrive as HTTP 200 - they must not pass silently."""
        from prospector.client import preflight

        REQUESTS.clear()
        httpd = _serve(
            lambda self: _respond(self, [
                {"type": "server_tool_use", "id": "s1", "name": "web_search",
                 "input": {"query": "x"}},
                {"type": "web_search_tool_result", "tool_use_id": "s1",
                 "content": {"type": "web_search_tool_result_error",
                             "error_code": "max_uses_exceeded"}},
                {"type": "text", "text": "Couldn't search."},
            ])
        )
        try:
            client = anthropic.Anthropic(
                api_key="sk-ant-t",
                base_url=f"http://127.0.0.1:{httpd.server_port}",
            )
            out = preflight(client)
        finally:
            httpd.shutdown()

        assert "max_uses_exceeded" in " ".join(out["tool_errors"])

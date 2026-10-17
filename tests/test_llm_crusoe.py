import io
import json
import urllib.error

import pytest

from schema_voyager import llm as llm_module
from schema_voyager.llm import CRUSOE_MODEL, LLM, LLMError, fixture_prior_rules
from schema_voyager.models import Action

PLAN = {"summary": "mine a log", "actions": [{"kind": "mine", "target": "oak_log", "tool": "", "times": 1}]}


class FakeEndpoint:
    """Stands in for urlopen: records each request and answers with the next scripted reply,
    either a reply text or an HTTPError."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append({"url": request.full_url, "headers": dict(request.header_items()),
                              "body": json.loads(request.data)})
        reply = self.replies.pop(0)
        if isinstance(reply, urllib.error.HTTPError):
            raise reply
        body = {"choices": [{"message": {"content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
        return io.BytesIO(json.dumps(body).encode())


def _http_error(code, detail, headers=None):
    return urllib.error.HTTPError("https://x", code, "error", headers or {}, io.BytesIO(detail.encode()))


@pytest.fixture
def crusoe(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUSOE_API_KEY", "test-key")
    monkeypatch.delenv("SCHEMA_VOYAGER_BASE_URL", raising=False)
    monkeypatch.delenv("SCHEMA_VOYAGER_MODEL", raising=False)

    def make(*replies):
        endpoint = FakeEndpoint(*replies)
        endpoint.waits = []
        monkeypatch.setattr(llm_module.urllib.request, "urlopen", endpoint)
        monkeypatch.setattr(llm_module.time, "sleep", endpoint.waits.append)
        return LLM(mode="live", provider="crusoe", cache_path=tmp_path / "cache.jsonl"), endpoint

    return make


def test_claude_stays_the_default(monkeypatch):
    monkeypatch.delenv("SCHEMA_VOYAGER_PROVIDER", raising=False)
    monkeypatch.delenv("SCHEMA_VOYAGER_MODEL", raising=False)
    assert LLM(mode="fake").provider == "anthropic" and LLM(mode="fake").model == "claude-sonnet-5-5"
    assert LLM(mode="fake", provider="crusoe").model == CRUSOE_MODEL
    with pytest.raises(LLMError):
        LLM(mode="fake", provider="openrouter")


def test_a_plan_is_one_structured_request_without_thinking(crusoe, tmp_path):
    llm, endpoint = crusoe(json.dumps(PLAN))
    summary, actions = llm.baseline_plan("Goal: one log")
    assert summary == "mine a log" and actions == [Action("mine", "oak_log")]

    request = endpoint.requests[0]
    assert request["url"] == "https://api.inference.crusoecloud.com/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["body"]["model"] == CRUSOE_MODEL
    assert request["body"]["response_format"]["json_schema"]["strict"] is True
    assert request["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert [m["role"] for m in request["body"]["messages"]] == ["system", "user"]
    assert llm.calls[-1].tokens == 120
    record = json.loads((tmp_path / "cache.jsonl").read_text())
    assert record["purpose"] == "baseline_plan" and record["output_tokens"] == 20


def test_a_revision_thinks_and_leaked_thinking_is_stripped(crusoe):
    rule = next(r for r in fixture_prior_rules() if r.id == "mine_coal_ore")
    revision = {"rules": [{"action_type": "mine", "subject": "coal_ore", "tool_any": ["stone_pickaxe"], "consumes": [],
                           "station": "", "yields": [{"item": "coal", "count": 1}], "rationale": "needs stone"}]}
    llm, endpoint = crusoe(f"<think>wooden failed, so stone?</think>\n{json.dumps(revision)}")
    rules = llm.revise_rule(rule, "evidence", {"observed": {}}, ["coal"], "w1")
    assert endpoint.requests[0]["body"]["chat_template_kwargs"] == {"enable_thinking": True}
    assert rules[0].preconditions["tool_any"] == ["stone_pickaxe"] and rules[0].id == "mine_coal_ore.rev1@w1"


def test_without_structured_output_the_schema_goes_in_the_prompt(crusoe):
    llm, endpoint = crusoe(_http_error(400, '{"error": "response_format is not supported"}'),
                           f"Here you go:\n```json\n{json.dumps(PLAN)}\n```")
    assert llm.baseline_plan("Goal: one log")[0] == "mine a log"
    retry = endpoint.requests[1]["body"]
    assert "response_format" not in retry and "chat_template_kwargs" not in retry
    assert '"summary"' in retry["messages"][0]["content"]
    assert llm.calls[-1].tokens == 120  # the rejected request reported no usage


def test_a_reply_that_misses_the_schema_is_asked_for_once_more(crusoe):
    llm, endpoint = crusoe('{"summary": "no actions"}', json.dumps(PLAN))
    assert llm.baseline_plan("Goal: one log")[1] == [Action("mine", "oak_log")]
    assert len(endpoint.requests) == 2 and llm.calls[-1].tokens == 240

    llm, _ = crusoe("not json", '{"actions": "nope"}')
    with pytest.raises(LLMError):
        llm.baseline_plan("Goal: one log")


def test_server_errors_and_a_missing_key_are_llm_errors(crusoe, monkeypatch):
    llm, _ = crusoe(*[_http_error(500, "overloaded")] * 4)
    with pytest.raises(LLMError, match="500"):
        llm.baseline_plan("Goal: one log")

    monkeypatch.delenv("CRUSOE_API_KEY")
    llm, endpoint = crusoe(json.dumps(PLAN))
    with pytest.raises(LLMError, match="CRUSOE_API_KEY"):
        llm.baseline_plan("Goal: one log")
    assert endpoint.requests == []


def test_recordings_of_the_two_providers_do_not_mix(crusoe, tmp_path):
    llm, _ = crusoe(json.dumps(PLAN))
    llm.baseline_plan("Goal: one log")
    replay = LLM(mode="replay", provider="anthropic", cache_path=tmp_path / "cache.jsonl")
    with pytest.raises(LLMError, match="no recorded response"):
        replay.baseline_plan("Goal: one log")
    assert LLM(mode="replay", provider="crusoe", cache_path=tmp_path / "cache.jsonl").baseline_plan("Goal: one log")[0] == "mine a log"


def test_a_rate_limited_server_is_retried_after_a_wait(crusoe):
    llm, endpoint = crusoe(_http_error(429, "max_requests_limit"), _http_error(429, "max_requests_limit"), json.dumps(PLAN))
    assert llm.baseline_plan("Goal: one log")[0] == "mine a log"
    assert endpoint.waits == [10, 30] and len(endpoint.requests) == 3

    llm, endpoint = crusoe(_http_error(429, "slow down", {"Retry-After": "2"}), json.dumps(PLAN))
    llm.baseline_plan("Goal: one log")
    assert endpoint.waits == [2.0]

    llm, endpoint = crusoe(*[_http_error(503, "busy", {"Retry-After": "1"})] * 4)
    with pytest.raises(LLMError, match="503"):
        llm.baseline_plan("Goal: one log")
    assert len(endpoint.requests) == 4


def test_a_timeout_gets_one_more_try(crusoe, monkeypatch):
    llm, _ = crusoe()
    calls = []

    def times_out(request, timeout):
        calls.append(timeout)
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", times_out)
    with pytest.raises(LLMError, match="could not reach"):
        llm.baseline_plan("Goal: one log")
    assert calls == [llm_module.CRUSOE_TIMEOUT_S] * 2

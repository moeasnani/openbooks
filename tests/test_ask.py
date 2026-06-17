"""Tests for the natural-language Ask layer (``openbooks.ask``).

These do NOT hit any LLM provider: ``_chat_completion`` is monkeypatched to
scripted assistant turns so the tool-call loop is exercised deterministically.

They DO require the warehouse (for real tool dispatch against real rows),
skipping cleanly when it is absent.
"""

from __future__ import annotations

import os

import pytest

from openbooks import OpenBooks
from openbooks.ask import MAX_TOOL_ROWS, AskError, _truncate, ask

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAREHOUSE = os.path.join(REPO_ROOT, "warehouse.duckdb")

requires_warehouse = pytest.mark.skipif(
    not os.path.exists(WAREHOUSE),
    reason="warehouse.duckdb not present (data artifacts are not in git)",
)


@pytest.fixture(scope="module")
def ob():
    if not os.path.exists(WAREHOUSE):
        pytest.skip("warehouse.duckdb not present")
    instance = OpenBooks(WAREHOUSE)
    yield instance
    instance.close()


# ---------------------------------------------------------------------------
# Tool-spec / dispatch-table parity (the load-bearing invariant: every name
# the model is allowed to call must have a dispatch entry, and vice-versa).
# A drift here makes the model call a tool that returns {"error": "unknown
# tool"} — a silent correctness regression.
# ---------------------------------------------------------------------------


def _dispatch_tool_names() -> set[str]:
    """Names with entries in the ``_dispatch_tool`` lambda table."""
    import inspect
    import re

    import openbooks.ask as ask_mod

    src = inspect.getsource(ask_mod._dispatch_tool)
    # lines like:  "search": lambda: ...,
    return {m for m in re.findall(r'["\'](\w+)["\']\s*:\s*lambda', src)}


def _tool_spec_names() -> set[str]:
    return {t["function"]["name"] for t in __import__("openbooks.ask", fromlist=["_tool_specs"])._tool_specs()}


def test_tool_specs_and_dispatch_table_are_in_sync():
    specs = _tool_spec_names()
    dispatch = _dispatch_tool_names()
    assert specs == dispatch, (
        f"tool-spec/dispatch drift — spec-only: {specs - dispatch}; "
        f"dispatch-only: {dispatch - specs}"
    )


def test_every_tool_spec_has_required_name_and_parameters():
    """Each spec is a well-formed function-tool schema."""
    for spec in __import__("openbooks.ask", fromlist=["_tool_specs"])._tool_specs():
        assert spec["type"] == "function"
        fn = spec["function"]
        assert fn["name"], "tool spec missing name"
        assert fn.get("description"), f"tool {fn['name']} missing description"
        params = fn.get("parameters", {})
        assert params.get("type") == "object", f"tool {fn['name']} params not object"
        assert "properties" in params, f"tool {fn['name']} missing properties"


# ---------------------------------------------------------------------------
# _truncate — context-window safety on big result sets
# ---------------------------------------------------------------------------


def test_truncate_leaves_small_lists_untouched():
    assert _truncate([1, 2, 3]) == [1, 2, 3]


def test_truncate_caps_large_lists_with_metadata():
    big = list(range(MAX_TOOL_ROWS + 100))
    out = _truncate(big)
    assert isinstance(out, dict)
    assert out["_truncated"] is True
    assert out["_total_rows"] == MAX_TOOL_ROWS + 100
    assert out["_showing"] == MAX_TOOL_ROWS
    assert len(out["rows"]) == MAX_TOOL_ROWS


def test_truncate_passes_through_non_lists():
    assert _truncate({"a": 1}) == {"a": 1}
    assert _truncate("hello") == "hello"
    assert _truncate(None) is None


# ---------------------------------------------------------------------------
# End-to-end loop with a mocked LLM transport.
# We script _chat_completion to return a tool call, then a final answer, so
# the whole ask() loop (dispatch → truncate → audit → finalize) runs for real
# against the warehouse, without any network.
# ---------------------------------------------------------------------------


class _FakeToolCall(dict):
    """Minimal dict shaped like an OpenAI tool_call object (ask.py accesses
    it as ``call["function"]["name"]`` and ``call.get("id")``)."""

    def __init__(self, name: str, args: dict, call_id: str = "call_1"):
        super().__init__(
            id=call_id,
            type="function",
            function={"name": name, "arguments": __import__("json").dumps(args)},
        )


def _make_chat_completion_scripted(returns: list[dict]):
    """Return a fake _chat_completion that pops scripted responses in order.

    Each entry in ``returns`` is an assistant message dict with either
    ``tool_calls`` (→ another round) or just ``content`` (→ final answer).
    """
    queue = list(returns)

    def _fake(messages, tools, cfg):
        assert queue, "_chat_completion called more times than scripted"
        return queue.pop(0)

    return _fake


@requires_warehouse
def test_ask_waterfall_end_to_end(ob, monkeypatch):
    """One tool round (waterfall) → final answer. Exercises real dispatch,
    the audit trail, and the loop termination on a no-tool-calls message."""
    import openbooks.ask as ask_mod

    fake = _make_chat_completion_scripted([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_FakeToolCall("waterfall", {})],
        },
        {
            "role": "assistant",
            "content": "The model clears most dollars into Tier 4.",
        },
    ])
    monkeypatch.setattr(ask_mod, "_chat_completion", fake)
    # _resolve_provider would need a credential; bypass it by stubbing too.
    monkeypatch.setattr(ask_mod, "_resolve_provider", lambda: {
        "provider": "test", "base_url": "http://x", "model": "test-model",
        "token": "t", "auth_source": "test",
    })

    result = ask(ob, "Does the model clear most dollars?")

    assert result["question"] == "Does the model clear most dollars?"
    assert "clears most dollars" in result["answer"].lower() or result["answer"]
    assert result["model"] == "test-model"
    assert result["rounds"] == 2  # tool round + final round
    calls = result["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["name"] == "waterfall"
    # waterfall() returns a dict (not a list), so rows is None — that's correct.
    assert calls[0]["rows"] is None


@requires_warehouse
def test_ask_unknown_tool_surfaces_error_to_model_not_crash(ob, monkeypatch):
    """A model that calls a non-existent tool gets an error dict back (fed
    into the next round), not an exception — the loop must survive."""
    import openbooks.ask as ask_mod

    fake = _make_chat_completion_scripted([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_FakeToolCall("nonexistent_tool", {"x": 1})],
        },
        {"role": "assistant", "content": "Sorry, I could not find that."},
    ])
    monkeypatch.setattr(ask_mod, "_chat_completion", fake)
    monkeypatch.setattr(ask_mod, "_resolve_provider", lambda: {
        "provider": "test", "base_url": "http://x", "model": "test-model",
        "token": "t", "auth_source": "test",
    })

    result = ask(ob, "anything")
    assert result["rounds"] == 2
    assert result["tool_calls"][0]["name"] == "nonexistent_tool"


def test_ask_empty_question_raises():
    with pytest.raises(AskError):
        ask(None, "")
    with pytest.raises(AskError):
        ask(None, "   ")


def test_ask_rounds_cap_prevents_infinite_loop(monkeypatch):
    """If the model keeps calling tools forever, ask() must terminate at
    max_rounds with an AskError, not loop."""
    import openbooks.ask as ask_mod

    def _always_tool_call(messages, tools, cfg):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [_FakeToolCall("waterfall", {})],
        }

    monkeypatch.setattr(ask_mod, "_chat_completion", _always_tool_call)
    monkeypatch.setattr(ask_mod, "_resolve_provider", lambda: {
        "provider": "test", "base_url": "http://x", "model": "m",
        "token": "t", "auth_source": "test",
    })

    class _StubOB:
        def waterfall(self):
            return [{"tier": 1}]

    with pytest.raises(AskError, match="exceeded"):
        ask(_StubOB(), "loop forever", max_rounds=2)


# ---------------------------------------------------------------------------
# Lock is honored around tool dispatch (the #4 concurrency fix)
# ---------------------------------------------------------------------------


def test_ask_lock_acquired_around_each_dispatch(monkeypatch):
    """The optional ``lock`` must be entered before each _dispatch_tool and
    released after, so the slow LLM call between dispatches is NOT under the
    lock. Verified by recording enter/exit events."""
    import openbooks.ask as ask_mod

    events: list[str] = []

    class _RecordingLock:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, *exc):
            events.append("exit")
            return False

    # Script: round 1 → two tool calls in one assistant turn; round 2 → final.
    fake = _make_chat_completion_scripted([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                _FakeToolCall("waterfall", {}, "c1"),
                _FakeToolCall("waterfall", {}, "c2"),
            ],
        },
        {"role": "assistant", "content": "done"},
    ])
    monkeypatch.setattr(ask_mod, "_chat_completion", fake)
    monkeypatch.setattr(ask_mod, "_resolve_provider", lambda: {
        "provider": "test", "base_url": "http://x", "model": "m",
        "token": "t", "auth_source": "test",
    })

    class _StubOB:
        def waterfall(self):
            return [{"tier": 1}]

    ask(_StubOB(), "q", lock=_RecordingLock())
    # Two tool dispatches → enter/exit twice, in strict order.
    assert events == ["enter", "exit", "enter", "exit"]

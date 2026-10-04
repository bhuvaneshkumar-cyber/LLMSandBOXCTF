"""The model fallback chain, offline: order, benching after a failure, and the last-resort pass."""

import asyncio

import pytest

from app.llm import client as llm


@pytest.fixture
def chain(monkeypatch):
    """Two fake models; the names in `broken` raise. Returns (models tried, broken)."""
    tried, broken = [], {"a/one"}

    async def ask(provider, model, history, prompt):
        tried.append(f"{provider}/{model}")
        if f"{provider}/{model}" in broken:
            raise RuntimeError("provider down")
        return "hello"

    monkeypatch.setattr(llm, "CHAIN", [("a", "one"), ("b", "two")])
    monkeypatch.setattr(llm, "ask", ask)
    monkeypatch.setattr(llm, "_benched_until", {})
    return tried, broken


def test_falls_back_and_benches_the_failing_model(chain):
    tried, broken = chain
    assert asyncio.run(llm.reply([], "hi")) == ("hello", "b/two")
    assert tried == ["a/one", "b/two"]

    tried.clear()
    asyncio.run(llm.reply([], "hi"))
    assert tried == ["b/two"]  # benched: skipped while a healthy model answers

    broken.clear()
    llm._benched_until[("a", "one")] = 0  # bench time over
    assert asyncio.run(llm.reply([], "hi"))[1] == "a/one"  # first choice again


def test_tries_benched_models_before_giving_up(chain):
    tried, broken = chain
    broken.add("b/two")
    with pytest.raises(RuntimeError):
        asyncio.run(llm.reply([], "hi"))
    tried.clear()
    with pytest.raises(RuntimeError):
        asyncio.run(llm.reply([], "hi"))
    assert tried == ["a/one", "b/two"]  # everything benched: still tried in order as a last resort

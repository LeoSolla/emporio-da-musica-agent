"""Memory persistence and the agent loop, with a scripted fake model (no API calls)."""

from datetime import datetime
from types import SimpleNamespace

import anthropic
import pytest

from emporio_agent.agent import FAILURE_REPLY, REFUSAL_REPLY, Agent
from emporio_agent.data_store import StoreData
from emporio_agent.memory import ConversationStore
from emporio_agent.policies import PolicyManual
from emporio_agent.tools import ToolBox


# ------------------------------------------------------------------ fake model


class Block(SimpleNamespace):
    def model_dump(self, **_):
        return {k: v for k, v in vars(self).items() if v is not None}


def text(t):
    return Block(type="text", text=t)


def tool_use(name, args, id_="tu_1"):
    return Block(type="tool_use", id=id_, name=name, input=args)


def response(stop_reason, *blocks):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=100)
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks), usage=usage)


class FakeClient:
    """Returns scripted responses and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(scope="module")
def toolbox():
    return ToolBox(StoreData.load(), PolicyManual.load(), now=lambda: datetime(2026, 2, 20, 10, 0))


# ------------------------------------------------------------------ loop


def test_tool_round_then_answer(toolbox):
    client = FakeClient(
        response("tool_use", tool_use("search_products", {
            "query": "Takamine GD20", "category": None, "max_price": None, "min_price": None,
            "on_sale": False, "include_unavailable": False})),
        response("end_turn", text("Custa R$ 2.199,00.")),
    )
    agent = Agent(toolbox, client=client, model="test-model")
    turn = agent.chat("Quanto custa o Takamine GD20?")

    assert turn.reply == "Custa R$ 2.199,00."
    assert [c.name for c in turn.tool_calls] == ["search_products"]
    # user, assistant(tool_use), user(tool_result), assistant(text)
    assert [m["role"] for m in agent.messages] == ["user", "assistant", "user", "assistant"]
    result = agent.messages[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"
    assert "2199" in result["content"]
    # The static system prompt and tools are sent with caching on every call.
    assert all(r["cache_control"] == {"type": "ephemeral"} for r in client.requests)


def test_refusal_gets_polite_reply(toolbox):
    agent = Agent(toolbox, client=FakeClient(response("refusal")), model="test-model")
    assert agent.chat("...").reply == REFUSAL_REPLY
    assert agent.messages[-1]["role"] == "assistant"


def test_api_error_keeps_history_valid(toolbox):
    error = anthropic.APIConnectionError(request=None)
    agent = Agent(toolbox, client=FakeClient(error), model="test-model")
    assert agent.chat("Oi").reply == FAILURE_REPLY
    assert [m["role"] for m in agent.messages] == ["user", "assistant"]


def test_runaway_tool_loop_is_stopped(toolbox):
    looping = [response("tool_use", tool_use("list_categories", {}, id_=f"tu_{i}")) for i in range(8)]
    agent = Agent(toolbox, client=FakeClient(*looping), model="test-model")
    assert agent.chat("Oi").reply == FAILURE_REPLY


# ------------------------------------------------------------------ memory


def test_memory_roundtrip_and_append_only(tmp_path):
    store = ConversationStore(tmp_path / "c.db")
    msgs = [{"role": "user", "content": "Oi"}, {"role": "assistant", "content": [{"type": "text", "text": "Olá!"}]}]
    assert store.save_new("s1", msgs) == 2
    assert store.save_new("s1", msgs) == 0  # nothing new, nothing duplicated
    msgs.append({"role": "user", "content": "Tudo bem?"})
    assert store.save_new("s1", msgs) == 1
    assert store.load("s1") == msgs
    assert store.load("other") == []


def test_agent_resumes_saved_session(toolbox, tmp_path):
    memory = ConversationStore(tmp_path / "c.db")
    first = Agent(toolbox, client=FakeClient(response("end_turn", text("Oi, Ana!"))),
                  model="test-model", memory=memory, session_id="abc")
    first.chat("Oi, sou a Ana")

    client = FakeClient(response("end_turn", text("Seu nome é Ana.")))
    resumed = Agent(toolbox, client=client, model="test-model", memory=memory, session_id="abc")
    resumed.chat("Qual é o meu nome?")

    sent = client.requests[0]["messages"]
    assert sent[0]["content"] == "Oi, sou a Ana"  # earlier turn was sent to the model
    assert len(memory.load("abc")) == 4

"""The agent loop: model -> tool calls -> tool results -> ... -> final answer.

A hand-written loop (instead of the SDK's beta tool runner or a framework) was
chosen on purpose: it is ~60 lines, every step is visible and easy to log, and
it is the clearest way to show how the agent decides between data and policies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from .data_store import StoreData
from .memory import ConversationStore
from .policies import PolicyManual
from .prompts import SYSTEM_PROMPT
from .tools import ToolBox

DEFAULT_MODEL = "claude-sonnet-5-5"
STORE_TZ = ZoneInfo("America/Campo_Grande")
MAX_TOOL_ROUNDS = 8  # safety stop: a normal answer needs 1-3 rounds

REFUSAL_REPLY = (
    "Desculpe, não consigo ajudar com esse pedido por aqui. Posso ajudar com instrumentos, "
    "pedidos ou políticas da loja?"
)
FAILURE_REPLY = (
    "Tive um problema técnico agora e não consegui concluir sua consulta. Pode tentar de novo "
    "em instantes? Se preferir, fale com a equipe no WhatsApp (67) 3321-4500."
)


def store_clock() -> datetime:
    """Current time in Campo Grande. EMPORIO_TODAY=YYYY-MM-DD fixes the date
    (keeping the real time of day) for reproducible demos and tests."""
    now = datetime.now(STORE_TZ)
    fixed = os.getenv("EMPORIO_TODAY", "").strip()
    if fixed:
        d = datetime.fromisoformat(fixed).date()
        now = now.replace(year=d.year, month=d.month, day=d.day)
    return now.replace(tzinfo=None)


@dataclass
class ToolCall:
    name: str
    arguments: dict
    is_error: bool


@dataclass
class Turn:
    reply: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


class Agent:
    def __init__(
        self,
        toolbox: ToolBox,
        client: anthropic.Anthropic | None = None,
        model: str | None = None,
        effort: str = "medium",
        messages: list[dict] | None = None,
        memory: ConversationStore | None = None,
        session_id: str | None = None,
    ):
        self.toolbox = toolbox
        self.client = client or anthropic.Anthropic()
        self.model = model or os.getenv("EMPORIO_MODEL") or DEFAULT_MODEL
        self.effort = effort
        # Full history in API format. Assistant content is stored exactly as
        # returned (thinking blocks included): the history is append-only.
        self.messages: list[dict] = messages if messages is not None else []
        # Optional persistence: history is loaded from and appended to SQLite.
        self.memory = memory
        self.session_id = session_id
        if memory is not None and session_id is not None and messages is None:
            self.messages = memory.load(session_id)

    @classmethod
    def from_defaults(cls, **kwargs) -> "Agent":
        toolbox = ToolBox(StoreData.load(), PolicyManual.load(), now=store_clock)
        return cls(toolbox, **kwargs)

    def _call_model(self):
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=self.toolbox.schemas,
            messages=self.messages,
            # The system prompt and tools never change, so they are cached;
            # automatic caching also covers the growing conversation prefix.
            cache_control={"type": "ephemeral"},
            output_config={"effort": self.effort},
            # If a safety classifier declines, retry server-side on the model
            # Anthropic recommends for that category instead of failing.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    def chat(self, user_text: str) -> Turn:
        turn = self._run(user_text)
        if self.memory is not None and self.session_id is not None:
            self.memory.save_new(self.session_id, self.messages)
        return turn

    def _run(self, user_text: str) -> Turn:
        self.messages.append({"role": "user", "content": user_text})
        turn = Turn(reply="")

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response = self._call_model()
            except anthropic.APIConnectionError:
                return self._fail(turn)
            except anthropic.RateLimitError:
                return self._fail(turn)
            except anthropic.APIStatusError:
                return self._fail(turn)

            usage = response.usage
            turn.input_tokens += usage.input_tokens
            turn.output_tokens += usage.output_tokens
            turn.cache_read_tokens += usage.cache_read_input_tokens or 0

            if response.stop_reason == "refusal":
                # Nothing is appended for the refused attempt; answer politely.
                self.messages.append({"role": "assistant", "content": REFUSAL_REPLY})
                turn.reply = REFUSAL_REPLY
                return turn

            content = [block.model_dump(mode="json", exclude_none=True) for block in response.content]
            self.messages.append({"role": "assistant", "content": content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "tool_use" and tool_uses:
                results = []
                for block in tool_uses:
                    output, is_error = self.toolbox.execute(block.name, block.input)
                    turn.tool_calls.append(ToolCall(block.name, block.input, is_error))
                    results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": output, "is_error": is_error,
                    })
                # All results of one round go back in a single user message.
                self.messages.append({"role": "user", "content": results})
                continue

            if response.stop_reason == "pause_turn":
                continue

            turn.reply = "\n".join(b.text for b in response.content if b.type == "text").strip()
            return turn

        return self._fail(turn)

    def _fail(self, turn: Turn) -> Turn:
        """Keep the history valid (it must alternate user/assistant and every
        tool_use needs its tool_result) and give the customer a way out."""
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.append({"role": "assistant", "content": FAILURE_REPLY})
        turn.reply = FAILURE_REPLY
        return turn

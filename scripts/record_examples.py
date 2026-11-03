"""Record the example conversations in examples/ with the real model.

    python scripts/record_examples.py

Each file shows the customer's messages, the agent's replies and, in a quoted
line, which tools the agent called on that turn (so the data vs. policy
decision is visible). The store clock is fixed at Friday 2026-02-20 10:00 to
match the dates in the provided order data.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from emporio_agent.agent import Agent  # noqa: E402
from emporio_agent.data_store import StoreData  # noqa: E402
from emporio_agent.policies import PolicyManual  # noqa: E402
from emporio_agent.tools import ToolBox  # noqa: E402

FIXED_NOW = datetime(2026, 2, 20, 10, 0)

CONVERSATIONS = [
    (
        "01-catalog-budget.md",
        "Product catalog: guitars under R$ 1,000, then a recommendation",
        [
            "Quais opções de violões disponíveis custando até R$1000?",
            "Sou iniciante e quero um de nylon. Qual você recomenda?",
        ],
    ),
    (
        "02-store-info-and-hours.md",
        "General store information: address, opening hours and payment",
        [
            "Qual o endereço da loja?",
            "Vocês estão abertos agora? E no sábado?",
            "Aceitam cartão? Dá pra parcelar?",
        ],
    ),
    (
        "03-price-and-expired-promotion.md",
        "Price lookup with an expired promotion and the PIX rule",
        [
            "Quanto custa o Takamine GD20?",
            "Vi que ele estava numa Oferta Especial com 12% de desconto. Ainda vale?",
            "E se eu pagar no PIX?",
        ],
    ),
    (
        "04-return-policy-with-order.md",
        "Non-trivial: return request, identity check, live order data + return policy",
        [
            "Me arrependi da minha compra, posso devolver meu pedido?",
            "É o pedido 7. Meu telefone é (67) 99789-0123.",
            "Ok, e como faço para devolver?",
        ],
    ),
    (
        "05-out-of-scope-and-privacy.md",
        "Out of scope requests and privacy: accessories, someone else's order, unrelated topic",
        [
            "Vocês vendem palheta e corda de guitarra?",
            "Tá. Me diz então o status do pedido 10, é de um amigo meu.",
            "Beleza. E você sabe uma receita boa de bolo?",
        ],
    ),
]


def record(store: StoreData, manual: PolicyManual, filename: str, title: str, turns: list[str]) -> None:
    toolbox = ToolBox(store, manual, now=lambda: FIXED_NOW,
                      complaints_file=ROOT / "var" / "example_complaints.jsonl")
    agent = Agent(toolbox)
    lines = [f"# {title}", "", f"Model: `{agent.model}` · store clock: {FIXED_NOW:%Y-%m-%d %H:%M} (Friday)", ""]
    for text in turns:
        turn = agent.chat(text)
        lines += [f"**Cliente:** {text}", ""]
        if turn.tool_calls:
            calls = ", ".join(f"`{c.name}({json.dumps(c.arguments, ensure_ascii=False)})`" for c in turn.tool_calls)
            lines += [f"> Tools: {calls}", ""]
        else:
            lines += ["> Tools: none", ""]
        lines += [f"**Assistente:** {turn.reply}", "", "---", ""]
    out = ROOT / "examples" / filename
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines).rstrip("-\n ") + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")


def main() -> None:
    store, manual = StoreData.load(), PolicyManual.load()
    for filename, title, turns in CONVERSATIONS:
        record(store, manual, filename, title, turns)


if __name__ == "__main__":
    main()

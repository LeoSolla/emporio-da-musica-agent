"""Terminal chat with the agent.

    emporio-agent            # chat
    emporio-agent --debug    # also show which tools were called and token usage
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ENV = Path(__file__).resolve().parents[2] / ".env"

GREETING = (
    "Oi! Aqui é o assistente virtual da Empório da Música. Posso ajudar com instrumentos, "
    "preços, pedidos, trocas, pagamento e horários. Como posso ajudar?"
)
HELP = "Comandos: /novo (nova conversa), /sair (encerrar)."


def main() -> None:
    load_dotenv(PROJECT_ENV)  # works from any working directory
    load_dotenv()  # or a .env in the current directory
    parser = argparse.ArgumentParser(description="Empório da Música customer service agent")
    parser.add_argument("--debug", action="store_true", help="show tool calls and token usage")
    args = parser.parse_args()

    from .agent import Agent  # imported after load_dotenv so env vars apply

    agent = Agent.from_defaults()
    print(f"\nEmpório da Música · modelo {agent.model}\n{HELP}\n")
    print(f"Assistente: {GREETING}\n")

    while True:
        try:
            text = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in {"/sair", "sair", "/exit"}:
            break
        if text.lower() == "/novo":
            agent.messages.clear()
            print(f"\nAssistente: {GREETING}\n")
            continue

        turn = agent.chat(text)
        if args.debug:
            for call in turn.tool_calls:
                flag = " (erro)" if call.is_error else ""
                print(f"  [tool] {call.name} {json.dumps(call.arguments, ensure_ascii=False)}{flag}")
            print(f"  [tokens] in={turn.input_tokens} out={turn.output_tokens} cache_read={turn.cache_read_tokens}")
        print(f"\nAssistente: {turn.reply}\n")

    print("Até mais! 🎸")


if __name__ == "__main__":
    main()

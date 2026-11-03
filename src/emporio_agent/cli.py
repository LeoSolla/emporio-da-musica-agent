"""Terminal chat with the agent.

    emporio-agent            # chat
    emporio-agent --debug    # also show which tools were called and token usage
    emporio-agent --session ID  # resume a saved conversation
    emporio-agent --list     # list saved conversations
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
HELP = "Comandos: /novo (nova conversa), /sair (encerrar). A conversa é salva e pode ser retomada com --session."


def main() -> None:
    load_dotenv(PROJECT_ENV)  # works from any working directory
    load_dotenv()  # or a .env in the current directory
    parser = argparse.ArgumentParser(description="Empório da Música customer service agent")
    parser.add_argument("--debug", action="store_true", help="show tool calls and token usage")
    parser.add_argument("--session", help="resume the conversation with this session id")
    parser.add_argument("--list", action="store_true", help="list saved conversations and exit")
    args = parser.parse_args()

    from .agent import Agent  # imported after load_dotenv so env vars apply
    from .memory import ConversationStore, new_session_id

    memory = ConversationStore()
    if args.list:
        for session_id, last, count in memory.sessions():
            print(f"{session_id}  {last}  {count} mensagens")
        return

    session_id = args.session or new_session_id()
    agent = Agent.from_defaults(memory=memory, session_id=session_id)
    resumed = bool(agent.messages)
    print(f"\nEmpório da Música · modelo {agent.model} · sessão {session_id}"
          f"{' (retomada)' if resumed else ''}\n{HELP}\n")
    if resumed:
        print("Assistente: Que bom ter você de volta! Em que posso ajudar?\n")
    else:
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
            session_id = new_session_id()
            agent = Agent(agent.toolbox, client=agent.client, model=agent.model,
                          memory=memory, session_id=session_id)
            print(f"\n(nova sessão {session_id})\nAssistente: {GREETING}\n")
            continue

        turn = agent.chat(text)
        if args.debug:
            for call in turn.tool_calls:
                flag = " (erro)" if call.is_error else ""
                print(f"  [tool] {call.name} {json.dumps(call.arguments, ensure_ascii=False)}{flag}")
            print(f"  [tokens] in={turn.input_tokens} out={turn.output_tokens} cache_read={turn.cache_read_tokens}")
        print(f"\nAssistente: {turn.reply}\n")

    print(f"Até mais! (para retomar: emporio-agent --session {session_id})")


if __name__ == "__main__":
    main()

"""Tools exposed to the LLM: JSON schemas + Python implementations.

Each tool answers one kind of question. The split between *data* tools
(catalog, orders) and *policy* tools (manual, opening hours) is what lets the
agent "know when to consult data and when to consult policies": the model picks
the tool from its description, and the logs show which one it picked.

All tools are read-only except `log_complaint`, which appends to a local file.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from .data_store import StoreData
from .policies import PolicyManual, store_status

DEFAULT_COMPLAINTS_FILE = Path(__file__).resolve().parents[2] / "var" / "complaints.jsonl"


def _nullable(schema: dict) -> dict:
    """Optional argument: strict tool schemas require every property to be
    listed in `required`, so optional ones accept null instead."""
    return {**schema, "type": [schema["type"], "null"]}


def build_tool_schemas(category_names: list[str]) -> list[dict]:
    policy_sections = PolicyManual.section_catalog()
    return [
        {
            "name": "search_products",
            "description": (
                "Busca instrumentos no catálogo da loja, com preço atual, promoção ativa e "
                "disponibilidade. Use para qualquer pergunta sobre produtos, preços, estoque, "
                "opções dentro de um orçamento ou promoções em andamento. Nunca informe preço ou "
                "disponibilidade sem chamar esta ferramenta. Por padrão retorna só itens "
                "disponíveis e informa, à parte, os que foram encontrados mas estão indisponíveis."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": _nullable({
                        "type": "string",
                        "description": "Marca, modelo ou características (ex.: 'Takamine GD20', 'nylon', 'tenor'). Null para não filtrar por texto.",
                    }),
                    "category": _nullable({
                        "type": "string",
                        "enum": category_names,
                        "description": "Categoria exata, ou null.",
                    }),
                    "max_price": _nullable({"type": "number", "description": "Preço máximo em reais, ou null."}),
                    "min_price": _nullable({"type": "number", "description": "Preço mínimo em reais, ou null."}),
                    "on_sale": {"type": "boolean", "description": "true para listar só produtos em promoção ativa."},
                    "include_unavailable": {
                        "type": "boolean",
                        "description": "true para incluir itens sem estoque, descontinuados ou em breve na lista principal.",
                    },
                },
                "required": ["query", "category", "max_price", "min_price", "on_sale", "include_unavailable"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_product_details",
            "description": "Detalhes completos de um produto (descrição e especificações técnicas), pelo product_id retornado em search_products.",
            "input_schema": {
                "type": "object",
                "properties": {"product_id": {"type": "integer"}},
                "required": ["product_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_categories",
            "description": "Lista as categorias da loja e quantos produtos disponíveis há em cada uma. Use quando o cliente perguntar o que a loja vende ou por uma categoria que talvez esteja vazia.",
            "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {
            "name": "get_order_status",
            "description": (
                "Consulta um pedido: status, itens, total, rastreio, previsão de entrega e prazos de "
                "troca/devolução contados até hoje. Exige o número do pedido E o telefone ou e-mail "
                "cadastrado do cliente. Peça esses dados ao cliente antes de chamar; nunca invente."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                    "phone_or_email": {"type": "string", "description": "Telefone ou e-mail informado pelo cliente."},
                },
                "required": ["order_id", "phone_or_email"],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_my_orders",
            "description": "Lista os pedidos do cliente quando ele não sabe o número. Exige telefone E e-mail cadastrados, ambos informados pelo cliente.",
            "input_schema": {
                "type": "object",
                "properties": {"phone": {"type": "string"}, "email": {"type": "string"}},
                "required": ["phone", "email"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_store_policy",
            "description": (
                "Retorna o texto oficial de seções do manual de políticas da loja. Use para regras: "
                "trocas, devoluções, garantia, pagamento, parcelamento, frete, promoções, contatos, "
                "endereço, privacidade. Nunca responda regras de memória. Seções: "
                + "; ".join(f"{k} ({v})" for k, v in policy_sections.items())
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(policy_sections)},
                        "description": "Uma ou mais seções relevantes.",
                    }
                },
                "required": ["sections"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_store_hours_status",
            "description": "Diz se a loja está aberta agora (horário de Campo Grande) e quando abre de novo. Use para 'vocês estão abertos?' ou 'até que horas?'.",
            "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {
            "name": "log_complaint",
            "description": (
                "Registra uma reclamação para a equipe responsável e devolve um protocolo. Use depois "
                "de ouvir o cliente com empatia e entender o problema. O retorno da equipe é em até 24 horas úteis."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Resumo objetivo da reclamação."},
                    "customer_name": _nullable({"type": "string"}),
                    "contact": _nullable({"type": "string", "description": "Telefone ou e-mail para retorno."}),
                    "order_id": _nullable({"type": "integer"}),
                },
                "required": ["summary", "customer_name", "contact", "order_id"],
                "additionalProperties": False,
            },
        },
    ]


@dataclass
class ToolBox:
    store: StoreData
    manual: PolicyManual
    now: Callable[[], datetime]
    complaints_file: Path = DEFAULT_COMPLAINTS_FILE
    schemas: list[dict] = field(init=False)

    def __post_init__(self) -> None:
        self.schemas = build_tool_schemas(self.store.category_names())
        self._handlers: dict[str, Callable[..., Any]] = {
            "search_products": self.search_products,
            "get_product_details": self.get_product_details,
            "list_categories": lambda: self.store.list_categories(),
            "get_order_status": self.get_order_status,
            "find_my_orders": self.find_my_orders,
            "get_store_policy": lambda sections: self.manual.get(sections),
            "get_store_hours_status": lambda: store_status(self.now()),
            "log_complaint": self.log_complaint,
        }

    def execute(self, name: str, arguments: dict) -> tuple[str, bool]:
        """Run a tool. Returns (JSON result, is_error). Errors go back to the
        model as a tool_result with is_error=True instead of crashing the chat."""
        handler = self._handlers.get(name)
        if handler is None:
            return json.dumps({"error": f"Ferramenta desconhecida: {name}"}), True
        try:
            result = handler(**arguments)
        except Exception as exc:  # report to the model, keep the conversation alive
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), True
        return json.dumps(result, ensure_ascii=False, default=str), False

    # ------------------------------------------------------------------ handlers

    def search_products(self, query=None, category=None, max_price=None, min_price=None,
                        on_sale=False, include_unavailable=False) -> dict:
        return self.store.search_products(
            query=query, category=category, max_price=max_price, min_price=min_price,
            on_sale=on_sale, only_available=not include_unavailable, limit=20,
        )

    def get_product_details(self, product_id: int) -> dict:
        product = self.store.get_product(product_id)
        return product if product else {"error": f"Produto {product_id} não encontrado."}

    def get_order_status(self, order_id: int, phone_or_email: str) -> dict:
        order = self.store.get_order(order_id, phone_or_email)
        if order.get("found"):
            order["return_windows"] = return_windows(order, self.now().date())
        return order

    def find_my_orders(self, phone: str, email: str) -> dict:
        result = self.store.find_customer_orders(phone, email)
        today = self.now().date()
        for order in result.get("orders", []):
            order["return_windows"] = return_windows(order, today)
        return result

    def log_complaint(self, summary: str, customer_name=None, contact=None, order_id=None) -> dict:
        now = self.now()
        protocol = f"REC-{now:%Y%m%d}-{secrets.token_hex(2).upper()}"
        self.complaints_file.parent.mkdir(parents=True, exist_ok=True)
        with self.complaints_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "protocol": protocol, "created_at": now.isoformat(), "summary": summary,
                "customer_name": customer_name, "contact": contact, "order_id": order_id,
            }, ensure_ascii=False) + "\n")
        return {"protocol": protocol, "registered": True,
                "next_step": "A equipe responsável retorna em até 24 horas úteis (manual, seção 7.3)."}


def return_windows(order: dict, today: date) -> dict:
    """Facts the model needs to apply section 4 of the manual, computed in code
    (date arithmetic is a classic LLM mistake).

    Assumption: the data has no actual delivery date, so `estimated_delivery`
    is used as the receipt date for delivered orders."""
    info: dict[str, Any] = {"reference_date": today.isoformat()}
    if order["status"] != "delivered" or not order.get("estimated_delivery"):
        info["note"] = "Pedido ainda não entregue: prazos de arrependimento e troca começam a contar no recebimento."
        return info
    received = date.fromisoformat(order["estimated_delivery"])
    purchased = date.fromisoformat(order["order_date"])
    since_receipt = (today - received).days
    since_purchase = (today - purchased).days
    info.update({
        "assumed_receipt_date": received.isoformat(),
        "days_since_receipt": since_receipt,
        "days_since_purchase": since_purchase,
        "regret_return_7_days_from_receipt": "dentro do prazo" if since_receipt <= 7 else "prazo encerrado",
        "preference_exchange_7_days": "dentro do prazo" if since_receipt <= 7 else "prazo encerrado",
        "defect_exchange_30_days_from_purchase": "dentro do prazo" if since_purchase <= 30 else "prazo encerrado (acionar garantia do fabricante; a loja pode intermediar)",
        "legal_warranty_90_days_from_receipt": "dentro do prazo" if since_receipt <= 90 else "prazo encerrado",
        "assumption": "A base não tem a data real de entrega; usamos a previsão de entrega como data de recebimento.",
    })
    return info

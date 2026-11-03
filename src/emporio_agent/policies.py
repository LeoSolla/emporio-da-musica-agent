"""Store policy manual: PDF -> clean text -> one chunk per numbered section.

Why sections and not embeddings
-------------------------------
The manual is ~8 pages (~3k tokens) with 10 numbered sections. Splitting by its
own headings gives chunks that are exact, explainable and cheap to retrieve: the
model picks a section by name through a tool. A vector store would add moving
parts without improving recall at this size. See README.

Text is kept verbatim. Known inconsistencies of the manual are NOT edited in the
text; they are returned as explicit clarification notes next to the section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from pypdf import PdfReader

DEFAULT_PDF = Path(__file__).resolve().parents[2] / "data" / "politicas_da_loja.pdf"

# Section number -> (tool key, short description used in the tool schema)
SECTION_KEYS: dict[int, tuple[str, str]] = {
    1: ("sobre_a_loja", "quem somos, endereço, telefone, e-mail, CNPJ, o que a loja vende e não vende"),
    2: ("horario", "horário de funcionamento da loja e do WhatsApp"),
    3: ("pagamento", "formas de pagamento, desconto no PIX, parcelamento"),
    4: ("trocas_e_devolucoes", "arrependimento, troca por defeito, troca por preferência, itens não elegíveis"),
    5: ("frete_e_entregas", "frete, prazos de entrega, Correios/Jadlog, rastreamento"),
    6: ("promocoes", "campanhas do ano e regras de promoções"),
    7: ("atendimento", "regras do atendimento, produto sem estoque, reclamações"),
    8: ("garantia", "garantia legal e do fabricante, o que não é coberto"),
    9: ("privacidade", "LGPD, uso e exclusão de dados pessoais"),
    10: ("disposicoes_finais", "uso do manual e dúvidas sobre políticas"),
}

# Decisions on contradictions found in the manual (documented in the README).
CLARIFICATIONS: dict[str, str] = {
    "sobre_a_loja": (
        "O manual traz dois números: (67) 3341-4444 (capa e tabela de dados) e "
        "(67) 3321-4500 (seção 7, canal de WhatsApp). Informe (67) 3341-4444 como "
        "telefone e (67) 3321-4500 como WhatsApp de atendimento. O e-mail correto é "
        "contato@emporiodamusica.com.br (sem acento; a tabela tem um erro de digitação)."
    ),
    "atendimento": (
        "WhatsApp de atendimento: (67) 3321-4500. Telefone da loja: (67) 3341-4444."
    ),
    "pagamento": (
        "A tabela fala em parcela mínima de R$ 100,00, mas a seção 3.1 detalha faixas. "
        "Vale a 3.1, que é mais específica: até 3x sem mínimo de parcela (desde que a "
        "parcela não fique abaixo de R$ 50,00); 4x a 6x com parcela mínima de R$ 80,00; "
        "7x a 12x com parcela mínima de R$ 100,00."
    ),
}

_HEADER_RE = re.compile(r"^Empório da Música\s+Manual de Políticas e Procedimentos$")
_PAGE_RE = re.compile(r"^Página \d+$")


@dataclass(frozen=True)
class PolicySection:
    number: int
    key: str
    title: str
    text: str


def extract_text(pdf_path: Path | str = DEFAULT_PDF) -> str:
    """PDF -> clean text. Layout mode keeps lines and table rows together (the
    default pypdf mode breaks this PDF one word per line). Cover page skipped:
    its contact data is repeated in section 1.2."""
    reader = PdfReader(str(pdf_path))
    lines: list[str] = []
    for page in reader.pages[1:]:
        for raw in page.extract_text(extraction_mode="layout").splitlines():
            line = re.sub(r"\s{2,}", "  ", raw.strip())
            if _HEADER_RE.match(re.sub(r"\s+", " ", line)) or _PAGE_RE.match(line):
                continue
            lines.append(line)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_sections(text: str) -> list[PolicySection]:
    """Split on top-level headings '1. Title' ... '10. Title'. Headings are
    matched in order, so numbered list items inside a section (e.g. '1.
    Saudação' in section 7.2) are not mistaken for a new section."""
    sections: list[PolicySection] = []
    expected = 1
    current_title, buffer = None, []
    for line in text.splitlines():
        m = re.match(rf"^{expected}\.\s+(\S.*)$", line)
        if m and expected in SECTION_KEYS:
            if current_title is not None:
                sections.append(_make(expected - 1, current_title, buffer))
            current_title, buffer = m.group(1).strip(), []
            expected += 1
            continue
        if current_title is not None:
            buffer.append(line)
    if current_title is not None:
        sections.append(_make(expected - 1, current_title, buffer))
    return sections


def _make(number: int, title: str, buffer: list[str]) -> PolicySection:
    body = "\n".join(buffer).strip()
    # The last section ends with the manual's closing tagline; keep only content.
    body = body.split("Empório da Música — Sua música começa aqui.")[0].strip()
    return PolicySection(number, SECTION_KEYS[number][0], title, body)


class PolicyManual:
    def __init__(self, sections: list[PolicySection]):
        if len(sections) != len(SECTION_KEYS):
            raise ValueError(f"Expected {len(SECTION_KEYS)} sections, found {len(sections)}")
        self.sections = {s.key: s for s in sections}

    @classmethod
    def load(cls, pdf_path: Path | str = DEFAULT_PDF) -> "PolicyManual":
        return cls(split_sections(extract_text(pdf_path)))

    @staticmethod
    def section_catalog() -> dict[str, str]:
        return {key: desc for key, desc in SECTION_KEYS.values()}

    def get(self, keys: list[str]) -> dict:
        found, unknown = [], []
        for key in keys:
            section = self.sections.get(key)
            if section is None:
                unknown.append(key)
                continue
            item = {"section": f"{section.number}. {section.title}", "text": section.text}
            if key in CLARIFICATIONS:
                item["clarification"] = CLARIFICATIONS[key]
            found.append(item)
        result: dict = {"sections": found}
        if unknown:
            result["unknown_sections"] = unknown
            result["valid_sections"] = list(self.sections)
        return result


# ----------------------------------------------------------------- opening hours

# Manual, section 2. Monday=0 ... Sunday=6. Holidays are not listed in the manual,
# so the agent says "closed on holidays" without asserting which dates.
OPENING_HOURS: dict[int, tuple[time, time] | None] = {
    0: (time(9), time(18)),
    1: (time(9), time(18)),
    2: (time(9), time(18)),
    3: (time(9), time(18)),
    4: (time(9), time(18)),
    5: (time(9), time(13)),
    6: None,
}
WEEKDAYS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def store_status(now: datetime) -> dict:
    """Is the store open at `now` (local time, America/Campo_Grande)? If not,
    when does it open next?"""
    hours = OPENING_HOURS[now.weekday()]
    if hours and hours[0] <= now.time() < hours[1]:
        return {
            "open_now": True,
            "closes_at": hours[1].strftime("%H:%M"),
            "note": "Feriados: fechado (o manual não lista as datas).",
        }
    for offset in range(0, 8):
        day = now + timedelta(days=offset)
        h = OPENING_HOURS[day.weekday()]
        if not h:
            continue
        if offset == 0 and now.time() >= h[0]:
            continue
        label = "hoje" if offset == 0 else ("amanhã" if offset == 1 else WEEKDAYS_PT[day.weekday()])
        return {
            "open_now": False,
            "next_opening": f"{label} ({day.date().isoformat()}) às {h[0].strftime('%H:%M')}",
            "note": "Feriados: fechado (o manual não lista as datas).",
        }
    raise RuntimeError("No opening hours configured")

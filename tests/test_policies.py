from datetime import datetime

import pytest

from emporio_agent.policies import SECTION_KEYS, PolicyManual, store_status


@pytest.fixture(scope="module")
def manual() -> PolicyManual:
    return PolicyManual.load()


def test_all_ten_sections_extracted(manual):
    assert list(manual.sections) == [key for key, _ in SECTION_KEYS.values()]
    assert all(len(s.text) > 200 for s in manual.sections.values())


def test_page_headers_and_footers_removed(manual):
    full = "\n".join(s.text for s in manual.sections.values())
    assert "Página" not in full
    assert "Manual de Políticas e Procedimentos" not in full


def test_numbered_list_inside_section_is_not_a_new_section(manual):
    # Section 7.2 has a list "1. Saudação ... 5. Fechamento".
    text = manual.sections["atendimento"].text
    assert "Saudação" in text and "Fechamento" in text
    assert "Garantia Legal" not in text


def test_return_policy_content(manual):
    text = manual.sections["trocas_e_devolucoes"].text
    assert "7 (sete) dias corridos após o recebimento" in text
    assert "30 (trinta) dias" in text


def test_clarification_attached_for_conflicting_contacts(manual):
    result = manual.get(["sobre_a_loja"])
    section = result["sections"][0]
    assert "(67) 3341-4444" in section["text"]
    assert "3321-4500" in section["clarification"]


def test_unknown_section_lists_valid_ones(manual):
    result = manual.get(["horario", "cashback"])
    assert len(result["sections"]) == 1
    assert result["unknown_sections"] == ["cashback"]
    assert "pagamento" in result["valid_sections"]


@pytest.mark.parametrize(
    "now, open_now, expected",
    [
        (datetime(2026, 11, 3, 10, 0), True, "18:00"),                 # Tuesday morning
        (datetime(2026, 11, 3, 19, 0), False, "amanhã (2026-11-04) às 09:00"),
        (datetime(2026, 11, 7, 12, 59), True, "13:00"),                # Saturday
        (datetime(2026, 11, 7, 13, 0), False, "segunda-feira (2026-11-09) às 09:00"),
        (datetime(2026, 11, 8, 10, 0), False, "amanhã (2026-11-09) às 09:00"),  # Sunday
        (datetime(2026, 11, 3, 7, 30), False, "hoje (2026-11-03) às 09:00"),
    ],
)
def test_store_status(now, open_now, expected):
    status = store_status(now)
    assert status["open_now"] is open_now
    assert expected == (status["closes_at"] if open_now else status["next_opening"])

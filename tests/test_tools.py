import json
from datetime import date, datetime

import pytest

from emporio_agent.data_store import StoreData
from emporio_agent.policies import PolicyManual
from emporio_agent.tools import ToolBox, return_windows


@pytest.fixture(scope="module")
def store():
    return StoreData.load()


@pytest.fixture(scope="module")
def manual():
    return PolicyManual.load()


@pytest.fixture
def toolbox(store, manual, tmp_path):
    return ToolBox(store, manual, now=lambda: datetime(2026, 2, 20, 10, 0), complaints_file=tmp_path / "c.jsonl")


def run(toolbox, name, **args):
    out, is_error = toolbox.execute(name, args)
    return json.loads(out), is_error


def test_strict_schemas_list_every_property_as_required(toolbox):
    for schema in toolbox.schemas:
        s = schema["input_schema"]
        assert s["additionalProperties"] is False
        assert set(s["required"]) == set(s["properties"]), schema["name"]


def test_every_schema_has_a_handler(toolbox):
    for schema in toolbox.schemas:
        assert schema["name"] in toolbox._handlers


def test_search_with_nulls(toolbox):
    result, err = run(toolbox, "search_products", query=None, category="Violões", max_price=1000,
                      min_price=None, on_sale=False, include_unavailable=False)
    assert not err and result["total_found"] == 12


def test_policy_tool(toolbox):
    result, err = run(toolbox, "get_store_policy", sections=["pagamento"])
    assert not err and "PIX" in result["sections"][0]["text"]


def test_unknown_tool_and_bad_arguments_are_errors_not_crashes(toolbox):
    _, err = run(toolbox, "delete_database")
    assert err
    _, err = run(toolbox, "get_product_details", wrong_arg=1)
    assert err


def test_order_status_includes_return_windows(toolbox):
    # Order 7 (customer 14) delivered, estimated delivery 2026-02-17; "today" is 2026-02-20.
    phone = toolbox.store.customers.set_index("customer_id").loc[14, "phone"]
    result, _ = run(toolbox, "get_order_status", order_id=7, phone_or_email=phone)
    windows = result["return_windows"]
    assert windows["days_since_receipt"] == 3
    assert windows["regret_return_7_days_from_receipt"] == "dentro do prazo"


def test_return_windows_expired_and_not_delivered():
    delivered = {"status": "delivered", "order_date": "2025-10-15", "estimated_delivery": "2025-10-25"}
    w = return_windows(delivered, date(2026, 11, 3))
    assert w["regret_return_7_days_from_receipt"] == "prazo encerrado"
    assert w["defect_exchange_30_days_from_purchase"].startswith("prazo encerrado")
    shipped = {"status": "shipped", "order_date": "2026-02-15", "estimated_delivery": "2026-02-28"}
    assert "ainda não entregue" in return_windows(shipped, date(2026, 2, 20))["note"]


def test_hours_tool_uses_injected_clock(toolbox):
    result, _ = run(toolbox, "get_store_hours_status")
    assert result["open_now"] is True  # Friday 10:00


def test_log_complaint_writes_file(toolbox):
    result, err = run(toolbox, "log_complaint", summary="Instrumento chegou riscado",
                      customer_name="Ana", contact="(67) 99654-7821", order_id=8)
    assert not err and result["protocol"].startswith("REC-20260220-")
    saved = json.loads(toolbox.complaints_file.read_text().strip())
    assert saved["order_id"] == 8

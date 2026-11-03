import pytest

from emporio_agent.data_store import StoreData, normalize, tokens


@pytest.fixture(scope="module")
def store() -> StoreData:
    return StoreData.load()


# ---------------------------------------------------------------- text helpers


def test_normalize_strips_accents_and_punctuation():
    assert normalize("Violões Elétricos!") == "violoes eletricos"
    assert normalize("GD-20") == "gd20"


def test_tokens_handles_plurals_and_stopwords():
    assert tokens("violões de nylon") == ["violao", "nylon"]


# ---------------------------------------------------------------- catalog


def test_loads_all_products(store):
    assert len(store.products) == 65


def test_price_lookup_takamine_gd20(store):
    result = store.search_products(query="Takamine GD20")
    assert result["total_found"] == 1
    product = result["products"][0]
    assert product["name"] == "Takamine GD20 Dreadnought Natural"
    assert product["list_price_brl"] == 2199.0
    # Its 12% promotion is inactive: no discount may be offered.
    assert "promotion" not in product
    assert product["pix_price_brl"] == pytest.approx(2089.05)


def test_guitars_up_to_1000(store):
    result = store.search_products(category="Violões", max_price=1000)
    prices = [p["current_price_brl"] for p in result["products"]]
    assert result["total_found"] > 0
    assert all(p <= 1000 for p in prices)
    names = [p["name"] for p in result["products"]]
    # Active but out of stock: must not be offered as available.
    assert "Giannini GF-3D Dreadnought Sunburst" not in names
    assert any(u["name"] == "Giannini GF-3D Dreadnought Sunburst" for u in result["unavailable_matches"])


def test_query_finds_category_in_singular(store):
    result = store.search_products(query="violão", max_price=500)
    assert result["total_found"] > 0
    assert all(p["category"] == "Violões" for p in result["products"])


def test_active_promotion_is_applied(store):
    product = store.get_product(127)  # Kalani KAL-700T, 10% active
    assert product["promotion"]["discount_percent"] == 10
    assert product["promotion"]["promo_price_brl"] == pytest.approx(323.1)
    assert "pix_price_brl" not in product  # PIX does not stack on promotions


def test_on_sale_lists_only_active_promotions(store):
    result = store.search_products(on_sale=True, limit=50)
    assert sorted(p["product_id"] for p in result["products"]) == [90, 94, 121, 127]


def test_discontinued_with_stock_is_not_available(store):
    product = store.get_product(136)  # Ibanez Bass 2X: discontinued, 13 in stock
    assert product["availability"].startswith("descontinuado")
    assert product["stock_quantity"] == 0


def test_coming_soon_is_not_available(store):
    product = store.get_product(130)
    assert product["availability"].startswith("em breve")


def test_empty_categories_report_zero(store):
    counts = {c["category"]: c["available_products"] for c in store.list_categories()}
    assert counts["Cordas Orquestrais"] == 0
    assert counts["Instrumentos de Sopro (Metais)"] == 0
    assert counts["Violões"] > 0


def test_unknown_category_returns_valid_options(store):
    result = store.search_products(category="Amplificadores")
    assert "error" in result
    assert "Violões" in result["valid_categories"]


def test_unknown_product_returns_nothing(store):
    assert store.search_products(query="Stradivarius")["total_found"] == 0


# ---------------------------------------------------------------- orders


def test_order_requires_matching_contact(store):
    # Order 8 belongs to customer 2, Ana Carolina, (67) 99654-7821.
    ok = store.get_order(8, "(67) 99654-7821")
    assert ok["found"] and ok["status"] == "shipped"
    assert ok["tracking_code"] == "BRJL5544332BR"

    assert store.get_order(8, "anacarol.ferreira@coldmail.com")["found"]
    assert store.get_order(8, "99654-7821")["found"]  # without area code
    assert store.get_order(8, "+55 67 99654-7821")["found"]  # with country code


def test_wrong_contact_and_missing_order_look_the_same(store):
    wrong_contact = store.get_order(8, "(67) 99999-0000")
    missing = store.get_order(999, "(67) 99654-7821")
    assert wrong_contact == missing
    assert wrong_contact["found"] is False


def test_order_reports_recorded_total(store):
    # Sum of items is 3,498.00 but the recorded total is 3,450.00.
    email = store.customers.set_index("customer_id").loc[12, "email"]
    order = store.get_order(3, email)
    assert order["total_brl"] == 3450.0


def test_cancelled_order_shows_reason(store):
    # Find customer 17's phone from data to keep the test independent of row order.
    phone = store.customers.set_index("customer_id").loc[17, "phone"]
    order = store.get_order(18, phone)
    assert order["status"] == "cancelled"
    assert "estoque" in order["notes"]


def test_find_orders_needs_phone_and_email(store):
    result = store.find_customer_orders("(67) 98432-1098", "pedro.oliveira@jmail.com")
    assert result["found"]
    assert {o["order_id"] for o in result["orders"]} == {1, 19}
    assert not store.find_customer_orders("(67) 98432-1098", "outro@jmail.com")["found"]

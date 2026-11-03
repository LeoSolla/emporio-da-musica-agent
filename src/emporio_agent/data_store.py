"""Loads and cleans the store's CSV data and answers structured queries.

Design notes
------------
* The catalog is tiny (65 products), so everything is loaded into memory once.
* Every number the agent says (price, stock, order total) comes from here, never
  from the model's memory.
* Methods return plain dicts/lists so they can be serialized straight into a
  tool result for the LLM.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

PIX_DISCOUNT_PERCENT = 5  # store manual, section 3

# Words that should find a category even when the customer does not use its exact
# name (singular forms, common synonyms).
CATEGORY_ALIASES: dict[int, str] = {
    1: "guitarra",
    2: "baixo contrabaixo eletrico",
    3: "bateria percussao",
    4: "teclado piano sintetizador",
    5: "violao",
    6: "sopro madeira flauta clarinete saxofone",
    7: "sopro metal trompete trombone",
    8: "violino viola violoncelo",
    9: "ukulele",
}

AVAILABILITY_LABELS = {
    "available": "disponível",
    "out_of_stock": "temporariamente sem estoque",
    "discontinued": "descontinuado (fora do catálogo)",
    "coming_soon": "em breve (ainda não disponível para venda)",
}

ORDER_STATUS_LABELS = {
    "pending": "aguardando confirmação do pagamento",
    "confirmed": "pagamento confirmado, em separação",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}

PAYMENT_LABELS = {
    "pix": "PIX",
    "debit": "cartão de débito",
    "boleto": "boleto bancário",
}

STOPWORDS = {
    "a", "o", "as", "os", "de", "do", "da", "dos", "das", "e", "em", "um", "uma",
    "para", "pra", "com", "por", "no", "na", "r", "ate", "que", "tem", "vcs",
}


# --------------------------------------------------------------------------- text


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation. 'Violões' -> 'violoes'."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("-", "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _stem(token: str) -> str:
    """Very light Portuguese plural stripping: violoes -> violao, kits -> kit."""
    if token.endswith("oes") or token.endswith("aes"):
        return token[:-3] + "ao"
    if token.endswith("s") and len(token) > 3 and not token[-2].isdigit():
        return token[:-1]
    return token


def tokens(text: str) -> list[str]:
    return [_stem(t) for t in normalize(text).split() if t not in STOPWORDS]


def phone_digits(phone: str) -> str:
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) in (12, 13) and digits.startswith("55"):  # +55 country code
        digits = digits[2:]
    return digits


# --------------------------------------------------------------------------- store


@dataclass
class StoreData:
    products: pd.DataFrame
    categories: pd.DataFrame
    promotions: pd.DataFrame
    orders: pd.DataFrame
    order_items: pd.DataFrame
    customers: pd.DataFrame

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, data_dir: Path | str = DEFAULT_DATA_DIR) -> "StoreData":
        d = Path(data_dir)
        categories = pd.read_csv(d / "categories.csv")
        products = pd.read_csv(d / "products.csv")
        promotions = pd.read_csv(d / "promotions.csv")
        orders = pd.read_csv(
            d / "orders.csv",
            dtype={"tracking_code": "string", "notes": "string"},
            parse_dates=["order_date", "estimated_delivery"],
        )
        order_items = pd.read_csv(d / "order_items.csv")
        customers = pd.read_csv(d / "customers.csv", dtype={"phone": "string"})

        # products: types, parsed specs, search text, availability, current price
        products["price_brl"] = products["price_brl"].astype(float)
        products["stock_quantity"] = products["stock_quantity"].astype(int)
        products["specs"] = products["specs"].apply(json.loads)
        products = products.merge(
            categories[["category_id", "name"]].rename(columns={"name": "category"}),
            on="category_id",
            how="left",
        )
        products["availability"] = products.apply(_availability, axis=1)

        active = promotions[promotions["is_active"] == 1]
        # If a product ever had two active promotions, the biggest discount wins
        # (promotions are not cumulative, manual 6.2).
        active = active.sort_values("discount_percent", ascending=False).drop_duplicates("product_id")
        products = products.merge(
            active[["product_id", "discount_percent", "description"]].rename(
                columns={"description": "promotion_name"}
            ),
            on="product_id",
            how="left",
        )
        products["promo_price_brl"] = (
            products["price_brl"] * (1 - products["discount_percent"] / 100)
        ).round(2)
        products["current_price_brl"] = products["promo_price_brl"].fillna(products["price_brl"])

        products["search_text"] = products.apply(
            lambda r: " ".join(
                tokens(f"{r['name']} {r['category']} {CATEGORY_ALIASES.get(r['category_id'], '')} "
                       f"{' '.join(str(v) for v in r['specs'].values())}")
            ),
            axis=1,
        )
        products["compact_name"] = products["name"].map(lambda n: normalize(n).replace(" ", ""))

        customers["phone_digits"] = customers["phone"].map(phone_digits)
        customers["email_norm"] = customers["email"].str.strip().str.casefold()

        return cls(products, categories, promotions, orders, order_items, customers)

    # ------------------------------------------------------------------ catalog

    def list_categories(self) -> list[dict]:
        """All categories with how many products are available in each."""
        available = self.products[self.products["availability"] == "available"]
        counts = available.groupby("category_id").size()
        return [
            {
                "category": row["name"],
                "description": row["description"],
                "available_products": int(counts.get(row["category_id"], 0)),
            }
            for _, row in self.categories.iterrows()
        ]

    def category_names(self) -> list[str]:
        return self.categories["name"].tolist()

    def search_products(
        self,
        query: str | None = None,
        category: str | None = None,
        max_price: float | None = None,
        min_price: float | None = None,
        only_available: bool = True,
        on_sale: bool = False,
        limit: int = 10,
    ) -> dict:
        """Filter the catalog. Price filters use the current price (promotion
        applied when there is an active one). Returns matches plus counts that let
        the agent explain *why* something was not found."""
        df = self.products

        if category:
            wanted = normalize(category)
            mask = self.products["category"].map(normalize) == wanted
            if not mask.any():
                return {"error": f"Categoria desconhecida: {category}", "valid_categories": self.category_names()}
            df = df[mask]

        approximate = False
        if query:
            df, approximate = self._match_query(df, query)

        if max_price is not None:
            df = df[df["current_price_brl"] <= max_price]
        if min_price is not None:
            df = df[df["current_price_brl"] >= min_price]
        if on_sale:
            df = df[df["discount_percent"].notna()]

        matched_any_status = len(df)
        unavailable = df[df["availability"] != "available"]
        if only_available:
            df = df[df["availability"] == "available"]

        df = df.sort_values("current_price_brl")
        return {
            "total_found": int(len(df)),
            "returned": int(min(len(df), limit)),
            "approximate_name_match": approximate,
            "unavailable_matches": [
                {"product_id": int(r.product_id), "name": r.name, "availability": AVAILABILITY_LABELS[r.availability]}
                for r in unavailable.itertuples()
            ] if only_available else [],
            "products": [self._product_dict(r) for _, r in df.head(limit).iterrows()],
            "matched_before_availability_filter": int(matched_any_status),
        }

    def get_product(self, product_id: int) -> dict | None:
        rows = self.products[self.products["product_id"] == int(product_id)]
        if rows.empty:
            return None
        return self._product_dict(rows.iloc[0], full=True)

    def _match_query(self, df: pd.DataFrame, query: str) -> tuple[pd.DataFrame, bool]:
        """All query words must match (exact word or word prefix). If nothing
        matches, fall back to the products matching the most words."""
        q_tokens = tokens(query)
        if not q_tokens:
            return df, False
        compact_q = normalize(query).replace(" ", "")

        def score(row) -> int:
            hay = row["search_text"].split()
            hits = sum(
                any(h == t or (len(t) >= 3 and h.startswith(t)) for h in hay) for t in q_tokens
            )
            if len(compact_q) >= 4 and compact_q in row["compact_name"]:
                hits = len(q_tokens)
            return hits

        scores = df.apply(score, axis=1) if len(df) else pd.Series(dtype=int)
        full = df[scores == len(q_tokens)]
        if not full.empty:
            return full, False
        best = scores.max() if len(scores) else 0
        if best and best > 0:
            return df[scores == best], True
        return df.iloc[0:0], False

    @staticmethod
    def _product_dict(r: pd.Series, full: bool = False) -> dict:
        has_promo = pd.notna(r["discount_percent"])
        item = {
            "product_id": int(r["product_id"]),
            "name": r["name"],
            "category": r["category"],
            "availability": AVAILABILITY_LABELS[r["availability"]],
            "stock_quantity": int(r["stock_quantity"]) if r["availability"] == "available" else 0,
            "list_price_brl": float(r["price_brl"]),
            "current_price_brl": float(r["current_price_brl"]),
        }
        if has_promo:
            item["promotion"] = {
                "name": r["promotion_name"],
                "discount_percent": int(r["discount_percent"]),
                "promo_price_brl": float(r["promo_price_brl"]),
                "note": "Preço promocional; o desconto do PIX não se acumula com a promoção.",
            }
        else:
            item["pix_price_brl"] = round(float(r["price_brl"]) * (1 - PIX_DISCOUNT_PERCENT / 100), 2)
        if full:
            item["description"] = r["description"]
            item["specs"] = r["specs"]
        return item

    # ------------------------------------------------------------------ orders

    def get_order(self, order_id: int, phone_or_email: str) -> dict:
        """Order details, only if the contact matches the order's customer.

        The same generic error is returned whether the order does not exist or
        the contact does not match, so the tool cannot be used to discover which
        order numbers exist (privacy, LGPD)."""
        not_found = {
            "found": False,
            "message": "Não localizamos um pedido com esse número e esse contato. "
                       "Confira os dados ou fale com a equipe.",
        }
        try:
            order_id = int(order_id)
        except (TypeError, ValueError):
            return not_found
        rows = self.orders[self.orders["order_id"] == order_id]
        if rows.empty:
            return not_found
        order = rows.iloc[0]
        customer = self.customers[self.customers["customer_id"] == order["customer_id"]].iloc[0]
        if not self._contact_matches(customer, phone_or_email):
            return not_found
        return {"found": True, **self._order_dict(order, customer)}

    def find_customer_orders(self, phone: str, email: str) -> dict:
        """List a customer's orders when they do not know the order number.
        Requires phone AND email (one e-mail is shared by two customers)."""
        digits = phone_digits(phone)
        email_n = str(email).strip().casefold()
        match = self.customers[
            (self.customers["email_norm"] == email_n)
            & self.customers["phone_digits"].map(lambda p: _phone_equal(p, digits))
        ]
        if match.empty:
            return {"found": False, "message": "Não localizamos cadastro com esse telefone e e-mail."}
        customer = match.iloc[0]
        orders = self.orders[self.orders["customer_id"] == customer["customer_id"]].sort_values(
            "order_date", ascending=False
        )
        return {
            "found": True,
            "customer_first_name": customer["name"].split()[0],
            "orders": [self._order_dict(o, customer) for _, o in orders.iterrows()],
        }

    @staticmethod
    def _contact_matches(customer: pd.Series, phone_or_email: str) -> bool:
        value = str(phone_or_email).strip()
        if "@" in value:
            return value.casefold() == customer["email_norm"]
        return _phone_equal(customer["phone_digits"], phone_digits(value))

    def _order_dict(self, order: pd.Series, customer: pd.Series) -> dict:
        items = self.order_items[self.order_items["order_id"] == order["order_id"]].merge(
            self.products[["product_id", "name"]], on="product_id", how="left"
        )
        payment = order["payment_method"]
        if payment.startswith("credit_"):
            payment_label = f"cartão de crédito em {payment.split('_')[1]}"
        else:
            payment_label = PAYMENT_LABELS.get(payment, payment)
        tracking = order["tracking_code"]
        notes = order["notes"]
        return {
            "order_id": int(order["order_id"]),
            "customer_first_name": customer["name"].split()[0],
            "customer_city": customer["city"],
            "order_date": order["order_date"].date().isoformat(),
            "status": order["status"],
            "status_label": ORDER_STATUS_LABELS.get(order["status"], order["status"]),
            # The recorded total is reported as is. In two orders it differs from
            # the sum of current list prices (likely a discount at the time).
            "total_brl": float(order["total_brl"]),
            "payment": payment_label,
            "items": [
                {"product_id": int(i.product_id), "name": i.name, "quantity": int(i.quantity)}
                for i in items.itertuples()
            ],
            "tracking_code": None if pd.isna(tracking) else str(tracking),
            "estimated_delivery": None
            if pd.isna(order["estimated_delivery"])
            else order["estimated_delivery"].date().isoformat(),
            "notes": None if pd.isna(notes) else str(notes),
        }


def _phone_equal(stored: str, given: str) -> bool:
    """Equal with or without area code (DDD); at least 8 digits required."""
    if len(given) < 8:
        return False
    return stored == given or stored.endswith(given)


def _availability(row: pd.Series) -> str:
    """Status column and stock disagree in the data, so both are combined:
    * active + stock > 0            -> available
    * active + stock == 0           -> out_of_stock (e.g. Giannini GF-3D)
    * discontinued (even with stock) -> discontinued (e.g. Ibanez Bass 2X has 13)
    * coming_soon                   -> coming_soon (Kala KA-ABP-CTG)
    """
    if row["status"] == "discontinued":
        return "discontinued"
    if row["status"] == "coming_soon":
        return "coming_soon"
    return "available" if row["stock_quantity"] > 0 else "out_of_stock"

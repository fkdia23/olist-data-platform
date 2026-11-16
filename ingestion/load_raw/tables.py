# ingestion/load_raw/tables.py
"""Description des tables à ingérer : contrat, partitionnement, références à vérifier."""

from dataclasses import dataclass, field

from contracts import olist as c


@dataclass(frozen=True)
class TableSpec:
    name: str  # même nom côté source et dans le schéma raw
    contract: type[c.Contract]  # modèle Pydantic qui valide chaque ligne
    needs_order_date: bool = False  # recopier order_purchase_timestamp depuis raw.orders ?
    # Intégrité référentielle : {colonne: (table raw, colonne référencée)}
    references: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def columns(self) -> tuple[str, ...]:
        """Colonnes du contrat, dans l'ordre où elles sont déclarées."""
        return tuple(self.contract.model_fields)


# L'ORDRE COMPTE : une table est chargée après celles qu'elle référence.
TABLES: list[TableSpec] = [
    TableSpec("geolocation", c.Geolocation),
    TableSpec("customers", c.Customer),
    TableSpec("sellers", c.Seller),
    TableSpec("products", c.Product),
    TableSpec("orders", c.Order, references={"customer_id": ("raw.customers", "customer_id")}),
    TableSpec(
        "order_items",
        c.OrderItem,
        needs_order_date=True,
        references={
            "order_id": ("raw.orders", "order_id"),
            "product_id": ("raw.products", "product_id"),
            "seller_id": ("raw.sellers", "seller_id"),
        },
    ),
    TableSpec(
        "order_payments",
        c.OrderPayment,
        needs_order_date=True,
        references={"order_id": ("raw.orders", "order_id")},
    ),
    TableSpec(
        "order_reviews",
        c.OrderReview,
        needs_order_date=True,
        references={"order_id": ("raw.orders", "order_id")},
    ),
]

PARTITIONED = ("orders", "order_items", "order_payments", "order_reviews")
BY_NAME = {spec.name: spec for spec in TABLES}

# contracts/olist.py
"""Contrats de données Olist : ce que l'ingestion accepte, colonne par colonne.

Un contrat vérifie la STRUCTURE (présence, type, format, valeurs permises).
Les règles MÉTIER (prix positif, note entre 1 et 5) sont contrôlées plus tard par dbt.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

TS_FORMAT = "%Y-%m-%d %H:%M:%S"  # seul format d'horodatage accepté


def parse_timestamp(value: object) -> object:
    """Convertit '2025-03-14 10:22:05' en datetime ; tout autre format lève une erreur."""
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError(f"horodatage attendu en texte, reçu {type(value).__name__}")
    return datetime.strptime(value, TS_FORMAT)  # ValueError si le format diffère


# Types réutilisables : un nom lisible pour chaque règle de format
Timestamp = Annotated[datetime, BeforeValidator(parse_timestamp)]
OptionalTimestamp = Annotated[datetime | None, BeforeValidator(parse_timestamp)]
Id32 = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]  # identifiants Olist : 32 hexadécimaux
ZipPrefix = Annotated[str, Field(pattern=r"^\d{5}$")]  # préfixe postal à 5 chiffres
StateCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]  # code d'État brésilien
NonEmpty = Annotated[str, Field(min_length=1)]


class Contract(BaseModel):
    # extra="ignore" : une colonne en plus (dont _src_id) est ignorée sans rejeter la ligne.
    # Passe à "forbid" si tu veux qu'un ajout de colonne côté fournisseur soit une rupture de contrat.
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True)


class Geolocation(Contract):
    geolocation_zip_code_prefix: ZipPrefix
    geolocation_lat: float = Field(ge=-90, le=90)
    geolocation_lng: float = Field(ge=-180, le=180)
    geolocation_city: NonEmpty
    geolocation_state: StateCode


class Customer(Contract):
    customer_id: Id32
    customer_unique_id: Id32
    customer_zip_code_prefix: ZipPrefix
    customer_city: NonEmpty
    customer_state: StateCode


class Seller(Contract):
    seller_id: Id32
    seller_zip_code_prefix: ZipPrefix
    seller_city: NonEmpty
    seller_state: StateCode


class Product(Contract):
    product_id: Id32
    product_category_name: str | None = None  # Olist contient des produits sans catégorie
    product_name_lenght: int | None = Field(default=None, ge=0)
    product_description_lenght: int | None = Field(default=None, ge=0)
    product_photos_qty: int | None = Field(default=None, ge=0)
    product_weight_g: int | None = Field(default=None, ge=0)
    product_length_cm: int | None = Field(default=None, ge=0)
    product_height_cm: int | None = Field(default=None, ge=0)
    product_width_cm: int | None = Field(default=None, ge=0)


class Order(Contract):
    order_id: Id32
    customer_id: Id32
    order_status: Literal[
        "created", "approved", "invoiced", "processing", "shipped", "delivered", "canceled", "unavailable"
    ]
    order_purchase_timestamp: Timestamp
    order_approved_at: OptionalTimestamp = None
    order_delivered_carrier_date: OptionalTimestamp = None
    order_delivered_customer_date: OptionalTimestamp = None
    order_estimated_delivery_date: Timestamp


class OrderItem(Contract):
    order_id: Id32
    order_item_id: int = Field(ge=1)
    product_id: Id32
    seller_id: Id32
    shipping_limit_date: Timestamp
    price: Decimal  # pas de règle « > 0 » ici : c'est du métier (dbt)
    freight_value: Decimal


class OrderPayment(Contract):
    order_id: Id32
    payment_sequential: int = Field(ge=1)
    payment_type: Literal["credit_card", "boleto", "voucher", "debit_card", "not_defined"]
    payment_installments: int = Field(ge=0)
    payment_value: Decimal


class OrderReview(Contract):
    review_id: Id32
    order_id: Id32
    review_score: int  # la plage 1..5 est vérifiée par dbt
    review_comment_title: str | None = None
    review_comment_message: str | None = None
    review_creation_date: Timestamp
    review_answer_timestamp: OptionalTimestamp = None

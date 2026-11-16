# tests/test_contracts.py
"""Tests unitaires des contrats : rapides, sans base de données."""

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from contracts.olist import Order, OrderItem

VALID_ORDER = {
    "_src_id": 1,  # colonne technique : doit être ignorée
    "order_id": "fddfe89bc0f845eabde4ffd496a188bc",
    "customer_id": "9f4b23d36eb24d06851a7d606efe8e88",
    "order_status": "delivered",
    "order_purchase_timestamp": "2026-07-16 02:59:23",
    "order_approved_at": "2026-07-17 16:55:12",
    "order_delivered_carrier_date": None,
    "order_delivered_customer_date": None,
    "order_estimated_delivery_date": "2026-08-09 00:00:00",
}

VALID_ITEM = {
    "order_id": "fddfe89bc0f845eabde4ffd496a188bc",
    "order_item_id": 1,
    "product_id": "85a04b46c0e84703b882d5851a164a1c",
    "seller_id": "ca9f4c16e3d84dc39a2bb4542f45aedd",
    "shipping_limit_date": "2026-07-22 02:59:23",
    "price": "30.91",
    "freight_value": "14.02",
}


def test_valid_order_is_parsed():
    order = Order.model_validate(VALID_ORDER)
    assert order.order_purchase_timestamp == datetime(2026, 7, 16, 2, 59, 23)


def test_non_standard_timestamp_is_rejected():
    bad = VALID_ORDER | {"order_purchase_timestamp": "16/07/2026 02h59"}
    with pytest.raises(ValidationError, match="order_purchase_timestamp"):
        Order.model_validate(bad)


def test_unknown_status_is_rejected():
    with pytest.raises(ValidationError, match="order_status"):
        Order.model_validate(VALID_ORDER | {"order_status": "lost"})


def test_malformed_id_is_rejected():
    with pytest.raises(ValidationError, match="order_id"):
        Order.model_validate(VALID_ORDER | {"order_id": "123"})


def test_price_text_becomes_exact_decimal():
    assert OrderItem.model_validate(VALID_ITEM).price == Decimal("30.91")


def test_negative_price_passes_the_contract():
    """Un prix négatif est un problème MÉTIER : le contrat le laisse passer, dbt le bloquera."""
    assert OrderItem.model_validate(VALID_ITEM | {"price": "-5.00"}).price < 0

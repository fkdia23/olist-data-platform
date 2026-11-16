# tests/test_load_raw.py
"""Tests unitaires du loader : sans base ni API."""

import httpx

from ingestion.load_raw.source_client import is_retryable
from ingestion.load_raw.tables import TABLES


def status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://source/tables/orders")
    return httpx.HTTPStatusError("erreur", request=request, response=httpx.Response(code, request=request))


def test_server_errors_are_retried():
    assert is_retryable(status_error(500))
    assert is_retryable(status_error(503))


def test_client_errors_are_not_retried():
    assert not is_retryable(status_error(404))
    assert not is_retryable(status_error(422))


def test_network_errors_are_retried():
    assert is_retryable(httpx.ConnectError("connexion refusée"))


def test_tables_are_loaded_after_what_they_reference():
    order = [spec.name for spec in TABLES]
    for spec in TABLES:
        for ref_table, _ in spec.references.values():
            assert order.index(ref_table.removeprefix("raw.")) < order.index(spec.name), spec.name

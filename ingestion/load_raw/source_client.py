# ingestion/load_raw/source_client.py
"""Client HTTP de l'API source, avec nouvelles tentatives automatiques."""

import logging

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)


def is_retryable(exc: BaseException) -> bool:
    """On réessaie les pannes passagères (réseau, 5xx), jamais les erreurs de notre requête (4xx)."""
    if isinstance(exc, httpx.TransportError):  # connexion refusée, délai dépassé…
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


class SourceClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.http = httpx.Client(base_url=base_url, timeout=timeout)
        self.retries = 0  # compteur exposé dans les KPIs

    def _on_retry(self, retry_state) -> None:
        """Appelée avant chaque nouvel essai : compte et journalise."""
        self.retries += 1
        log.warning(
            "essai %d échoué (%s), nouvel essai dans %.1f s",
            retry_state.attempt_number,
            retry_state.outcome.exception(),
            retry_state.next_action.sleep,
        )

    def fetch_page(self, table: str, after_id: int, limit: int) -> dict:
        """Une page de lignes ; jusqu'à 5 tentatives espacées de 0,5 s, 1 s, 2 s, 4 s."""

        @retry(
            retry=retry_if_exception(is_retryable),
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=0.5, max=10),
            before_sleep=self._on_retry,
            reraise=True,  # après 5 échecs, on remonte l'erreur d'origine
        )
        def _get() -> dict:
            response = self.http.get(f"/tables/{table}", params={"after_id": after_id, "limit": limit})
            response.raise_for_status()  # transforme 4xx/5xx en exception
            return response.json()

        return _get()

    def close(self) -> None:
        self.http.close()

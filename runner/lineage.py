# runner/lineage.py
"""OpenLineage côté runner : un run par scénario, un run enfant par étape.

  olist/scenario.nominal                     run parent (START, puis COMPLETE ou FAIL)
    ├─ olist/generate_normal                 décrit par le runner : sorties = tables de la source
    ├─ olist/load_raw                        source -> raw, avec le nombre de lignes insérées
    ├─ olist/dbt-run-olist                   émis par dbt-ol lui-même, rattaché via OPENLINEAGE_PARENT_ID
    └─ olist/publish_duckdb                  marts -> mart.duckdb

Les datasets portent les mêmes noms que ceux de dbt-ol (postgres://pg16:5432, warehouse.raw.orders) :
Marquez raccorde donc les graphes, de la source jusqu'au fichier DuckDB.
Une panne de Marquez ne fait jamais échouer un run : chaque émission est protégée.
"""

import os
from datetime import UTC, datetime
from urllib.parse import urlparse

from openlineage.client import OpenLineageClient
from openlineage.client.event_v2 import InputDataset, Job, OutputDataset, Run, RunEvent, RunState
from openlineage.client.facet_v2 import (
    error_message_run,
    job_type_job,
    output_statistics_output_dataset,
    parent_run,
)
from openlineage.client.uuid import generate_new_uuid

NAMESPACE = os.environ.get("OPENLINEAGE_NAMESPACE", "olist")
PRODUCER = "https://github.com/olist-data-platform/runner"
START, COMPLETE, FAIL = RunState.START, RunState.COMPLETE, RunState.FAIL

Dataset = tuple[str, str]  # (namespace, nom), ex. ("postgres://pg16:5432", "warehouse.raw.orders")

_client: OpenLineageClient | None = None


def _client_or_none() -> OpenLineageClient | None:
    """OPENLINEAGE_URL définie : HTTP vers Marquez ; sinon la console. Créé au premier usage."""
    global _client
    if _client is None:
        _client = OpenLineageClient()
    return _client


def _pg(dsn_var: str, default: str) -> tuple[str, str]:
    """Namespace OpenLineage d'une base PostgreSQL : postgres://hôte:port, et le nom de la base."""
    url = urlparse(os.environ.get(dsn_var, default))
    return f"postgres://{url.hostname}:{url.port or 5432}", url.path.lstrip("/")


def source(tables: tuple[str, ...]) -> tuple[Dataset, ...]:
    ns, db = _pg("SOURCE_DSN", "postgresql://localhost:5433/olist_source")
    return tuple((ns, f"{db}.public.{t}") for t in tables)


def warehouse(schema: str, tables: tuple[str, ...]) -> tuple[Dataset, ...]:
    ns, db = _pg("WAREHOUSE_DSN", "postgresql://localhost:5432/warehouse")
    return tuple((ns, f"{db}.{schema}.{t}") for t in tables)


def duckdb(tables: tuple[str, ...]) -> tuple[Dataset, ...]:
    return tuple(("duckdb", f"mart.main.{t}") for t in tables)


def new_run_id() -> str:
    return str(generate_new_uuid())  # UUID v7, recommandé par la spécification


def emit(
    state: RunState,
    job_name: str,
    run_id: str,
    *,
    parent: tuple[str, str] | None = None,  # (nom du job parent, run id parent)
    inputs: tuple[Dataset, ...] = (),
    outputs: tuple[Dataset, ...] = (),
    row_counts: dict[str, int] | None = None,  # nom du dataset -> lignes écrites
    error: str | None = None,
    job_type: str = "TASK",
) -> int:
    """Émet un événement ; renvoie 1 s'il est parti, 0 sinon (compté dans les KPIs du run)."""
    run_facets = {}
    if parent:
        run_facets["parent"] = parent_run.ParentRunFacet(
            run=parent_run.Run(runId=parent[1]), job=parent_run.Job(namespace=NAMESPACE, name=parent[0])
        )
    if error:
        run_facets["errorMessage"] = error_message_run.ErrorMessageRunFacet(
            message=error, programmingLanguage="shell"
        )
    counts = row_counts or {}
    event = RunEvent(
        eventType=state,
        eventTime=datetime.now(UTC).isoformat(),
        run=Run(runId=run_id, facets=run_facets),
        job=Job(
            namespace=NAMESPACE,
            name=job_name,
            facets={
                "jobType": job_type_job.JobTypeJobFacet(
                    processingType="BATCH", integration="OLIST_RUNNER", jobType=job_type
                )
            },
        ),
        producer=PRODUCER,
        inputs=[InputDataset(namespace=ns, name=name) for ns, name in inputs],
        outputs=[
            OutputDataset(
                namespace=ns,
                name=name,
                outputFacets=(
                    {
                        "outputStatistics": output_statistics_output_dataset.OutputStatisticsOutputDatasetFacet(
                            rowCount=counts[name]
                        )
                    }
                    if name in counts
                    else None
                ),
            )
            for ns, name in outputs
        ],
    )
    try:
        _client_or_none().emit(event)
        return 1
    except Exception as exc:  # Marquez éteint, réseau coupé : on le note, le run continue
        print(f"openlineage: {job_name} {state.value} non émis : {exc}", flush=True)
        return 0

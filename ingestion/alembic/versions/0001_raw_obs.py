# ingestion/alembic/versions/0001_raw_obs.py
"""Schémas raw et obs : tables brutes partitionnées, rejets, observabilité.

Revision ID: 0001_raw_obs
Revises:
Create Date: 2026-11-13
"""

from alembic import op

revision = "0001_raw_obs"  # identifiant lisible, choisi à la main
down_revision = None  # première migration : rien avant elle
branch_labels = None
depends_on = None

# Colonnes techniques ajoutées à chaque table brute
META = """
    _src_id      bigint      not null,             -- identifiant de la ligne chez le fournisseur
    _batch_id    bigint,                           -- run d'ingestion qui l'a insérée (obs.pipeline_runs)
    _ingested_at timestamptz not null default now()
"""

UPGRADE = [
    "create schema if not exists raw",
    "create schema if not exists obs",
    # ---------- Dimensions : petites, non partitionnées ----------
    f"""create table raw.geolocation (
        row_hash                    text primary key,  -- md5 de la ligne : la table n'a pas de clé naturelle
        geolocation_zip_code_prefix text             not null,
        geolocation_lat             double precision not null,
        geolocation_lng             double precision not null,
        geolocation_city            text             not null,
        geolocation_state           text             not null,
        {META})""",
    f"""create table raw.customers (
        customer_id              text primary key,
        customer_unique_id       text not null,
        customer_zip_code_prefix text not null,
        customer_city            text not null,
        customer_state           text not null,
        {META})""",
    f"""create table raw.sellers (
        seller_id              text primary key,
        seller_zip_code_prefix text not null,
        seller_city            text not null,
        seller_state           text not null,
        {META})""",
    f"""create table raw.products (
        product_id                 text primary key,
        product_category_name      text,
        product_name_lenght        integer,
        product_description_lenght integer,
        product_photos_qty         integer,
        product_weight_g           integer,
        product_length_cm          integer,
        product_height_cm          integer,
        product_width_cm           integer,
        {META})""",
    # ---------- Faits : partitionnés par mois de commande ----------
    # Règle PostgreSQL : la clé primaire d'une table partitionnée DOIT contenir la colonne de partition.
    f"""create table raw.orders (
        order_id                      text      not null,
        customer_id                   text      not null,
        order_status                  text      not null,
        order_purchase_timestamp      timestamp not null,
        order_approved_at             timestamp,
        order_delivered_carrier_date  timestamp,
        order_delivered_customer_date timestamp,
        order_estimated_delivery_date timestamp not null,
        {META},
        primary key (order_id, order_purchase_timestamp)
    ) partition by range (order_purchase_timestamp)""",
    f"""create table raw.order_items (
        order_id                 text           not null,
        order_item_id            integer        not null,
        product_id               text           not null,
        seller_id                text           not null,
        shipping_limit_date      timestamp      not null,
        price                    numeric(10, 2) not null,
        freight_value            numeric(10, 2) not null,
        order_purchase_timestamp timestamp      not null,  -- recopiée de la commande pour partitionner
        {META},
        primary key (order_id, order_item_id, order_purchase_timestamp)
    ) partition by range (order_purchase_timestamp)""",
    f"""create table raw.order_payments (
        order_id                 text           not null,
        payment_sequential       integer        not null,
        payment_type             text           not null,
        payment_installments     integer        not null,
        payment_value            numeric(10, 2) not null,
        order_purchase_timestamp timestamp      not null,
        {META},
        primary key (order_id, payment_sequential, order_purchase_timestamp)
    ) partition by range (order_purchase_timestamp)""",
    f"""create table raw.order_reviews (
        review_id                text      not null,
        order_id                 text      not null,
        review_score             integer   not null,
        review_comment_title     text,
        review_comment_message   text,
        review_creation_date     timestamp not null,
        review_answer_timestamp  timestamp,
        order_purchase_timestamp timestamp not null,
        {META},
        primary key (review_id, order_id, order_purchase_timestamp)
    ) partition by range (order_purchase_timestamp)""",
    # ---------- Lignes refusées par le contrat ----------
    """create table raw.rejects (
        table_name  text        not null,
        src_id      bigint      not null,
        reason      text        not null,              -- message lisible : champ et erreur
        payload     jsonb       not null,              -- la ligne telle que reçue
        _batch_id   bigint,
        rejected_at timestamptz not null default now(),
        primary key (table_name, src_id)               -- relancer ne crée pas de doublon
    )""",
    # ---------- Création des partitions mensuelles à la demande ----------
    r"""create or replace function raw.ensure_monthly_partitions(p_parent text, p_from date, p_to date)
    returns integer
    language plpgsql
    as $$
    declare
        v_month   date    := date_trunc('month', p_from)::date;
        v_name    text;
        v_created integer := 0;
    begin
        while v_month <= p_to loop
            v_name := format('%s_p%s', p_parent, to_char(v_month, 'YYYY_MM'));   -- ex. orders_p2025_01
            if to_regclass(format('raw.%I', v_name)) is null then
                execute format('create table raw.%I partition of raw.%I for values from (%L) to (%L)',
                               v_name, p_parent, v_month, (v_month + interval '1 month')::date);
                v_created := v_created + 1;
            end if;
            v_month := (v_month + interval '1 month')::date;
        end loop;
        return v_created;
    end;
    $$""",
    # ---------- Observabilité ----------
    """create table obs.pipeline_runs (
        run_id       bigserial primary key,
        scenario     text        not null,
        trigger      text        not null,
        schedule_id  text,
        status       text        not null default 'queued',
        requested_at timestamptz not null default now(),
        started_at   timestamptz,
        finished_at  timestamptz,
        error        text
    )""",
    """create table obs.run_metrics (
        run_id      bigint references obs.pipeline_runs,
        step        text             not null,
        metric      text             not null,
        value       double precision not null,
        recorded_at timestamptz      not null default now()
    )""",
    """create table obs.run_logs (
        run_id    bigint references obs.pipeline_runs,
        logged_at timestamptz not null default now(),
        message   text        not null
    )""",
    """create table obs.alerts (
        alert_id    bigserial primary key,
        received_at timestamptz not null default now(),
        alertname   text,
        severity    text,
        status      text,
        payload     jsonb
    )""",
    """create table obs.schedules (
        schedule_id text primary key,
        cron        text        not null,
        scenario    text        not null,
        enabled     boolean     not null default true,
        updated_at  timestamptz not null default now()
    )""",
    """create table obs.watermarks (
        table_name  text primary key,
        last_src_id bigint      not null default 0,    -- plus grand _src_id déjà traité
        updated_at  timestamptz not null default now()
    )""",
    "create index on obs.pipeline_runs (status, requested_at)",
    "create index on obs.run_logs (run_id, logged_at)",
    "create index on obs.run_metrics (step, recorded_at)",
    # Volume par partition, lu par Grafana (KPI « volumétrie par partition »)
    """create view obs.partition_sizes as
    select parent.relname                    as table_name,
           child.relname                     as partition_name,
           greatest(child.reltuples, 0)::bigint as estimated_rows,
           pg_total_relation_size(child.oid) as size_bytes
    from pg_inherits i
    join pg_class parent on parent.oid = i.inhparent
    join pg_class child  on child.oid  = i.inhrelid
    join pg_namespace n  on n.oid = parent.relnamespace
    where n.nspname = 'raw'
      and child.relkind = 'r'                -- tables seulement (pas les index partitionnés)""",
]

DOWNGRADE = [
    "drop schema if exists obs cascade",
    "drop schema if exists raw cascade",
]


def upgrade() -> None:
    for statement in UPGRADE:  # une instruction à la fois : messages d'erreur précis
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)

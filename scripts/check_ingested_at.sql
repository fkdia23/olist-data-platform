-- scripts/check_ingested_at.sql : la colonne _ingested_at, prérequis de « dbt source freshness ».
-- Lancé par « make raw-check » avec psql -v ON_ERROR_STOP=1 : code retour non nul si le contrôle 2 échoue.
-- Lecture seule : ce script ne modifie rien.
\pset footer off

\echo '1. Définition de _ingested_at dans chaque table brute'
select c.relname                                                  as table_brute,
       case c.relkind when 'p' then 'partitionnée' else 'simple' end as genre,
       format_type(a.atttypid, a.atttypmod)                       as type,
       a.attnotnull                                               as non_nulle,
       pg_get_expr(d.adbin, d.adrelid)                            as defaut
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
left join pg_attribute a on a.attrelid = c.oid and a.attname = '_ingested_at' and not a.attisdropped
left join pg_attrdef d on d.adrelid = c.oid and d.adnum = a.attnum
where n.nspname = 'raw'
  and c.relkind in ('r', 'p')          -- r : table simple, p : table partitionnée
  and not c.relispartition             -- les partitions sont vérifiées au contrôle 2
  and c.relname <> 'rejects'           -- les rejets ont leur propre horodatage : rejected_at
order by c.relname;

\echo '2. Contrôle bloquant : timestamptz, non nulle, default now(), partitions comprises'
do $$
declare
    v_ko text;
begin
    select string_agg(c.relname, ', ' order by c.relname) into v_ko
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    left join pg_attribute a on a.attrelid = c.oid and a.attname = '_ingested_at' and not a.attisdropped
    left join pg_attrdef d on d.adrelid = c.oid and d.adnum = a.attnum
    where n.nspname = 'raw'
      and c.relkind in ('r', 'p')
      and c.relname <> 'rejects'
      and (a.attnum is null                                  -- colonne absente
           or a.atttypid <> 'timestamptz'::regtype           -- mauvais type
           or not a.attnotnull                               -- nulls permis
           or coalesce(pg_get_expr(d.adbin, d.adrelid), '') <> 'now()');  -- pas de valeur par défaut
    if v_ko is not null then
        raise exception '_ingested_at absente ou mal définie dans : %', v_ko;
    end if;
    raise notice 'OK : _ingested_at conforme dans toutes les tables brutes et leurs partitions';
end $$;

\echo '3. Remplissage : lignes, première et dernière ingestion, âge de la dernière'
select table_brute, lignes,
       date_trunc('second', premiere)       as premiere,
       date_trunc('second', derniere)       as derniere,
       date_trunc('second', now() - derniere) as age_derniere
from (
    select 'customers' as table_brute, count(*) as lignes,
           min(_ingested_at) as premiere, max(_ingested_at) as derniere from raw.customers
    union all select 'geolocation',    count(*), min(_ingested_at), max(_ingested_at) from raw.geolocation
    union all select 'order_items',    count(*), min(_ingested_at), max(_ingested_at) from raw.order_items
    union all select 'order_payments', count(*), min(_ingested_at), max(_ingested_at) from raw.order_payments
    union all select 'order_reviews',  count(*), min(_ingested_at), max(_ingested_at) from raw.order_reviews
    union all select 'orders',         count(*), min(_ingested_at), max(_ingested_at) from raw.orders
    union all select 'products',       count(*), min(_ingested_at), max(_ingested_at) from raw.products
    union all select 'sellers',        count(*), min(_ingested_at), max(_ingested_at) from raw.sellers
) t
order by table_brute;
-- dbt/tests/assert_quarantine_under_5_percent.sql
{{ config(severity = 'warn') }}                            -- signal, pas blocage
select
    count(*) filter (where not is_valid)::numeric
        / nullif(count(*), 0)            as quarantine_ratio   -- part des lignes rejetées
from {{ ref('int_order_items__checked') }}
having count(*) filter (where not is_valid)::numeric
        / nullif(count(*), 0) > 0.05                       -- renvoie une ligne si plus de 5 %
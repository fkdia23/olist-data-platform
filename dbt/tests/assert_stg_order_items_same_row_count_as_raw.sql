-- dbt/tests/assert_stg_order_items_same_row_count_as_raw.sql
-- Test singulier : renvoie une ligne (donc échoue) si staging a perdu ou dupliqué des lignes.
with raw_count as (
    select count(*) as n from {{ source('raw', 'order_items') }}
),

stg_count as (
    select count(*) as n from {{ ref('stg_order_items') }}
)

select raw_count.n as raw_rows, stg_count.n as stg_rows
from raw_count cross join stg_count
where raw_count.n <> stg_count.n
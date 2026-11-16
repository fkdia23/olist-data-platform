-- dbt/models/intermediate/int_order_items__valid.sql
-- Lignes qui passent toutes les règles : elles seules alimentent les marts.
select * from {{ ref('int_order_items__checked') }}
where is_valid
-- dbt/models/intermediate/int_order_items__checked.sql
with items as (
    select * from {{ ref('stg_order_items') }}             -- ref() : dépendance vers un autre modèle dbt
),

orders as (
    select order_id, purchased_at, delivered_at from {{ ref('stg_orders') }}
),

checked as (
    select
        items.*,
        orders.purchased_at,
        coalesce(items.price > 0, false)          as is_valid_price,      -- règle 1 ; un prix NULL compte comme faux
        coalesce(items.freight_value >= 0, false) as is_valid_freight,    -- règle 2 : frais de port non négatifs
        coalesce(orders.delivered_at is null
                 or orders.delivered_at >= orders.purchased_at, false)
                                                  as is_date_consistent   -- règle 3 : pas de livraison avant l'achat
    from items
    left join orders using (order_id)              -- left join : un article sans commande reste visible
)

select
    *,
    is_valid_price and is_valid_freight and is_date_consistent as is_valid   -- synthèse des règles
from checked
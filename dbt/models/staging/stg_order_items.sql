-- dbt/models/staging/stg_order_items.sql
-- Une ligne par article de commande : grain (order_id, order_item_id). Aucune règle métier ici.
with source as (
    select * from {{ source('raw', 'order_items') }}       -- dépendance tracée vers raw.order_items
),

renamed as (
    select
        order_id,                                           -- clé de la commande (relie à stg_orders)
        order_item_id,                                      -- rang de l'article dans la commande : 1, 2, 3…
        product_id,
        seller_id,
        shipping_limit_date::timestamp as shipping_limit_at, -- suffixe _at, comme dans stg_orders
        price,                                              -- numeric(10,2) : pas de float pour de l'argent
        freight_value,
        _ingested_at                                        -- métadonnée d'ingestion (fraîcheur, audit)
        -- order_purchase_timestamp volontairement absente : c'est une copie technique qui sert
        -- au partitionnement de raw.order_items. La date d'achat officielle vient de stg_orders ;
        -- la garder ici créerait une deuxième colonne purchased_at dans int_order_items__checked.
    from source
)

select * from renamed
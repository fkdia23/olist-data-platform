-- dbt/models/staging/stg_orders.sql
-- Une ligne par commande. Aucune règle métier ici.
with source as (
    select * from {{ source('raw', 'orders') }}           -- source() : dépendance tracée vers raw.orders
),

renamed as (
    select
        order_id,                                          -- clé naturelle, gardée telle quelle
        customer_id,
        order_status                             as status,          -- noms courts et homogènes
        order_purchase_timestamp::timestamp      as purchased_at,    -- suffixe _at pour les horodatages
        order_approved_at::timestamp             as approved_at,
        order_delivered_carrier_date::timestamp  as shipped_at,
        order_delivered_customer_date::timestamp as delivered_at,
        order_estimated_delivery_date::date      as estimated_delivery_date,
        _ingested_at                                       -- métadonnée d'ingestion, utile pour la fraîcheur
    from source
)

select * from renamed                                      -- un select final lisible : convention dbt
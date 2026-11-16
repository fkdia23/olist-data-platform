-- dbt/models/marts/fct_order_items.sql
-- Grain : une ligne par article de commande.


{{
  config(
    materialized = 'incremental',            
    unique_key = 'order_item_key',          
    incremental_strategy = 'delete+insert',  
    on_schema_change = 'fail'                
  )
}}

with items as (
    select * from {{ ref('int_order_items__valid') }}
    {% if is_incremental() %}                -- ignoré au premier run et avec --full-refresh
    -- Lignes ARRIVÉES depuis le dernier build (_ingested_at), quelle que soit leur date d'achat :
    -- un article livré en retard (30 jours rejoués par la surcharge, source régénérée) est repris.
    -- L'ancien filtre purchased_at > max - 3 jours les perdait, et une table vide (max = NULL)
    -- ne recevait plus rien. coalesce : table vide = tout reprendre.
    -- Hypothèse : aucune ingestion ne tourne PENDANT dbt build (le runner enchaîne les étapes).
    where _ingested_at > (
        select coalesce(max(_ingested_at), '-infinity') from {{ this }}   -- {{ this }} = la table existante
    )
    {% endif %}
)

select
    {{ dbt_utils.generate_surrogate_key(['order_id', 'order_item_id']) }} as order_item_key,  -- clé technique (hash)
    order_id,
    order_item_id,
    {{ dbt_utils.generate_surrogate_key(['product_id']) }} as product_key,  -- clé vers dim_products
    {{ dbt_utils.generate_surrogate_key(['seller_id']) }}  as seller_key,   -- clé vers dim_sellers
    purchased_at,
    purchased_at::date                     as date_key,       -- jointure avec dim_date.date_day
    price,
    freight_value,
    price + freight_value                  as total_amount,   -- mesure dérivée
    _ingested_at                                              -- arrivée dans raw : le repère de l'incrémental
from items
-- dbt/models/intermediate/int_order_items__quarantine.sql
-- Lignes rejetées, gardées avec leur motif pour l'analyse et les KPIs.
select
    *,
    case                                                   -- premier motif rencontré
        when not is_valid_price     then 'prix_non_positif'
        when not is_valid_freight   then 'frais_negatifs'
        when not is_date_consistent then 'livraison_avant_achat'
    end                         as quarantine_reason,
    current_timestamp           as quarantined_at          -- date de mise en quarantaine
from {{ ref('int_order_items__checked') }}
where not is_valid
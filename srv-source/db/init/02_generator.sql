-- srv-source/db/init/02_generator.sql

-- ---------------------------------------------------------------------
-- 1. Référentiels : géolocalisation, vendeurs, produits. Créés une seule fois.
-- ---------------------------------------------------------------------
create or replace function olist_reference(p_scale integer)
returns jsonb
language plpgsql
as $$
declare
    v_states text[] := array['SP','RJ','MG','RS','PR','SC','BA','DF','GO','ES','PE','CE','PA','MT',
                             'MS','MA','PB','RN','PI','AL','SE','TO','RO','AM','AC','AP','RR'];
    v_categories text[] := array['cama_mesa_banho','beleza_saude','esporte_lazer','moveis_decoracao',
                                 'informatica_acessorios','utilidades_domesticas','relogios_presentes',
                                 'telefonia','ferramentas_jardim','automotivo','brinquedos','cool_stuff',
                                 'perfumaria','bebes','eletronicos','papelaria',
                                 'fashion_bolsas_e_acessorios','pet_shop','moveis_escritorio',
                                 'consoles_games'];
begin
    if exists (select 1 from sellers) then
        return jsonb_build_object('skipped', true);       -- déjà présents : on ne recrée rien
    end if;

    -- Géolocalisation : la plus grosse table (1 million de lignes par unité de SCALE)
    insert into geolocation (geolocation_zip_code_prefix, geolocation_lat, geolocation_lng,
                             geolocation_city, geolocation_state)
    select lpad((1001 + floor(random() * 98998))::int::text, 5, '0'),   -- préfixe postal 01001..99998
           -33.7 + random() * 39.0,                                     -- latitude dans le Brésil
           -73.9 + random() * 39.1,                                     -- longitude dans le Brésil
           'cidade_' || floor(random() * 4000)::int,
           v_states[1 + floor(random() * array_length(v_states, 1))::int]  -- un État au hasard
    from generate_series(1, 1000000 * p_scale);

    insert into sellers (seller_id, seller_zip_code_prefix, seller_city, seller_state)
    select replace(gen_random_uuid()::text, '-', ''),                   -- 32 hexadécimaux, comme Olist
           lpad((1001 + floor(random() * 98998))::int::text, 5, '0'),
           'cidade_' || floor(random() * 4000)::int,
           v_states[1 + floor(random() * array_length(v_states, 1))::int]
    from generate_series(1, 3000 * p_scale);

    insert into products (product_id, product_category_name, product_name_lenght,
                          product_description_lenght, product_photos_qty, product_weight_g,
                          product_length_cm, product_height_cm, product_width_cm)
    select replace(gen_random_uuid()::text, '-', ''),
           case when random() < 0.98                                    -- 2 % sans catégorie, comme Olist
                then v_categories[1 + floor(random() * array_length(v_categories, 1))::int] end,
           20 + floor(random() * 57)::int,
           50 + floor(random() * 2950)::int,
           1 + floor(random() * 6)::int,
           50 + floor(random() * 29950)::int,
           10 + floor(random() * 90)::int,
           2 + floor(random() * 98)::int,
           6 + floor(random() * 112)::int
    from generate_series(1, 33000 * p_scale);

    return jsonb_build_object('geolocation', 1000000 * p_scale,
                              'sellers', 3000 * p_scale,
                              'products', 33000 * p_scale);
end;
$$;

-- ---------------------------------------------------------------------
-- 2. Faits d'une période : environ 4 000 commandes par mois et par unité de SCALE.
-- ---------------------------------------------------------------------
create or replace function olist_generate(p_start timestamp, p_end timestamp, p_scale integer)
returns jsonb
language plpgsql
as $$
declare
    v_fmt      constant text := 'YYYY-MM-DD HH24:MI:SS';      -- format attendu par le contrat
    v_states   text[] := array['SP','RJ','MG','RS','PR','SC','BA','DF','GO','ES','PE','CE','PA','MT',
                               'MS','MA','PB','RN','PI','AL','SE','TO','RO','AM','AC','AP','RR'];
    v_months   numeric := extract(epoch from (p_end - p_start)) / (30.44 * 86400);
    v_orders   integer := greatest(1, round(4000 * p_scale * v_months))::int;
    v_products bigint  := (select max(_src_id) from products);
    v_sellers  bigint  := (select max(_src_id) from sellers);
    v_items    bigint;
    v_payments bigint;
    v_reviews  bigint;
begin
    if v_products is null or v_sellers is null then
        raise exception 'Référentiels absents : lancer olist_reference() d''abord';
    end if;

    -- Toutes les dates d'une commande sont tirées en une fois, puis le statut est déduit :
    -- une commande dont la livraison tomberait après p_end n'est pas encore « delivered ».
    -- on commit drop : la table temporaire disparaît à la fin de la transaction.
    create temp table tmp_orders on commit drop as
    with base as (
        select replace(gen_random_uuid()::text, '-', '') as order_id,
               replace(gen_random_uuid()::text, '-', '') as customer_id,
               p_start + random() * (p_end - p_start)    as purchased_at,   -- instant au hasard dans la période
               random()                                   as r
        from generate_series(1, v_orders)
    ), dated as (
        select *,
               purchased_at + random() * interval '2 days'                         as approved_at,
               purchased_at + (15 + floor(random() * 25)::int) * interval '1 day'  as estimated_at
        from base
    ), shipped as (
        select *, approved_at + (1 + random() * 4) * interval '1 day' as carrier_at from dated
    ), delivered as (
        select *, carrier_at + (2 + random() * 18) * interval '1 day' as customer_at from shipped
    )
    select order_id, customer_id, purchased_at, approved_at, carrier_at, customer_at, estimated_at,
           case
               when approved_at > p_end            then 'created'
               when r < 0.006                      then 'canceled'
               when r < 0.012                      then 'unavailable'
               when r < 0.015                      then 'invoiced'
               when r < 0.018                      then 'processing'
               when r < 0.019                      then 'created'
               when r < 0.020                      then 'approved'
               when carrier_at > p_end             then 'approved'
               when customer_at > p_end or r < 0.031 then 'shipped'
               else 'delivered'                                         -- environ 95 % des commandes
           end as status
    from delivered;

    -- Un client par commande (dans Olist, customer_id est propre à chaque commande)
    insert into customers (customer_id, customer_unique_id, customer_zip_code_prefix,
                           customer_city, customer_state)
    select customer_id,
           replace(gen_random_uuid()::text, '-', ''),
           lpad((1001 + floor(random() * 98998))::int::text, 5, '0'),
           'cidade_' || floor(random() * 4000)::int,
           v_states[1 + floor(random() * array_length(v_states, 1))::int]
    from tmp_orders;

    -- Dates écrites en texte au format du contrat ; NULL quand l'étape n'a pas eu lieu
    insert into orders (order_id, customer_id, order_status, order_purchase_timestamp,
                        order_approved_at, order_delivered_carrier_date,
                        order_delivered_customer_date, order_estimated_delivery_date)
    select order_id, customer_id, status,
           to_char(purchased_at, v_fmt),
           case when status <> 'created' then to_char(approved_at, v_fmt) end,
           case when status in ('shipped', 'delivered') then to_char(carrier_at, v_fmt) end,
           case when status = 'delivered' then to_char(customer_at, v_fmt) end,
           to_char(date_trunc('day', estimated_at), v_fmt)              -- date estimée à minuit, comme Olist
    from tmp_orders;

    -- Articles : 1 article dans 88 % des commandes, 2 dans 9 %, 3 dans 3 %.
    -- random() rend la CTE « volatile » : PostgreSQL la calcule une seule fois (matérialisée).
    with lines as (
        select t.order_id, t.purchased_at, g.i as order_item_id,
               (1 + floor(random() * v_products))::bigint as product_sid,  -- produit tiré au hasard
               (1 + floor(random() * v_sellers))::bigint  as seller_sid
        from (select order_id, purchased_at,
                     case when random() < 0.88 then 1 when random() < 0.75 then 2 else 3 end as n_items
              from tmp_orders) t
        cross join lateral generate_series(1, t.n_items) as g(i)       -- n_items lignes par commande
    )
    insert into order_items (order_id, order_item_id, product_id, seller_id, shipping_limit_date,
                             price, freight_value)
    select l.order_id, l.order_item_id, p.product_id, s.seller_id,
           to_char(l.purchased_at + interval '6 days', v_fmt),
           round(exp(2.3 + random() * 4)::numeric, 2),                   -- prix de 10 à 540 environ
           round((7 + random() * 53)::numeric, 2)                        -- frais de port de 7 à 60
    from lines l
    join products p on p._src_id = l.product_sid                      -- _src_id va de 1 à N sans trou
    join sellers  s on s._src_id = l.seller_sid;
    get diagnostics v_items = row_count;                               -- nombre de lignes insérées

    -- Paiement : un par commande, montant = articles + frais de port
    insert into order_payments (order_id, payment_sequential, payment_type, payment_installments,
                                payment_value)
    select order_id, 1, payment_type,
           case when payment_type = 'credit_card' then 1 + floor(random() * 10)::int else 1 end,
           total
    from (select order_id, total,
                 case when random() < 0.74 then 'credit_card'
                      when random() < 0.73 then 'boleto'
                      when random() < 0.78 then 'voucher'
                      else 'debit_card' end as payment_type
          from (select order_id, sum(price + freight_value) as total
                from order_items
                where order_id in (select order_id from tmp_orders)
                group by order_id) totals) typed;
    get diagnostics v_payments = row_count;

    -- Avis : environ 99 % des commandes, note 5 majoritaire, créé après la livraison
    insert into order_reviews (review_id, order_id, review_score, review_comment_title,
                               review_comment_message, review_creation_date, review_answer_timestamp)
    select replace(gen_random_uuid()::text, '-', ''), order_id, score,
           null,
           case when random() < 0.4 then 'Comentario ' || floor(random() * 1000)::int end,
           to_char(date_trunc('day', created_at), v_fmt),
           case when answered_at <= p_end then to_char(answered_at, v_fmt) end
    from (select order_id, created_at,
                 created_at + (1 + random() * 3) * interval '1 day' as answered_at,
                 case when r2 < 0.57 then 5 when r2 < 0.76 then 4 when r2 < 0.84 then 3
                      when r2 < 0.87 then 2 else 1 end as score
          from (select order_id, random() as r2,
                       case when status = 'delivered' then customer_at
                            else purchased_at + interval '10 days' end + interval '1 day' as created_at
                from tmp_orders) t) x
    where created_at <= p_end                                          -- pas d'avis dans le futur
      and random() < 0.99;
    get diagnostics v_reviews = row_count;

    return jsonb_build_object('customers', v_orders, 'orders', v_orders, 'order_items', v_items,
                              'order_payments', v_payments, 'order_reviews', v_reviews);
end;
$$;

-- ---------------------------------------------------------------------
-- 3. Génération incrémentale : de la fin de la dernière génération (ou du 2025-01-01)
--    jusqu'à maintenant.
-- ---------------------------------------------------------------------
create or replace function olist_generate_until_now(p_scale integer)
returns jsonb
language plpgsql
as $$
declare
    v_start  timestamp := coalesce((select max(period_end) from generation_runs),
                                   timestamp '2025-01-01 00:00:00');
    v_end    timestamp := localtimestamp(0);                          -- maintenant, à la seconde
    v_run    integer;
    v_counts jsonb;
begin
    if v_end - v_start < interval '1 minute' then
        return jsonb_build_object('skipped', 'période vide');
    end if;
    insert into generation_runs (scale, period_start, period_end)
    values (p_scale, v_start, v_end)
    returning run_id into v_run;

    v_counts := olist_generate(v_start, v_end, p_scale);

    update generation_runs
    set finished_at = clock_timestamp(), row_counts = v_counts
    where run_id = v_run;
    return v_counts || jsonb_build_object('period_start', v_start, 'period_end', v_end);
end;
$$;

-- ---------------------------------------------------------------------
-- 4. Scénario 2 : casser le contrat (dates illisibles, clients orphelins).
-- ---------------------------------------------------------------------
create or replace function olist_break_contract(p_rows integer default 50)
returns jsonb
language plpgsql
as $$
begin
    -- Horodatage au format jour/mois/année avec un « h » : illisible pour le contrat
    insert into orders (order_id, customer_id, order_status, order_purchase_timestamp,
                        order_estimated_delivery_date)
    select replace(gen_random_uuid()::text, '-', ''), c.customer_id, 'created',
           to_char(localtimestamp, 'DD/MM/YYYY HH24"h"MI'),
           to_char(date_trunc('day', localtimestamp + interval '20 days'), 'YYYY-MM-DD HH24:MI:SS')
    from (select customer_id from customers order by _src_id limit p_rows) c;

    -- customer_id qui n'existe dans aucune table : rupture d'intégrité référentielle
    insert into orders (order_id, customer_id, order_status, order_purchase_timestamp,
                        order_estimated_delivery_date)
    select replace(gen_random_uuid()::text, '-', ''),
           replace(gen_random_uuid()::text, '-', ''),
           'created',
           to_char(localtimestamp, 'YYYY-MM-DD HH24:MI:SS'),
           to_char(date_trunc('day', localtimestamp + interval '20 days'), 'YYYY-MM-DD HH24:MI:SS')
    from generate_series(1, p_rows);

    return jsonb_build_object('bad_timestamp', p_rows, 'orphan_customer', p_rows);
end;
$$;

-- ---------------------------------------------------------------------
-- 5. Scénario 3 : dégrader la qualité. Des articles CONFORMES au contrat (types et formats
--    justes, références existantes) mais faux pour le métier : prix nul ou frais de port
--    négatifs. L'ingestion les accepte, dbt les met en quarantaine (int_order_items__checked).
--    p_pct = 8 : 8 lignes ajoutées pour 100 existantes, soit 7,4 % du total, au-dessus du seuil
--    de 5 % du test assert_quarantine_under_5_percent.
-- ---------------------------------------------------------------------
create or replace function olist_degrade_quality(p_pct numeric default 8)
returns jsonb
language plpgsql
as $$
declare
    v_rows     integer := ceil((select count(*) from order_items) * p_pct / 100.0)::int;
    v_inserted integer;
begin
    with last_items as (                       -- la dernière ligne de chaque commande
        select distinct on (order_id) order_id, order_item_id, product_id, seller_id,
               shipping_limit_date, price, freight_value
        from order_items
        order by order_id, order_item_id desc
    ), sample as (
        select *, row_number() over (order by random()) as n from last_items
    )
    insert into order_items (order_id, order_item_id, product_id, seller_id, shipping_limit_date,
                             price, freight_value)
    select order_id,
           order_item_id + 1,                   -- rang suivant : nouvelle clé, nouveau _src_id
           product_id, seller_id, shipping_limit_date,
           case when n % 2 = 0 then 0 else price end,                     -- une sur deux : prix nul
           case when n % 2 = 1 then -freight_value else freight_value end -- l'autre : frais < 0
    from sample
    where n <= v_rows;
    get diagnostics v_inserted = row_count;

    return jsonb_build_object('bad_items', v_inserted, 'pct_added', p_pct,
                              'zero_price', v_inserted / 2, 'negative_freight', v_inserted - v_inserted / 2);
end;
$$;

-- ---------------------------------------------------------------------
-- 6. Scénario 4 : surcharge. Rejoue les p_days derniers jours à p_scale fois le volume normal,
--    d'un seul coup : un pic de trafic chez le fournisseur. generation_runs n'est pas touché,
--    la génération incrémentale reprend normalement ensuite.
-- ---------------------------------------------------------------------
create or replace function olist_burst(p_scale integer, p_days integer default 30)
returns jsonb
language plpgsql
as $$
begin
    return olist_generate(localtimestamp(0) - make_interval(days => p_days), localtimestamp(0), p_scale)
           || jsonb_build_object('burst_days', p_days, 'scale', p_scale);
end;
$$;

-- srv-source/db/init/01_schema.sql
-- Schéma du fournisseur simulé (PostgreSQL 13). Colonnes identiques aux CSV Olist.

create table if not exists geolocation (
    _src_id                     bigserial primary key,   -- extraction incrémentale
    geolocation_zip_code_prefix text             not null,
    geolocation_lat             double precision not null,
    geolocation_lng             double precision not null,
    geolocation_city            text             not null,
    geolocation_state           text             not null
);

create table if not exists customers (
    _src_id                  bigserial primary key,
    customer_id              text not null unique,       -- un customer_id par commande, comme Olist
    customer_unique_id       text not null,              -- le vrai client, qui peut commander plusieurs fois
    customer_zip_code_prefix text not null,
    customer_city            text not null,
    customer_state           text not null
);

create table if not exists sellers (
    _src_id                bigserial primary key,
    seller_id              text not null unique,
    seller_zip_code_prefix text not null,
    seller_city            text not null,
    seller_state           text not null
);

create table if not exists products (
    _src_id                    bigserial primary key,
    product_id                 text not null unique,
    product_category_name      text,                     -- peut être vide, comme dans Olist
    product_name_lenght        integer,
    product_description_lenght integer,
    product_photos_qty         integer,
    product_weight_g           integer,
    product_length_cm          integer,
    product_height_cm          integer,
    product_width_cm           integer
);

-- Pas de clé étrangère vers customers : le fournisseur ne garantit rien
create table if not exists orders (
    _src_id                       bigserial primary key,
    order_id                      text not null unique,
    customer_id                   text not null,
    order_status                  text not null,
    order_purchase_timestamp      text not null,         -- texte : le format n'est pas garanti
    order_approved_at             text,
    order_delivered_carrier_date  text,
    order_delivered_customer_date text,
    order_estimated_delivery_date text not null
);

create table if not exists order_items (
    _src_id             bigserial primary key,
    order_id            text           not null,
    order_item_id       integer        not null,         -- rang de l'article dans la commande : 1, 2, 3…
    product_id          text           not null,
    seller_id           text           not null,
    shipping_limit_date text           not null,
    price               numeric(10, 2) not null,         -- numeric : montant exact, jamais de float
    freight_value       numeric(10, 2) not null,
    unique (order_id, order_item_id)
);

create table if not exists order_payments (
    _src_id              bigserial primary key,
    order_id             text           not null,
    payment_sequential   integer        not null,
    payment_type         text           not null,
    payment_installments integer        not null,
    payment_value        numeric(10, 2) not null,
    unique (order_id, payment_sequential)
);

create table if not exists order_reviews (
    _src_id                 bigserial primary key,
    review_id               text    not null,
    order_id                text    not null,
    review_score            integer not null,
    review_comment_title    text,
    review_comment_message  text,
    review_creation_date    text    not null,
    review_answer_timestamp text,
    unique (review_id, order_id)
);

-- Journal des générations : la suivante reprend là où celle-ci s'est arrêtée
create table if not exists generation_runs (
    run_id       serial primary key,
    scale        integer   not null,
    period_start timestamp not null,
    period_end   timestamp not null,
    started_at   timestamp not null default clock_timestamp(),
    finished_at  timestamp,
    row_counts   jsonb
);

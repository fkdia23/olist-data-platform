-- scripts/marquez_db.sql : base et rôle de Marquez dans le pg16 existant. Idempotent : relançable.
-- Lancé par « make marquez-db ». Le mot de passe arrive par la variable d'environnement
-- MARQUEZ_DB_PASSWORD : jamais écrit ici, ni visible dans la ligne de commande.
\set ON_ERROR_STOP on

-- 0. Lit MARQUEZ_DB_PASSWORD (psql 15+) ; sans elle, on s'arrête avec un message clair
\getenv marquez_password MARQUEZ_DB_PASSWORD
\if :{?marquez_password}
\else
  do $$ begin raise exception 'MARQUEZ_DB_PASSWORD absente de l''environnement : lance « make marquez-db »'; end $$;
\endif

-- 1. Le rôle : créé une seule fois, puis son mot de passe est réaligné à chaque passage
select format('create role marquez login password %L', :'marquez_password')
where not exists (select from pg_roles where rolname = 'marquez')
\gexec
alter role marquez with login password :'marquez_password';

-- 2. La base : CREATE DATABASE refuse les transactions et les blocs DO, d'où \gexec
select 'create database marquez owner marquez'
where not exists (select from pg_database where datname = 'marquez')
\gexec

-- 3. Cloisonnement : seule Marquez se connecte à sa base ; elle ne reçoit aucun droit sur l'entrepôt
revoke connect on database marquez from public;
grant connect on database marquez to marquez;

-- 4. Contrôle lisible
select r.rolname as role, d.datname as base, pg_get_userbyid(d.datdba) as proprietaire
from pg_roles r cross join pg_database d
where r.rolname = 'marquez' and d.datname = 'marquez';
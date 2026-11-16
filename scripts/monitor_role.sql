-- scripts/monitor_role.sql : rôle « monitor » de postgres_exporter (sprint 3), sur pg16 ET sur pg13.
-- Idempotent. Mot de passe lu dans MONITOR_PASSWORD (make obs-roles la remplit pour chaque serveur).
\set ON_ERROR_STOP on
\getenv monitor_pw MONITOR_PASSWORD
\if :{?monitor_pw}
\else
  do $$ begin raise exception 'MONITOR_PASSWORD absente : lance « make obs-roles »'; end $$;
\endif
select format('create role monitor login password %L', :'monitor_pw')
where not exists (select from pg_roles where rolname = 'monitor')
\gexec
alter role monitor with login password :'monitor_pw';
grant pg_monitor to monitor;          -- lecture des statistiques, aucun accès aux données
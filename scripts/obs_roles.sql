-- scripts/obs_roles.sql : rôles de Grafana et de Streamlit dans pg16 (sprint 3). Idempotent.
-- Les droits sont ceux du guide ; les mots de passe viennent de l'environnement, jamais du fichier.
\set ON_ERROR_STOP on
\getenv grafana_pw PG16_GRAFANA_PASSWORD
\getenv streamlit_pw PG16_STREAMLIT_PASSWORD
\if :{?grafana_pw}
\else
  do $$ begin raise exception 'PG16_GRAFANA_PASSWORD absente : lance « make obs-roles »'; end $$;
\endif
\if :{?streamlit_pw}
\else
  do $$ begin raise exception 'PG16_STREAMLIT_PASSWORD absente : lance « make obs-roles »'; end $$;
\endif

-- marts est créé par dbt à son premier build, donc APRÈS ce script sur une installation neuve :
-- on le crée d'avance pour pouvoir y donner les droits (dbt le réutilise tel quel)
create schema if not exists marts;

-- Grafana : lecture seule sur obs et marts
select format('create role grafana_ro login password %L', :'grafana_pw')
where not exists (select from pg_roles where rolname = 'grafana_ro')
\gexec
alter role grafana_ro with login password :'grafana_pw';
grant connect on database warehouse to grafana_ro;
grant usage on schema obs, marts to grafana_ro;
grant select on all tables in schema obs, marts to grafana_ro;
alter default privileges in schema obs grant select on tables to grafana_ro;  -- tables futures aussi
-- dbt recrée ses tables à chaque build (marts.dim_date…) : sans ce défaut, grafana_ro perdait l'accès
alter default privileges in schema marts grant select on tables to grafana_ro;

-- Streamlit : lit obs, dépose des demandes, gère les crons
select format('create role streamlit_app login password %L', :'streamlit_pw')
where not exists (select from pg_roles where rolname = 'streamlit_app')
\gexec
alter role streamlit_app with login password :'streamlit_pw';
grant connect on database warehouse to streamlit_app;
grant usage on schema obs to streamlit_app;
grant select on all tables in schema obs to streamlit_app;
grant insert on obs.pipeline_runs, obs.schedules to streamlit_app;
grant update on obs.schedules to streamlit_app;
grant usage on sequence obs.pipeline_runs_run_id_seq to streamlit_app;   -- nécessaire pour le bigserial

select rolname as role from pg_roles where rolname in ('grafana_ro', 'streamlit_app', 'monitor') order by 1;
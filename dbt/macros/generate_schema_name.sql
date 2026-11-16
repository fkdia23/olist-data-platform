    -- dbt/macros/generate_schema_name.sql
{% macro generate_schema_name(custom_schema_name, node) -%}   {# dbt l'appelle pour chaque modèle #}
    {%- if custom_schema_name is none -%}                      {# pas de +schema dans la config… #}
        {{ target.schema }}                                    {# … alors le schéma de la cible : analytics #}
    {%- else -%}
        {{ custom_schema_name | trim }}                        {# sinon le nom exact : staging, marts… #}
    {%- endif -%}
{%- endmacro %}
-- Creación de usuarios y base de datos de CALLFLOW.
-- Se ejecuta con un superusuario existente mediante scripts\crear_bd.ps1, que pasa:
--   :admin_user / :admin_password  -> superusuario propio de la empresa (mantenimiento, respaldos)
--   :app_user   / :app_password    -> usuario de la aplicación, SIN privilegios de superusuario
--   :db_name
-- Es idempotente: se puede volver a ejecutar sin romper nada.

\set ON_ERROR_STOP on

-- 1. Superusuario de la empresa (independiente del superusuario personal 'postgres')
SELECT format('CREATE ROLE %I LOGIN SUPERUSER CREATEDB CREATEROLE PASSWORD %L', :'admin_user', :'admin_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'admin_user') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN SUPERUSER PASSWORD %L', :'admin_user', :'admin_password') \gexec

-- 2. Usuario de la aplicación con permisos mínimos
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L CONNECTION LIMIT 50', :'app_user', :'app_password') \gexec

-- 3. Base de datos, propiedad del superusuario de la empresa
SELECT format('CREATE DATABASE %I OWNER %I ENCODING %L TEMPLATE template0', :'db_name', :'admin_user', 'UTF8')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name') \gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db_name') \gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'db_name', :'app_user') \gexec
SELECT format('ALTER DATABASE %I SET timezone TO %L', :'db_name', 'America/Guayaquil') \gexec

-- 4. Dentro de la base: extensiones y esquema
\connect :db_name

-- btree_gist: necesaria para impedir vigencias solapadas (salarios, horarios, comisiones, periodos)
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Solo el usuario de la aplicación crea objetos en el esquema public (las migraciones de Django).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('ALTER SCHEMA public OWNER TO %I', :'app_user') \gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'app_user') \gexec

\echo 'Base de datos y usuarios de CALLFLOW listos.'

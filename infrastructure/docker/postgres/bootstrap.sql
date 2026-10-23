-- Bootstrap de roles, schemas e privilégios do CCE (plano do M0, seção 7).
-- Executado como superusuário local, no database da aplicação, por 01-bootstrap.sh.
-- Reaplicável: cria só o que falta e reaplica atributos e grants. Senhas são definidas apenas
-- na criação do role; uma reaplicação nunca as altera.

\set ON_ERROR_STOP on

\getenv app_password CCE_APP_DB_PASSWORD
\getenv migrator_password CCE_MIGRATOR_DB_PASSWORD
\getenv db_name POSTGRES_DB

-- 1. Roles ausentes. As senhas são interpoladas pelo psql (:'var'), fora de blocos DO.
SELECT format('CREATE ROLE cce_migrator LOGIN PASSWORD %L', :'migrator_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cce_migrator')
\gexec

SELECT format('CREATE ROLE cce_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cce_app')
\gexec

SELECT 'CREATE ROLE cce_auth NOLOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cce_auth')
\gexec

-- 2. Atributos da matriz. Nenhum role do produto é superusuário nem ignora a RLS.
ALTER ROLE cce_migrator WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE cce_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE cce_auth WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;

-- cce_auth só é concedido a cce_migrator, que pode assumi-lo (SET ROLE) mas não herda seus privilégios.
GRANT cce_auth TO cce_migrator WITH INHERIT FALSE, SET TRUE;

-- 3. Schemas técnicos compartilhados (não há schema por Tenant).
CREATE SCHEMA IF NOT EXISTS cce AUTHORIZATION cce_migrator;
CREATE SCHEMA IF NOT EXISTS cce_private AUTHORIZATION cce_migrator;
ALTER SCHEMA cce OWNER TO cce_migrator;
ALTER SCHEMA cce_private OWNER TO cce_migrator;

-- 4. Nada de PUBLIC: sem CREATE em public e sem privilégios do database.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
REVOKE ALL ON SCHEMA cce, cce_private FROM PUBLIC;

-- 5. Concessões da matriz.
GRANT CONNECT ON DATABASE :"db_name" TO cce_app, cce_migrator;
GRANT USAGE ON SCHEMA cce TO cce_app, cce_auth;
GRANT USAGE ON SCHEMA cce_private TO cce_app;
-- cce_auth precisa criar a função de lookup em cce_private para ser dono dela; cce_app nunca.
GRANT USAGE, CREATE ON SCHEMA cce_private TO cce_auth;
REVOKE CREATE ON SCHEMA cce, cce_private FROM cce_app;

-- 6. Funções criadas por cce_migrator e cce_auth não nascem executáveis por PUBLIC.
-- Sem IN SCHEMA: um default por schema não consegue retirar o EXECUTE implícito de PUBLIC.
-- Cada função que cce_app deve executar precisa de GRANT EXECUTE explícito.
ALTER DEFAULT PRIVILEGES FOR ROLE cce_migrator REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE cce_auth REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- 7. Configuração fixa de sessão. Consultas de infraestrutura qualificam seus objetos.
ALTER ROLE cce_migrator SET search_path = pg_catalog, cce;
ALTER ROLE cce_app SET search_path = pg_catalog, cce;
ALTER ROLE cce_app SET statement_timeout = '5s';
ALTER ROLE cce_app SET lock_timeout = '2s';
ALTER ROLE cce_app SET idle_in_transaction_session_timeout = '10s';

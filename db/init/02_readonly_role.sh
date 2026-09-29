#!/bin/sh
# Creates the read-only role the api connects as. Runs once, after 01_chinook.sql
# has created and seeded the `chinook` database.
set -eu

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d chinook \
  -v ro_user="$CHINOOK_RO_USER" -v ro_pass="$CHINOOK_RO_PASSWORD" <<'SQL'
CREATE ROLE :"ro_user" LOGIN PASSWORD :'ro_pass'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

ALTER ROLE :"ro_user" SET default_transaction_read_only = on;
ALTER ROLE :"ro_user" SET statement_timeout = '5s';
ALTER ROLE :"ro_user" SET idle_in_transaction_session_timeout = '10s';

REVOKE ALL ON DATABASE chinook FROM PUBLIC;
GRANT CONNECT ON DATABASE chinook TO :"ro_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"ro_user";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"ro_user";
SQL

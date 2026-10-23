#!/bin/sh
# Provisiona roles e schemas do CCE. O entrypoint do PostgreSQL executa este script uma única vez,
# na criação do volume; ele também pode ser reaplicado à mão (não rotaciona senhas).
# As senhas chegam ao psql pelo ambiente (\getenv em bootstrap.sql), nunca por argumento.
set -eu

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${CCE_APP_DB_PASSWORD:?CCE_APP_DB_PASSWORD is required}"
: "${CCE_MIGRATOR_DB_PASSWORD:?CCE_MIGRATOR_DB_PASSWORD is required}"

psql --no-psqlrc -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --file /opt/cce/bootstrap.sql

#!/bin/bash
set -e

# Create test database alongside the production database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE nexstudiovisual_test OWNER $POSTGRES_USER;
EOSQL

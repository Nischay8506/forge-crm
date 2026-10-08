-- Runs once when the local Postgres container is first created.
-- forge_owner owns the schema and runs migrations; Alembic creates forge_app itself.
CREATE ROLE forge_owner LOGIN PASSWORD 'forge_owner' CREATEROLE;
CREATE DATABASE forge OWNER forge_owner;
CREATE DATABASE forge_test OWNER forge_owner;

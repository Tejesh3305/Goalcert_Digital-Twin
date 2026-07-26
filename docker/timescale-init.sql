-- timescale-init.sql — runs once, on first initdb of the postgres container.
--
-- Enabling the extension needs superuser, which the application's task role
-- deliberately does not have. Doing it here means a fresh local stack comes up
-- with the historian's full behaviour (hypertable, continuous aggregates,
-- compression, retention) rather than silently falling back to plain-Postgres
-- mode, where telemetry never compresses and never expires.
--
-- The equivalent step for an environment that already exists is:
--     python -m tools.historian_provision --extension
-- run as the master user. On RDS, connect as the master user; the extension is
-- on the RDS-supported list, so no parameter-group change is required.
--
-- Files in /docker-entrypoint-initdb.d are IGNORED when the data directory is
-- already initialised. Swapping the image on an existing postgres_data volume
-- therefore will not enable the extension — start fresh with
-- `docker compose down -v` if that is what you want.

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- The tables themselves are NOT created here. Schema creation is an explicit
-- step (`python -m tools.historian_provision`) because building a hypertable can
-- migrate existing rows, and a data operation must be a decision someone makes
-- rather than something several tasks race to perform during a rolling deploy.

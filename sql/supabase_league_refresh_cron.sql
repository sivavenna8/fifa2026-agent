-- REVIEW / LOCAL TEST ONLY. Applying this file creates a PAUSED dispatcher.
-- Prerequisites: pg_cron, pg_net, Vault, existing league/agent_runs schema.
-- In Vault UI create sportsintel_github_dispatch_token: fine-grained PAT for
-- sivavenna8/fifa2026-agent only, Actions: write, with an explicit expiry.
-- Never put a token in this file or print net.http_request_queue.headers.
-- pg_net queues decrypted tokens: trusted SQL logins can read them. Keep the
-- Data API disabled (or net unexposed); do NOT change global net permissions.
BEGIN;
CREATE SCHEMA IF NOT EXISTS sportsintel_scheduler;
REVOKE ALL ON SCHEMA sportsintel_scheduler FROM PUBLIC, anon, authenticated, service_role;

-- One bounded row: HTTP enqueue metadata, NOT proof of a successful refresh.
CREATE TABLE IF NOT EXISTS sportsintel_scheduler.league_refresh_dispatch (
    league_code text PRIMARY KEY CHECK (league_code = 'PL'),
    requested_at timestamptz NOT NULL,
    request_id bigint NOT NULL
);
REVOKE ALL ON sportsintel_scheduler.league_refresh_dispatch
    FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION sportsintel_scheduler.enqueue_league_refresh()
RETURNS bigint LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    token text;
    request_id bigint;
    last_request timestamptz;
    fixture_success timestamptz;
    pipeline_success timestamptz;
    current_time_utc timestamptz := pg_catalog.now();
    primary_tick boolean := extract(minute FROM pg_catalog.now() AT TIME ZONE 'UTC') = 7;
BEGIN
    -- Transaction-scoped lock works with transaction pooling and serializes
    -- duplicate enqueue calls, including a manual call at the Cron boundary.
    IF NOT pg_catalog.pg_try_advisory_xact_lock(20261009, 7301) THEN
        RETURN NULL;
    END IF;
    SELECT requested_at INTO last_request
      FROM sportsintel_scheduler.league_refresh_dispatch WHERE league_code = 'PL';
    -- Aligned 15-minute slots tolerate small Cron start-time jitter: measuring
    -- a strict elapsed 15 minutes could accidentally skip the next recovery.
    IF floor(extract(epoch FROM (last_request - interval '7 minutes')) / 900)
       >= floor(extract(epoch FROM (current_time_utc - interval '7 minutes')) / 900) THEN
        RETURN NULL;
    END IF;
    SELECT max(completed_at::timestamptz) INTO fixture_success
      FROM public.agent_runs
      WHERE command = 'league-fixtures-refresh:PL' AND status = 'success';
    SELECT max(completed_at::timestamptz) INTO pipeline_success
      FROM public.agent_runs
      WHERE command = 'league-daily:PL' AND status = 'success';
    -- Hourly primary, plus recovery when either success is missing/stale.
    -- Do not label a 204/200 dispatch acknowledgement as data freshness.
    IF NOT primary_tick
       AND fixture_success > current_time_utc - interval '60 minutes'
       AND pipeline_success > current_time_utc - interval '60 minutes' THEN
        RETURN NULL;
    END IF;
    SELECT decrypted_secret INTO STRICT token
      FROM vault.decrypted_secrets WHERE name = 'sportsintel_github_dispatch_token';
    IF token IS NULL OR length(token) < 20 OR token ~ '[[:space:]]' THEN
        RAISE EXCEPTION 'GitHub dispatch authentication is not configured correctly';
    END IF;
    request_id := net.http_post(
        url := 'https://api.github.com/repos/sivavenna8/fifa2026-agent/actions/workflows/sportsintel-league.yml/dispatches',
        headers := jsonb_build_object(
            'Accept', 'application/vnd.github+json',
            'Content-Type', 'application/json',
            'User-Agent', 'SportsIntelAI-refresh-scheduler',
            'X-GitHub-Api-Version', '2022-11-28',
            'Authorization', 'Bearer ' || token),
        body := '{"ref":"main"}'::jsonb,
        timeout_milliseconds := 10000
    );
    INSERT INTO sportsintel_scheduler.league_refresh_dispatch
        (league_code, requested_at, request_id) VALUES ('PL', current_time_utc, request_id)
    ON CONFLICT (league_code) DO UPDATE
        SET requested_at = excluded.requested_at, request_id = excluded.request_id;
    RETURN request_id;
END;
$$;
REVOKE ALL ON FUNCTION sportsintel_scheduler.enqueue_league_refresh()
    FROM PUBLIC, anon, authenticated, service_role;

-- Create inactive directly; no temporary active job and no HTTP call in setup.
-- Reapplying intentionally re-pauses this job, leaving Telegram jobs untouched.
DO $$
DECLARE refresh_job bigint;
BEGIN
    SELECT jobid INTO refresh_job FROM cron.job
      WHERE jobname = 'sportsintel-pl-refresh-dispatch' AND username = current_user;
    IF refresh_job IS NULL THEN
        PERFORM cron.schedule_in_database(
            'sportsintel-pl-refresh-dispatch', '7,22,37,52 * * * *',
            'SELECT sportsintel_scheduler.enqueue_league_refresh();',
            current_database(), current_user, active := false);
    ELSE
        PERFORM cron.alter_job(refresh_job,
            schedule := '7,22,37,52 * * * *',
            command := 'SELECT sportsintel_scheduler.enqueue_league_refresh();',
            active := false);
    END IF;
END;
$$;
COMMIT;

-- REVIEW ONLY. Do not run until extensions, migrations, worker and secrets are
-- verified. This configuration creates/updates a PAUSED job; it never enables it.
-- Enable pg_cron, pg_net and Vault separately in the Supabase Dashboard.
-- Set Vault secrets through its UI (never paste real secrets into this file):
--   sportsintel_telegram_worker_url = https://<host>/internal/telegram/check
--   sportsintel_telegram_worker_secret = same value as TELEGRAM_PUBLISHER_SECRET
BEGIN;
CREATE SCHEMA IF NOT EXISTS sportsintel_scheduler;
REVOKE ALL ON SCHEMA sportsintel_scheduler FROM PUBLIC, anon, authenticated;

-- pg_net queues request headers, including Authorization. Restrict direct
-- access so application roles cannot read scheduler credentials.
REVOKE ALL ON net.http_request_queue FROM PUBLIC, anon, authenticated;
REVOKE ALL ON net._http_response FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION sportsintel_scheduler.enqueue_sportsintel_telegram()
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  worker_url text;
  worker_secret text;
BEGIN
  SELECT decrypted_secret INTO STRICT worker_url
    FROM vault.decrypted_secrets WHERE name = 'sportsintel_telegram_worker_url';
  SELECT decrypted_secret INTO STRICT worker_secret
    FROM vault.decrypted_secrets WHERE name = 'sportsintel_telegram_worker_secret';
  IF worker_url !~ '^https://[^/?#@]+/internal/telegram/check$'
     OR length(worker_secret) < 32 THEN
    RAISE EXCEPTION 'Telegram scheduler URL or authentication is invalid';
  END IF;
  RETURN net.http_post(
    url := worker_url,
    headers := jsonb_build_object('Content-Type', 'application/json',
                                 'Authorization', 'Bearer ' || worker_secret),
    body := '{"dry_run": false}'::jsonb,
    timeout_milliseconds := 45000
  );
END;
$$;
REVOKE ALL ON FUNCTION sportsintel_scheduler.enqueue_sportsintel_telegram() FROM PUBLIC, anon, authenticated;

SELECT cron.schedule('sportsintel-telegram-v2', '*/15 * * * *',
                     'SELECT sportsintel_scheduler.enqueue_sportsintel_telegram();');
UPDATE cron.job SET active = false WHERE jobname = 'sportsintel-telegram-v2';
COMMIT;

-- AFTER explicit operator approval, successful authenticated dry run, and a
-- reviewed manual integration test, enable through the Cron Dashboard.
-- Example activation (intentionally commented):
-- UPDATE cron.job SET active = true WHERE jobname = 'sportsintel-telegram-v2';
-- To pause immediately:
-- UPDATE cron.job SET active = false WHERE jobname = 'sportsintel-telegram-v2';

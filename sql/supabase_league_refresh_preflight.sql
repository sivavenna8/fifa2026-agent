-- Read-only diagnostics. Never select request headers, Vault values or bodies.
SELECT jobname, schedule, active, username
FROM cron.job WHERE jobname IN ('sportsintel-pl-refresh-dispatch', 'sportsintel-telegram-v2');

SELECT command, status, started_at, completed_at
FROM public.agent_runs
WHERE command IN ('league-daily:PL', 'league-fixtures-refresh:PL')
ORDER BY started_at DESC LIMIT 20;

-- After the configuration has been applied (still paused), inspect only status.
-- 204 for API 2022-11-28; newer API versions may return 200 + a run identifier.
-- Neither acknowledgement proves that league-daily completed successfully.
SELECT d.requested_at, d.request_id, r.status_code, r.timed_out,
       r.id IS NOT NULL AS response_available
FROM sportsintel_scheduler.league_refresh_dispatch d
LEFT JOIN net._http_response r ON r.id = d.request_id;

SELECT name FROM vault.secrets
WHERE name = 'sportsintel_github_dispatch_token';

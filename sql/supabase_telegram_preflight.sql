-- READ ONLY: run before choosing/enabling Cron in this Supabase project.
SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name IN ('pg_cron', 'pg_net', 'supabase_vault');
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('pg_cron', 'pg_net', 'supabase_vault');
SELECT to_regprocedure('net.http_post(text,jsonb,jsonb,jsonb,integer)') AS http_post,
       to_regclass('vault.decrypted_secrets') AS vault,
       to_regclass('cron.job') AS cron_jobs;
SELECT indexname FROM pg_indexes
WHERE schemaname='public' AND indexname='idx_telegram_publication_identity';

-- Explicit, idempotent migration. Review and apply before any V2 live send.
-- Works with Postgres and SQLite. No predictions or model data are changed.
BEGIN;
CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_publication_identity
ON agent_runs(command)
WHERE command LIKE 'PL:%:MORNING' OR command LIKE 'PL:%:RESULTS';

-- Preserve V1 sent markers and checkpointed partial plans during cutover.
-- A conflicting V2 identity aborts the transaction; investigate rather than
-- losing delivery history. Run once before using V2, not alongside live sends.
UPDATE agent_runs
SET command = replace(command, 'league-telegram:', '') || ':MORNING'
WHERE command LIKE 'league-telegram:PL:%';
COMMIT;

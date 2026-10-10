# Free Premier League refresh automation

This is a prepared, inactive replacement trigger. It uses the existing Supabase
Free project, pg_cron/pg_net/Vault and standard GitHub-hosted Actions runners in
the public `sivavenna8/fifa2026-agent` repository. No new service or paid plan is
required. The Python pipeline, model, official locking rules, evaluations and
both dashboards remain unchanged apart from whole-cycle status tracking.

## Target and permissions

- Workflow: `.github/workflows/sportsintel-league.yml` (API filename:
  `sportsintel-league.yml`), ref `main`.
- Endpoint: `POST https://api.github.com/repos/sivavenna8/fifa2026-agent/actions/workflows/sportsintel-league.yml/dispatches`.
- Body: `{"ref":"main"}`; no workflow inputs or Telegram commands.
- Fine-grained personal access token: select ONLY `fifa2026-agent`, grant
  repository **Actions: write**, retain automatic Metadata: read, leave other
  permissions unset, and choose a short explicit expiration with a renewal reminder.
- This permission is repository-scoped, NOT limited to one workflow or dispatch.
  It can perform other Actions operations; treat this as a security limitation.
- Store the credential only in Vault, under `sportsintel_github_dispatch_token`.
  Never store it in SQL, the repository, workflow inputs, query output or logs.
- Workflow `GITHUB_TOKEN` is separate and only needs `contents: read`.

## Frequency, recovery and duplicates

The paused Cron job checks at minutes **7, 22, 37 and 52** of every hour (UTC).
Minute 7 dispatches the hourly primary run. Other ticks dispatch only when either
the successful fixture marker or the whole-pipeline marker is missing or at least
60 minutes old. Attempts are limited to one per aligned 15-minute slot by a
transaction-level advisory lock and a single private state row. Slots begin at
minutes 7/22/37/52, tolerating small start-time jitter. Repeated calls in one slot
return NULL and do not queue HTTP requests.

Normal operation is roughly 24 dispatches/day. During a persistent failure the
maximum is roughly 96/day. Acknowledged dispatches are NOT refresh successes:
`league-fixtures-refresh:PL` tracks ingestion and `league-daily:PL` tracks the
complete fetch/evaluate/predict cycle. HTTP dispatch errors or unsuccessful Actions
runs leave success markers stale, so subsequent recovery ticks retry. No marker
is fabricated from a successful HTTP response.

The existing GitHub concurrency group permits one running refresh and, by default,
one pending refresh; `cancel-in-progress: false` protects the running pipeline.
Extra pending runs can replace earlier pending runs, which is acceptable for a
refresh that fetches current data. This protects triggers of this workflow, not
unrelated processes or ad-hoc CLI execution. All refreshes must use this workflow.
Sequential duplicate runs cannot replace locked/evaluated prediction records or
evaluate the same record twice. Provisional picks may legitimately refresh.

Hourly dispatch plus observed 7–10 minute runtimes leaves margin under the
90-minute freshness gate. The recovery check starts once either marker is 60
minutes old, within another 15 minutes. This is a best-effort demo, NOT a timing
guarantee: runner delays, outages, token expiry or a failed recovery can exceed
90 minutes. The Telegram worker must continue refusing stale-data publication.

## Setup after approval (not performed by this change)

1. Review the local diff and tests. Deploy the reviewed workflow/service changes
   only after approval. Keep the existing GitHub scheduled trigger as fallback.
2. Confirm the repository is still public and standard runners remain free;
   confirm the Supabase organization is Free. Do not upgrade either service or
   enable paid runners. Free Supabase allowances include 500 MB database and
   5 GB egress; watch database/agent/Cron history growth and free-project pausing.
3. Create the fine-grained token in GitHub, then add its value through the Supabase
   Vault UI with the exact secret name above. Do not paste it into SQL Editor.
   Check Actions permission, selected repository, owner authorization and expiry.
4. Read-only preflight: verify extensions and that Data API remains disabled or
   that `net` and `sportsintel_scheduler` are unexposed. Trust all database logins.
5. Review and apply `sql/supabase_league_refresh_cron.sql` as `postgres` only after
   explicit approval. It creates/updates ONLY `sportsintel-pl-refresh-dispatch`
   in a paused state, using `SECURITY INVOKER` and an empty search path.
   It does not invoke the dispatcher or modify the Telegram job.
6. Run `sql/supabase_league_refresh_preflight.sql` after setup. Its third query
   requires the newly created state table. Inspect metadata/status only; never
   read queue headers or decrypted Vault values.
7. With separate approval, perform one manual refresh-dispatch integration test
   while automatic Cron remains paused and Telegram publishing remains disabled.
   Check the pg_net status (204 for the pinned 2022-11-28 API), the resulting
   `workflow_dispatch` Actions run, and BOTH successful database markers.
   Check official predictions/evaluations before and after a duplicate run.
8. Only after successful testing and separate activation approval, enable the
   refresh Cron job and observe regular successes and a recovery attempt. The
   Telegram Cron job and publisher flags remain disabled independently.
9. After the replacement proves healthy, remove ONLY the `schedule:` block from
   `sportsintel-league.yml` in a separately reviewed cutover change. Keep
   `workflow_dispatch`, the concurrency group and the Python command unchanged.

## Credential trade-off and rollback

Vault encrypts the PAT at rest. pg_net temporarily writes its decrypted value to
the queued Authorization header. Supabase-managed PUBLIC grants mean database
logins can read that value; an expiring, repository-limited PAT reduces scope but
does NOT remove this exposure. Keep Data API disabled or keep `net` unexposed,
do not add public queue-reading RPCs/views, and restrict database connections.
This design requires accepting trusted-database-role visibility before activation.

No SQL in the new files changes global pg_net privileges. Do not re-run the older
`sql/supabase_telegram_cron.sql` as part of refresh setup: its pre-existing global
net REVOKEs are not part of this solution. The existing paused Telegram setup is
left intact.

To recover from credential expiry, replace the Vault value securely; the next
approved active recovery tick will retry. To roll back the trigger, pause ONLY
`sportsintel-pl-refresh-dispatch`; retain or restore the GitHub hourly schedule.
Do not drop extensions, alter shared net grants, change predictions or delete
publication history. Restrict diagnostics to request ID/status, never headers.

References:
- https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
- https://docs.github.com/en/rest/about-the-rest-api/api-versions
- https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
- https://supabase.com/pricing
- https://supabase.com/docs/guides/cron
- https://supabase.com/docs/guides/troubleshooting/database-roles-can-read-request-headers-queued-by-pg_net-ad6357

-- Add a `revoked_at` column to the `session` table so server-side sign-out
-- can soft-delete a session rather than hard-deleting the row.
--
-- Why soft-delete?
--   1. Audit retention: keeps the historical record of when a session was
--      revoked + by whom (the row still references user_id). Hard-delete
--      loses that signal.
--   2. Forensic queries: lets us answer "which sessions did this user
--      explicitly sign out from" without a separate revocation log.
--   3. Race-safety: a concurrent /validate call hitting an already-deleted
--      row would surface "session_revoked" today; with soft-delete it
--      surfaces the same reason via the revoked_at IS NOT NULL check, but
--      the row is still introspectable for debug + alerting.
--
-- Migration is purely additive (new nullable column, no data mutation, no
-- constraint changes), per the project rule that migrations never touch
-- existing rows.

ALTER TABLE "session"
  ADD COLUMN IF NOT EXISTS "revoked_at" timestamp with time zone NULL;

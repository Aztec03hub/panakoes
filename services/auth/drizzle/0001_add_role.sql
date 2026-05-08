-- Add RBAC role column to the user table.
--
-- Slice 1 RBAC primitives: every user carries a role of either `user`
-- (default) or `admin`. Token claims include this value so downstream
-- services can authorize on it without a per-request DB hit. Real role
-- assignment + role-management UI lands in slice 2; for now `admin` is
-- assignable only by direct SQL update.
--
-- The column is `text` with a CHECK constraint instead of a Postgres
-- ENUM type because changing ENUM members later requires destructive DDL,
-- whereas CHECK constraints can be edited cheaply with `ALTER TABLE`.

ALTER TABLE "user"
  ADD COLUMN IF NOT EXISTS "role" text NOT NULL DEFAULT 'user';

ALTER TABLE "user"
  DROP CONSTRAINT IF EXISTS "user_role_check";

ALTER TABLE "user"
  ADD CONSTRAINT "user_role_check" CHECK ("role" IN ('user', 'admin'));

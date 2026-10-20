-- Read-only: which public tables can the anon / authenticated API roles still reach?
-- Paste into the Supabase SQL Editor and Run. Changes nothing.
-- After 022_lockdown_public_roles.sql, both result sets should be empty.

-- 1. Tables where anon or authenticated hold any privilege.
SELECT table_name, grantee, string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated')
GROUP BY table_name, grantee
ORDER BY table_name, grantee;

-- 2. Tables with row level security switched off.
SELECT c.relname AS table_without_rls
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
ORDER BY c.relname;

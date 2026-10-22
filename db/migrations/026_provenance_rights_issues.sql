-- Where an item came from and how it was linked, and what a build dropped.
--
--   public.sources.license / rights_status   licence evidence per source and its review state
--   public.chunk_concepts.method / confidence how each concept link was chosen
--   public.ingest_issues                      what a build dropped or could not structure, and why
--
-- Idempotent. Run by local_ingest/push_index.py after 025 on every push.

-- 1. sources: licence evidence and review state, per source (never per row).
create table if not exists public.sources (
  source_id text primary key,
  name      text not null unique,
  family    text not null,
  kind      text not null check (kind in ('book', 'exam', 'notes'))
);
alter table public.sources add column if not exists license text;
-- `unreviewed` until someone decides; nothing in retrieval gates on it yet.
alter table public.sources add column if not exists rights_status text not null default 'unreviewed';
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'sources_rights_status_chk') then
    alter table public.sources add constraint sources_rights_status_chk
      check (rights_status in ('unreviewed', 'approved', 'restricted'));
  end if;
end $$;
alter table public.sources enable row level security;
revoke all on public.sources from anon, authenticated;

-- 2. how each concept link was chosen
alter table public.chunk_concepts add column if not exists method text;
alter table public.chunk_concepts add column if not exists confidence real;
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'chunk_concepts_method_chk') then
    alter table public.chunk_concepts add constraint chunk_concepts_method_chk
      check (method is null or method in ('heading', 'embedding', 'syllabus_text')) not valid;
  end if;
end $$;

-- 3. what a build dropped, and why
create table if not exists public.ingest_issues (
  id         bigserial primary key,
  noted_at   timestamptz not null default now(),
  kind       text not null,
  content_id text,
  source     text,
  detail     text
);
create index if not exists ix_ingest_issues_kind on public.ingest_issues (kind);
alter table public.ingest_issues enable row level security;
revoke all on public.ingest_issues from anon, authenticated;

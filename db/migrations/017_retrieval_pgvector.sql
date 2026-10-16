-- 017_retrieval_pgvector.sql — the retrieval corpus lives in Postgres + pgvector.
--
-- Run on the *retrieval* project (RETRIEVAL_DATABASE_URL), never the user-data one.
-- `make push` (local_ingest/push_index.py) applies it before every push; it is idempotent.
-- It also reconciles the hosted project's earlier hand-made schema (a copy of the old
-- SQLite index): integer flags become booleans, the meaningless `vec_row` (a row index
-- into a .npy file) is dropped, syllabus_topics gains a subject.
--
-- Embedding contract (see retrieval/embedder.py): vector(256) from minishlab/potion-base-8M,
-- L2-normalised. `embed_model` is stored beside every vector and a CHECK constraint refuses
-- any other model, so two embedding spaces can never share a column. Changing the model is
-- a new migration (new width + new CHECK), never an env var.

create extension if not exists vector;

-- What the corpus was built with; the agent checks it at startup (store.verify_embedding_contract).
create table if not exists public.retrieval_meta (
  key        text primary key,
  value      text,
  updated_at timestamptz default now()
);

-- ── chapters ─────────────────────────────────────────────────────────────────
create table if not exists public.chapters (
  chapter_id  text primary key,
  subject     text not null default 'Physics',
  title       text,
  volume      text,
  unit        text,
  jee_unit    text,
  in_syllabus boolean default true,
  ord         integer
);
alter table public.chapters add column if not exists subject text not null default 'Physics';

-- ── concepts ─────────────────────────────────────────────────────────────────
alter table public.concepts
  add column if not exists chapter_id  text,
  add column if not exists unit        text,
  add column if not exists definition  text,
  add column if not exists in_syllabus boolean default true,
  add column if not exists ord         integer,
  add column if not exists aliases     jsonb default '[]'::jsonb,
  add column if not exists source      text,
  add column if not exists embedding   vector(256),
  add column if not exists embed_model text,
  add column if not exists text_hash   text;
alter table public.concepts drop column if exists vec_row;
-- Names repeat across chapters and subjects ("Isomerism" in haloalkanes and in coordination
-- compounds); concept_id is the identity. Nothing upserts on the name.
alter table public.concepts drop constraint if exists concepts_concept_name_key;

create table if not exists public.concept_edges (
  concept_id text not null,
  requires   text not null,
  primary key (concept_id, requires)
);

-- ── content ──────────────────────────────────────────────────────────────────
alter table public.content
  add column if not exists chapter_id   text,
  add column if not exists unit         text,
  add column if not exists source_ref   text,
  add column if not exists license      text,
  add column if not exists heading      text,
  add column if not exists in_syllabus  boolean default true,
  add column if not exists has_solution boolean,
  add column if not exists is_question  boolean,
  add column if not exists fingerprint  text,
  add column if not exists token_len    integer,
  add column if not exists structured   jsonb,
  add column if not exists embedding    vector(256),
  add column if not exists embed_model  text,
  add column if not exists text_hash    text,
  add column if not exists synced_at    timestamptz default now();
alter table public.content drop column if exists vec_row;

-- Full text, maintained by Postgres. Weights mirror the old FTS5 bm25(4, 2, 1): heading
-- outranks body. Lexical half of hybrid retrieval and of syllabus-topic matching.
alter table public.content
  add column if not exists fts tsvector
  generated always as (
    setweight(to_tsvector('english', coalesce(heading, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(core_text, '')), 'B') ||
    setweight(to_tsvector('english', left(coalesce(solution, ''), 2000)), 'D')
  ) stored;

-- primary + secondary + syllabus-topic links (role: primary | secondary | syllabus)
create table if not exists public.chunk_concepts (
  content_id text not null,
  concept_id text not null,
  role       text not null default 'primary',
  primary key (content_id, concept_id)
);

-- ── syllabus topics ──────────────────────────────────────────────────────────
create table if not exists public.syllabus_topics (
  subject    text not null default 'Physics',
  unit       text,
  unit_ord   integer not null,
  topic      text,
  topic_ord  integer not null,
  concept_id text,
  chapter_id text,
  created    boolean,
  material   integer,
  primary key (subject, unit_ord, topic_ord)
);

-- ── reconcile older hand-made tables ─────────────────────────────────────────
do $$
begin
  if (select data_type from information_schema.columns
      where table_schema = 'public' and table_name = 'chapters' and column_name = 'in_syllabus') <> 'boolean' then
    alter table public.chapters alter column in_syllabus drop default;
    alter table public.chapters alter column in_syllabus type boolean
      using (in_syllabus::text in ('1', 't', 'true'));
    alter table public.chapters alter column in_syllabus set default true;
  end if;

  if not exists (select 1 from information_schema.columns
                 where table_schema = 'public' and table_name = 'syllabus_topics' and column_name = 'subject') then
    alter table public.syllabus_topics add column subject text not null default 'Physics';
    alter table public.syllabus_topics drop constraint if exists syllabus_topics_pkey;
    alter table public.syllabus_topics add primary key (subject, unit_ord, topic_ord);
  end if;

  if (select data_type from information_schema.columns
      where table_schema = 'public' and table_name = 'syllabus_topics' and column_name = 'created') <> 'boolean' then
    alter table public.syllabus_topics alter column created type boolean
      using (created::text in ('1', 't', 'true'));
  end if;

  -- The embedding contract, enforced by the database. NOT VALID so rows written before
  -- the contract existed do not block the migration; `make push` rewrites them and then
  -- validates the constraint.
  if not exists (select 1 from pg_constraint where conname = 'content_embed_model_chk') then
    alter table public.content add constraint content_embed_model_chk
      check (embedding is null or embed_model = 'minishlab/potion-base-8M') not valid;
  end if;
  if not exists (select 1 from pg_constraint where conname = 'concepts_embed_model_chk') then
    alter table public.concepts add constraint concepts_embed_model_chk
      check (embedding is null or embed_model = 'minishlab/potion-base-8M') not valid;
  end if;
end $$;

-- ── indexes ──────────────────────────────────────────────────────────────────
create index if not exists ix_content_concept   on public.content (primary_concept_id, content_type);
create index if not exists ix_content_chapter   on public.content (chapter_id, content_type);
create index if not exists ix_content_unit      on public.content (unit, content_type);
create index if not exists ix_content_subject   on public.content (subject, content_type);
create index if not exists ix_content_fp        on public.content (fingerprint);
create index if not exists ix_content_fts       on public.content using gin (fts);
create index if not exists ix_content_id_prefix on public.content (content_id text_pattern_ops);
create index if not exists ix_content_embedding on public.content
  using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64);
create index if not exists ix_concepts_chapter  on public.concepts (chapter_id);
create index if not exists ix_concept_edges_requires    on public.concept_edges (requires);
create index if not exists ix_chunk_concepts_concept    on public.chunk_concepts (concept_id, role);

-- HNSW returns at most ef_search rows (default 40); the resolver's corpus probe asks for 64.
do $$
begin
  execute format('alter database %I set hnsw.ef_search = 100', current_database());
exception when others then
  raise warning 'could not set hnsw.ef_search (%): nearest-neighbour probes return at most 40 rows', sqlerrm;
end $$;

-- ── functions ────────────────────────────────────────────────────────────────
-- Natural-language text -> OR-of-terms tsquery (plainto_tsquery ANDs, far too strict for a
-- question). The agent passes already-filtered content words; this only does the parsing.
create or replace function public.or_tsquery(q text) returns tsquery
language sql immutable
as $$ select nullif(replace(plainto_tsquery('english', coalesce(q, ''))::text, '&', '|'), '')::tsquery $$;

-- The match_* / hybrid_match_* RPCs from earlier hand-made setups are superseded: the agent
-- queries Postgres directly (retrieval/store.py). Dropping them removes a second, divergent
-- retrieval path.
do $$
declare f record;
begin
  for f in
    select p.oid::regprocedure as sig
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname in ('match_content', 'match_concepts', 'hybrid_match_content', 'hybrid_match_concepts')
  loop
    execute 'drop function ' || f.sig;
  end loop;
end $$;

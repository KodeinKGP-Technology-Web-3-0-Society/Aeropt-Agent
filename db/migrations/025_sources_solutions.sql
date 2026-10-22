-- Real tables for what used to be text columns on every content row.
--
--   sources    one row per book / exam paper / note set: who it is, what family, what kind.
--   solutions  one row per question that has a worked solution or a stored answer.
--
-- content keeps theory + questions (they share the vector and full-text indexes, so they
-- cannot be split without losing hybrid search). It gains source_id. Solutions live in the
-- private schema `knowledge_private`: the REST API cannot see it, only the agent's own database
-- login can. content.solution is gone; push_index copies every solution across and then drops it.
-- Idempotent; runs after 019, 023 and 024.

create schema if not exists knowledge_private;
revoke all on schema knowledge_private from public, anon, authenticated;
alter default privileges in schema knowledge_private revoke all on tables from public, anon, authenticated;

-- `solutions` began as a view (024), then a table in public: a view is dropped, a table is moved.
DO $$ BEGIN
  IF (SELECT relkind FROM pg_class WHERE oid = to_regclass('public.solutions')) = 'v' THEN
    DROP VIEW public.solutions;
  ELSIF to_regclass('public.solutions') IS NOT NULL AND to_regclass('knowledge_private.solutions') IS NULL THEN
    ALTER TABLE public.solutions SET SCHEMA knowledge_private;
  END IF;
END $$;

create table if not exists public.sources (
  source_id text primary key,
  name      text not null unique,
  family    text not null,
  kind      text not null check (kind in ('book', 'exam', 'notes'))
);

create table if not exists knowledge_private.solutions (
  question_id     text primary key references public.content (content_id) on delete cascade,
  kind            text not null check (kind in ('worked', 'answer')),
  worked_solution text,
  answer          jsonb,
  check (worked_solution is not null or answer is not null)
);

alter table public.content add column if not exists source_id text;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'content_source_fk') THEN
    alter table public.content add constraint content_source_fk
      foreign key (source_id) references public.sources (source_id) deferrable initially deferred not valid;
  END IF;
END $$;
create index if not exists ix_content_source on public.content (source_id);

alter table public.sources enable row level security;
revoke all on public.sources from anon, authenticated;
revoke all on all tables in schema knowledge_private from public, anon, authenticated;

-- questions: the same view as before, now with the source family.
drop view if exists public.questions;
create view public.questions with (security_invoker = on) as
select c.content_id                                         as id,
       c.subject,
       h.title                                              as chapter,
       k.concept_name                                       as concept,
       case c.content_type when 'PYQ' then 'past-year question'
                           when 'Practice Question' then 'practice'
                           when 'Solved Example' then 'solved example' end as kind,
       c.target_exam                                        as exam,
       c.question_type                                      as format,
       c.difficulty,
       s.family                                             as source_family,
       c.source_book                                        as source,
       c.source_ref                                         as reference,
       coalesce(c.structured ->> 'stem', c.core_text)       as stem,
       c.structured -> 'options'                            as options,
       sol.answer,
       (sol.question_id is not null)                        as has_solution,
       c.in_syllabus
from public.content c
left join public.chapters  h   on h.chapter_id = c.chapter_id
left join public.concepts  k   on k.concept_id = c.primary_concept_id
left join public.sources   s   on s.source_id  = c.source_id
left join knowledge_private.solutions sol on sol.question_id = c.content_id
where c.is_question;
revoke all on public.questions from anon, authenticated;

comment on table  public.sources   is 'One row per book, exam paper or note set. family groups them (ncert, hc-verma, jee-advanced-official, jee-challenger, ...).';
comment on table  knowledge_private.solutions is 'One row per question that has a worked solution and/or a stored answer. Questions without a row have neither.';
comment on column knowledge_private.solutions.kind is 'worked = full solution text; answer = the final answer only.';
comment on column public.content.source_id is 'The book / paper this item comes from (sources table).';

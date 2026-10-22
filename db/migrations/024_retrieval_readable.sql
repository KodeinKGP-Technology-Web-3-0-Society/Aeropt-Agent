-- Make the retrieval database readable: no dead columns, plain-English views, a description
-- on every column. Nothing the search path reads is moved or renamed, and no vector is touched.
-- Idempotent; local_ingest/push_index.py runs it after 019 and 023 on every push.

-- 1. Columns that hold nothing on any row (the spreadsheet-era layout; images, per-row formula
--    lists and review flags were never filled). The build no longer writes them.
alter table public.content drop column if exists row_id;
alter table public.content drop column if exists prerequisite_concepts;
alter table public.content drop column if exists latex_formulae_core;
alter table public.content drop column if exists latex_formulae_solution;
alter table public.content drop column if exists has_image_core;
alter table public.content drop column if exists has_image_solution;
alter table public.content drop column if exists has_diagram;
alter table public.content drop column if exists image_filename;
alter table public.content drop column if exists diagram_description_core;
alter table public.content drop column if exists diagram_description_solution;
alter table public.content drop column if exists notes;
alter table public.content drop column if exists reviewed;

-- 2. Readable views. security_invoker: they see exactly what the caller may see, and anon /
--    authenticated get nothing (022 revoked the tables; the revokes below cover the views).
-- questions and solutions are defined in 025 (they depend on the sources and solutions tables).

create or replace view public.theory with (security_invoker = on) as
select c.content_id                                         as id,
       c.subject,
       h.title                                              as chapter,
       k.concept_name                                       as concept,
       c.question_type                                      as kind,
       c.source_book                                        as source,
       c.heading,
       c.core_text                                          as text
from public.content c
left join public.chapters h on h.chapter_id = c.chapter_id
left join public.concepts k on k.concept_id = c.primary_concept_id
where not c.is_question;

create or replace view public.concept_map with (security_invoker = on) as
select k.concept_id,
       k.concept_name                                       as concept,
       k.subject,
       h.title                                              as chapter,
       k.source,
       (select count(*) from public.content c where c.primary_concept_id = k.concept_id)                       as items,
       (select count(*) from public.content c where c.primary_concept_id = k.concept_id and c.is_question)     as questions,
       (select array_agg(r.concept_name order by r.concept_name)
          from public.concept_edges e join public.concepts r on r.concept_id = e.requires
         where e.concept_id = k.concept_id)                 as requires
from public.concepts k
left join public.chapters h on h.chapter_id = k.chapter_id;

create or replace view public.chapter_summary with (security_invoker = on) as
select h.subject,
       h.title                                              as chapter,
       h.volume                                             as source_volume,
       (select count(*) from public.concepts k where k.chapter_id = h.chapter_id) as concepts,
       x.items, x.questions, x.with_solution
from public.chapters h
cross join lateral (
  select count(*)                                                   as items,
         count(*) filter (where c.is_question)                      as questions,
         count(*) filter (where c.is_question and c.has_solution)   as with_solution
  from public.content c where c.chapter_id = h.chapter_id) x
order by h.subject, h.ord;

revoke all on public.theory, public.concept_map, public.chapter_summary
  from anon, authenticated;

-- 3. Descriptions: Supabase Studio shows these next to every table and column.
comment on table  public.content       is 'Every retrievable item: theory passages, solved examples, practice and past-year questions. Read it through the views questions / theory (solutions are in the private schema knowledge_private).';
comment on table  public.concepts      is 'One row per teachable concept (a textbook section or syllabus topic), each in exactly one chapter. Carries the concept embedding.';
comment on table  public.chapters      is 'One row per JEE chapter (a JEE unit), per subject.';
comment on table  public.concept_edges is 'Prerequisites: concept_id needs requires first.';
comment on table  public.chunk_concepts is 'Which concepts an item belongs to: primary, secondary, or syllabus topic.';
comment on table  public.syllabus_topics is 'The JEE syllabus topic list, each mapped to a concept and chapter.';
comment on table  public.retrieval_meta is 'What the corpus was built with (embedding model, dimension, build time, counts).';

comment on column public.content.content_id    is 'Stable item id; the prefix names the source (ncert_, hcv_, pyq_, jeechallenger_, osc_, ref_).';
comment on column public.content.content_type  is 'Theory | Solved Example | Practice Question | PYQ.';
comment on column public.content.question_type is 'Format: single_correct, multiple_correct, integer, numeric, matching, comprehension, subjective, exercise, worked_example; theory, definition, summary for non-questions.';
comment on column public.content.subject       is 'Physics | Chemistry | Mathematics. No default; a build must say.';
comment on column public.content.chapter_id    is 'The chapter (JEE unit) this item sits in.';
comment on column public.content.chapter       is 'Copy of the chapter title, for fast reads. The chapters table is the source of truth.';
comment on column public.content.unit          is 'Copy of the JEE unit name, for fast reads.';
comment on column public.content.primary_concept_id is 'The one concept this item mainly teaches or tests; always in the same chapter.';
comment on column public.content.primary_concept is 'Copy of the concept id, kept for older callers.';
comment on column public.content.secondary_concepts is 'Other concepts it touches, comma separated. chunk_concepts is the source of truth.';
comment on column public.content.target_exam   is 'Jee main | Jee Advanced | Jee main and Advanced.';
comment on column public.content.difficulty    is '1 (easy) to 5 (hardest); exam papers are raised a level at query time.';
comment on column public.content.source_book   is 'Where it came from (book or exam paper).';
comment on column public.content.source_ref    is 'Exact place in the source: section, page or question number.';
comment on column public.content.core_text     is 'The passage or the full question text (stem and options). Answers never live here.';
comment on column public.content.structured    is 'Questions only: stem, figure, options, parts, qtype and answer, laid out for the practice screen and the grader.';
comment on column public.content.has_solution  is 'True when a worked solution exists.';
comment on column public.content.is_question   is 'True for everything except Theory.';
comment on column public.content.in_syllabus   is 'False for material outside the JEE syllabus; search skips it.';
comment on column public.content.fingerprint   is 'Hash of the question text; identical questions share it, so repeats can be refused.';
comment on column public.content.token_len     is 'Rough word count, used to budget prompts.';
comment on column public.content.embedding     is 'potion-base-8M vector (256 dims) over heading + text. Never mix models.';
comment on column public.content.fts           is 'Full-text index, maintained by Postgres.';

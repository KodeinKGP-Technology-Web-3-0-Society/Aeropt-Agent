-- Retrieval corpus integrity: the rules the build already intends, enforced by the database.
--
-- Nothing tied the retrieval tables together. Edges outlived the concepts they named,
-- concepts pointed at chapters no build produced, and the 'Physics' default filed any row
-- that forgot its subject under physics. Run by local_ingest/push_index.py after 019 on every
-- push (idempotent), or once by hand on the retrieval database.
--
-- Foreign keys are DEFERRABLE INITIALLY DEFERRED: a push deletes and rewrites these tables
-- inside one transaction, and only the state it commits has to be consistent. The push sets
-- constraints immediate before it commits or rolls back, so a dry run proves them too.
--
-- Every constraint is added NOT VALID: it binds new writes at once, but does not scan rows
-- written before it existed. push_index.finalize() validates them all once the build has
-- rewritten the corpus, and fails the push if any old row still breaks one.

-- 1. No silent subject. Existing rows all carry one; new rows must say which.
alter table public.chapters        alter column subject drop default;
alter table public.concepts        alter column subject drop default;
alter table public.content         alter column subject drop default;
alter table public.syllabus_topics alter column subject drop default;
alter table public.content         alter column subject set not null;  -- holds today: no null subjects

DO $$
DECLARE
  r record;
BEGIN
  -- 2. Foreign keys.
  FOR r IN SELECT * FROM (VALUES
    ('content',         'content_chapter_fk',          'chapter_id',         'chapters (chapter_id)', ''),
    ('content',         'content_primary_concept_fk',  'primary_concept_id', 'concepts (concept_id)', ''),
    ('concepts',        'concepts_chapter_fk',         'chapter_id',         'chapters (chapter_id)', ''),
    ('chunk_concepts',  'chunk_concepts_content_fk',   'content_id',         'content (content_id)',  ' on delete cascade'),
    ('chunk_concepts',  'chunk_concepts_concept_fk',   'concept_id',         'concepts (concept_id)', ' on delete cascade'),
    ('concept_edges',   'concept_edges_concept_fk',    'concept_id',         'concepts (concept_id)', ' on delete cascade'),
    ('concept_edges',   'concept_edges_requires_fk',   'requires',           'concepts (concept_id)', ' on delete cascade'),
    ('syllabus_topics', 'syllabus_topics_concept_fk',  'concept_id',         'concepts (concept_id)', ' on delete cascade'),
    ('syllabus_topics', 'syllabus_topics_chapter_fk',  'chapter_id',         'chapters (chapter_id)', ' on delete cascade')
  ) AS t(tbl, name, col, ref, action) LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = r.name) THEN
      EXECUTE format('alter table public.%I add constraint %I foreign key (%I) references public.%s%s'
                     ' deferrable initially deferred not valid', r.tbl, r.name, r.col, r.ref, r.action);
    END IF;
  END LOOP;

  -- 3. Controlled values. Spellings are the build's (local_ingest/corpus.py: TYPE_*, _QTYPE_CANON).
  FOR r IN SELECT * FROM (VALUES
    ('chapters', 'chapters_subject_chk', 'subject in (''Physics'', ''Chemistry'', ''Mathematics'')'),
    ('concepts', 'concepts_subject_chk', 'subject in (''Physics'', ''Chemistry'', ''Mathematics'')'),
    ('content',  'content_subject_chk',  'subject in (''Physics'', ''Chemistry'', ''Mathematics'')'),
    ('content',  'content_type_chk',
       'content_type in (''Theory'', ''Solved Example'', ''Practice Question'', ''PYQ'')'),
    ('content',  'content_question_type_chk',
       'question_type is null or question_type in (''theory'', ''definition'', ''summary'', ''worked_example'','
       ' ''exercise'', ''conceptual'', ''numeric'', ''integer'', ''single_correct'', ''multiple_correct'','
       ' ''matching'', ''comprehension'', ''subjective'')'),
    ('content',  'content_target_exam_chk',
       'target_exam is null or target_exam in (''Jee main'', ''Jee Advanced'', ''Jee main and Advanced'')'),
    ('content',  'content_is_question_chk',
       'is_question = (content_type <> ''Theory'')'),
    ('concept_edges', 'concept_edges_no_self_chk', 'concept_id <> requires'),
    ('chunk_concepts', 'chunk_concepts_role_chk', 'role in (''primary'', ''secondary'', ''syllabus'')')
  ) AS t(tbl, name, expr) LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = r.name) THEN
      EXECUTE format('alter table public.%I add constraint %I check (%s) not valid', r.tbl, r.name, r.expr);
    END IF;
  END LOOP;
END $$;

-- 4. Spreadsheet-era columns nothing reads or writes.
alter table public.content drop column if exists collector_name;
alter table public.content drop column if exists reviewer_name;

-- 5. 022 revoked anon/authenticated outright; these policies only invited them back.
drop policy if exists anon_read on public.content;
drop policy if exists anon_read on public.concepts;
drop policy if exists anon_read on public.chapters;
drop policy if exists anon_read on public.concept_edges;
drop policy if exists anon_read on public.syllabus_topics;

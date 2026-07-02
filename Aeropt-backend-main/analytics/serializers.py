"""
analytics/serializers.py
========================
Serializers for the analytics app.

PLANNED SERIALIZERS
-------------------

ConceptProficiencySerializer
  - Fields: concept_name, chapter, score, attempts, correct_attempts, last_attempted, trend
  - Used by GET /analytics/concepts/ and injected into LLM system prompt

DailySnapshotSerializer
  - Fields: date, chapter, avg_score
  - Used to build the "Proficiency Over Time" line chart on Progress page

StudyStreakSerializer
  - Fields: current_streak, longest_streak, last_study_date, dots (7-element bool list)
  - dots computed property: last 7 days with activity = True

PracticeAttemptSerializer  (input)
  - Fields: concept_name, chapter, question_id, question_type, difficulty,
            selected_answer, is_correct, time_taken_secs, mistake_type
  - mistake_type required only when is_correct=False

DashboardSerializer
  - Fields: overall_proficiency, total_concepts_attempted, weak_concepts,
            session_streak (nested StudyStreakSerializer), chapters
  - chapters: list of ChapterHealthSerializer

ChapterHealthSerializer
  - Fields: name, rollup_score, concepts (list of ConceptProficiencySerializer)

ProgressSerializer
  - Fields: total_attempts, overall_accuracy, most_improved,
            proficiency_over_time, mistake_breakdown, concept_breakdown
  - concept_breakdown: list of ConceptBreakdownSerializer

ConceptBreakdownSerializer
  - Fields: concept, chapter, score, attempts, accuracy, last_attempted, trend_data
  - trend_data: list of last 5 daily scores (for the sparkline on Progress page)

WeeklyImprovementSerializer
  - Fields: concept_name, score_7d_ago, score_now, delta
"""

# Serializers will be implemented here.

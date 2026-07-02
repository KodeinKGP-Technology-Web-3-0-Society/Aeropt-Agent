"""
APP: analytics
==============
The data backbone for the Dashboard and Progress pages.

This app computes and stores all learning metrics derived from chat sessions
(chatbot app) and practice attempts. It is the source of truth for every
number and chart shown in the UI.

  Dashboard page (from Figma):
    - Overall Proficiency %         e.g. "64% across 18 attempted concepts"
    - Concepts Flagged Weak         e.g. "5 — Torque, Constraint Motion, Rolling with Slipping"
    - Session Streak                e.g. "4 days in a row" with 7 dot indicators
    - Physics Concept Health cards  per-concept: score bar, color, WEAK badge, attempt count
    - Chapter rollup score          e.g. "Laws of Motion 72%", "Rotational Motion 48%"

  Progress page (from Figma):
    - Total Questions Attempted     e.g. 147
    - Overall Accuracy              e.g. 71% correct on first attempt
    - Most Improved concept         e.g. "Work-Energy Theorem" (most delta this week)
    - Proficiency Over Time chart   line graph per chapter, last 14 days
    - Where Mistakes Come From      Conceptual 34%, Procedural 28%, Calculation 22%, Misinterpretation 16%
    - Concept Breakdown table       concept, chapter, score bar, attempts, accuracy, last attempted, trend sparkline

TABLES PLANNED
--------------
1. ConceptProficiency  (core score table — updated after every chat message or practice attempt)
   - user             : FK → users.User
   - concept_name     : CharField  (e.g. "Torque", "Newton's First Law")
   - chapter          : CharField  (e.g. "Laws of Motion", "Rotational Motion")
   - score            : PositiveSmallIntegerField (0–100)
                        Color thresholds: ≥70 → green, 50–69 → orange/yellow, <50 → red + WEAK badge
   - attempts         : PositiveIntegerField — total attempts across chat + practice
   - correct_attempts : PositiveIntegerField — used to compute accuracy %
   - last_attempted   : DateTimeField
   - trend            : CharField, choices = ['up', 'down', 'flat']
                        Computed by comparing score vs. score 3 days ago
   unique_together: (user, concept_name)

2. DailyProficiencySnapshot  (time-series for the line chart on Progress page)
   - user             : FK → users.User
   - chapter          : CharField
   - date             : DateField
   - avg_score        : FloatField — mean ConceptProficiency.score across concepts in chapter
   unique_together: (user, chapter, date)
   (Written by a Celery beat task at midnight UTC; one row per user+chapter+day)

3. StudyStreak
   - user             : OneToOneField → users.User
   - current_streak   : PositiveIntegerField — consecutive study days (Dashboard dot row)
   - longest_streak   : PositiveIntegerField
   - last_study_date  : DateField — updated on any chat message or practice attempt

4. PracticeAttempt  (records each Practice page question attempt)
   - user             : FK → users.User
   - concept_name     : CharField
   - chapter          : CharField
   - question_id      : CharField — stable identifier for the question
   - question_type    : CharField, choices = ['SCQ', 'MCQ', 'Numerical', 'PYQ']
   - difficulty       : PositiveSmallIntegerField (1–5, matching Practice page filter buttons)
   - selected_answer  : CharField
   - is_correct       : BooleanField
   - time_taken_secs  : PositiveIntegerField — timer from Practice page ("Time: 1m 23s")
   - mistake_type     : CharField, choices = ['Conceptual', 'Procedural', 'Calculation', 'Misinterpretation']
                        null when is_correct=True; drives the "Where mistakes come from" chart
   - attempted_at     : DateTimeField (auto_now_add)

5. WeeklyImprovement  (most-improved concept card on Progress page)
   - user             : FK → users.User
   - concept_name     : CharField
   - score_7d_ago     : PositiveSmallIntegerField
   - score_now        : PositiveSmallIntegerField
   - delta            : IntegerField — score_now − score_7d_ago
   - week_start       : DateField
   (Recomputed by Celery beat every Sunday; top record by delta per user = "Most Improved")

API ENDPOINTS NEEDED
--------------------
GET  /api/v1/analytics/dashboard/
  — Single payload for the entire Dashboard page:
    {
      "overall_proficiency": 64,
      "total_concepts_attempted": 18,
      "weak_concepts": [
        {"name": "Torque",            "score": 34},
        {"name": "Constraint Motion", "score": 34},
        {"name": "Rolling with Slipping", "score": 38}
      ],
      "session_streak": {
        "current": 4,
        "longest": 7,
        "dots": [true, true, true, true, false, false, false]
      },
      "chapters": [
        {
          "name": "Laws of Motion",
          "rollup_score": 72,
          "concepts": [
            {"name": "Newton's First Law", "score": 82, "attempts": 14, "is_weak": false},
            {"name": "Constraint Motion",  "score": 34, "attempts": 6,  "is_weak": true},
            ...
          ]
        },
        ...
      ]
    }
  Cache: Redis, TTL 5 min per user; invalidate on new chat message or practice attempt.

GET  /api/v1/analytics/progress/
  — Single payload for the entire Progress page:
    {
      "total_attempts": 147,
      "overall_accuracy": 71,
      "most_improved": {"concept": "Work-Energy Theorem", "delta": 22},
      "proficiency_over_time": [
        {"date": "2025-02-01", "Laws of Motion": 45, "Work Energy and Power": 38, "Rotational Motion": 22},
        ...
      ],
      "mistake_breakdown": {
        "Conceptual":        {"count": 14, "pct": 34},
        "Procedural":        {"count": 12, "pct": 28},
        "Calculation":       {"count": 9,  "pct": 22},
        "Misinterpretation": {"count": 7,  "pct": 16}
      },
      "concept_breakdown": [
        {
          "concept": "Torque",
          "chapter": "Rotational Motion",
          "score": 32,
          "attempts": 18,
          "accuracy": 33,
          "last_attempted": "2h ago",
          "trend_data": [28, 29, 30, 31, 32]
        },
        ...
      ]
    }
  Cache: Redis, TTL 15 min per user.

GET  /api/v1/analytics/concepts/
  — Full list of ConceptProficiency rows for the logged-in user.
  — Used by the chatbot app to inject concept scores into the LLM system prompt.
  — Query params: ?chapter=laws_of_motion   ?weak_only=true

POST /api/v1/analytics/practice-attempts/
  — Log a practice attempt (called by frontend Practice page after user submits).
  — Side effects:
      1. Upsert ConceptProficiency (recalculate score, accuracy, trend)
      2. Update StudyStreak
      3. Upsert DailyProficiencySnapshot for today
      4. Invalidate dashboard/progress cache for the user

GET  /api/v1/analytics/streak/
  — Returns { "current": 4, "longest": 7 }

SIGNAL HANDLER (analytics/signals.py — not a view)
  on_concept_mentioned(sender, user, concepts_data, ...)
    Receives signal fired by chatbot app after each assistant message.
    Updates ConceptProficiency rows + StudyStreak + invalidates cache.
"""

from django.db import models

# Models will be implemented here — see docstring above for schema.
from django.conf import settings

class ConceptProficiency(models.Model):
    TREND_UP = "up"
    TREND_DOWN = "down"
    TREND_FLAT = "flat"
    TREND_CHOICES = (
        (TREND_UP, "Up"),
        (TREND_DOWN, "Down"),
        (TREND_FLAT, "Flat"),
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="concept_proficiencies")
    concept_name = models.CharField(max_length=200)
    chapter = models.CharField(max_length=200)
    score = models.PositiveSmallIntegerField(default=0) # 0 to 100
    attempts = models.PositiveIntegerField(default=0)
    correct_attempts = models.PositiveIntegerField(default=0)
    last_attempted = models.DateTimeField(auto_now=True)
    trend = models.CharField(max_length=10, choices=TREND_CHOICES, default=TREND_FLAT)

    class Meta:
        ordering = ("concept_name",)
        unique_together = (("user", "concept_name"),)

    def __str__(self):
        return f"{self.user_id}:{self.concept_name}:{self.score}%"


class StudyStreak(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="study_streak")
    current_streak = models.PositiveIntegerField(default=0)
    longest_streak = models.PositiveIntegerField(default=0)
    last_study_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.user_id}: current={self.current_streak}, longest={self.longest_streak}"


class DailyProficiencySnapshot(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_proficiency_snapshots")
    chapter = models.CharField(max_length=200)
    date = models.DateField()
    avg_score = models.FloatField(default=0.0)

    class Meta:
        ordering = ("-date",)
        unique_together = (("user", "chapter", "date"),)


class PracticeAttempt(models.Model):
    QUESTION_TYPES = (
        ("SCQ", "Single Choice Question"),
        ("MCQ", "Multiple Choice Question"),
        ("Numerical", "Numerical"),
        ("PYQ", "Previous Year Question"),
    )
    MISTAKE_TYPES = (
        ("Conceptual", "Conceptual"),
        ("Procedural", "Procedural"),
        ("Calculation", "Calculation"),
        ("Misinterpretation", "Misinterpretation"),
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="practice_attempts")
    concept_name = models.CharField(max_length=200)
    chapter = models.CharField(max_length=200)
    question_id = models.CharField(max_length=200)
    question_type = models.CharField(max_length=20, choices=QUESTION_TYPES, default="SCQ")
    difficulty = models.PositiveSmallIntegerField(default=1) # 1 to 5
    selected_answer = models.CharField(max_length=200, blank=True)
    is_correct = models.BooleanField()
    time_taken_secs = models.PositiveIntegerField(default=0)
    mistake_type = models.CharField(max_length=50, choices=MISTAKE_TYPES, null=True, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-attempted_at",)


class WeeklyImprovement(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="weekly_improvements")
    concept_name = models.CharField(max_length=200)
    score_7d_ago = models.PositiveSmallIntegerField(default=0)
    score_now = models.PositiveSmallIntegerField(default=0)
    delta = models.IntegerField(default=0)
    week_start = models.DateField()

    class Meta:
        ordering = ("-week_start", "-delta")

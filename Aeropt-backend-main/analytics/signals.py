"""
analytics/signals.py
====================
Django signals that update analytics data when the chatbot app processes a message.

PLANNED SIGNALS
---------------

Signal: concept_mentioned
  - Defined here and fired by chatbot/views.py after every assistant LLM response
  - Carries: user, list of { concept_name, chapter, score_at_time, is_correct }

Handler: on_concept_mentioned(sender, user, concepts_data, **kwargs)
  - For each concept in concepts_data:
      1. Upsert ConceptProficiency (update score, increment attempts, recalculate trend)
      2. Upsert DailyProficiencySnapshot for today's date
  - Update StudyStreak (set last_study_date = today, increment current_streak if needed)
  - Invalidate Redis cache keys: dashboard:{user.id}, progress:{user.id}

Signal: practice_attempt_completed
  - Fired by analytics/views.py PracticeAttemptCreateView after saving an attempt
  - Carries: user, concept_name, chapter, is_correct, mistake_type

Handler: on_practice_attempt_completed(sender, user, concept_name, chapter, is_correct, mistake_type, **kwargs)
  - Upsert ConceptProficiency
  - Update StudyStreak
  - Upsert DailyProficiencySnapshot
  - Invalidate Redis cache
"""

# Signals will be implemented here.
from django.dispatch import receiver
from django.utils import timezone
from chatbot.signals import concept_mentioned
from analytics.models import ConceptProficiency, StudyStreak

def get_chapter_for_concept(concept_name):
    concept_map = {
        # Laws of Motion
        "Newton's First Law": "Laws of Motion",
        "Free Body Diagram": "Laws of Motion",
        "Constraint Motion": "Laws of Motion",
        "Pseudo Force": "Laws of Motion",
        "Friction": "Laws of Motion",
        "Newton's Third Law": "Laws of Motion",
        
        # Rotational Motion
        "Torque": "Rotational Motion",
        "Rolling with Slipping": "Rotational Motion",
        "Moment of Inertia": "Rotational Motion",
        "Angular Momentum": "Rotational Motion",
        "Fixed Axis Rotation": "Rotational Motion",
        
        # Work Energy & Power
        "Work-Energy Theorem": "Work Energy & Power",
        "Conservative Forces": "Work Energy & Power",
        "Power": "Work Energy & Power",
        "Potential Energy Curve": "Work Energy & Power",
    }
    return concept_map.get(concept_name, "Physics Fundamentals")

@receiver(concept_mentioned)
def on_concept_mentioned(sender, user, concepts_data, **kwargs):
    # concepts_data is a list of dicts: {concept_name, score_at_time, is_prerequisite}
    for item in concepts_data:
        concept_name = item.get("concept_name")
        score_at_time = item.get("score_at_time", 60)
        
        chapter = get_chapter_for_concept(concept_name)
        
        proficiency, created = ConceptProficiency.objects.get_or_create(
            user=user,
            concept_name=concept_name,
            defaults={"chapter": chapter, "score": score_at_time, "attempts": 1}
        )
        
        if not created:
            proficiency.attempts += 1
            if score_at_time > proficiency.score:
                proficiency.trend = ConceptProficiency.TREND_UP
            elif score_at_time < proficiency.score:
                proficiency.trend = ConceptProficiency.TREND_DOWN
            else:
                proficiency.trend = ConceptProficiency.TREND_FLAT
            
            proficiency.score = score_at_time
            proficiency.save()
            
    # Update StudyStreak
    today = timezone.now().date()
    streak, _ = StudyStreak.objects.get_or_create(user=user)
    if streak.last_study_date is None:
        streak.current_streak = 1
        streak.longest_streak = max(streak.longest_streak, 1)
    elif streak.last_study_date == today:
        pass # Already studied today
    elif streak.last_study_date == today - timezone.timedelta(days=1):
        streak.current_streak += 1
        streak.longest_streak = max(streak.longest_streak, streak.current_streak)
    else:
        streak.current_streak = 1
    
    streak.last_study_date = today
    streak.save()

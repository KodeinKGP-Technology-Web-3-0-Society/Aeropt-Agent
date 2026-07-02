import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import NotAuthenticated

from analytics.models import ConceptProficiency, StudyStreak

class _SessionMixin:
    def _get_actor_user(self, request):
        if request.user and request.user.is_authenticated:
            return request.user

        if not getattr(settings, "CHATBOT_AUTH_DISABLED", False):
            raise NotAuthenticated("Authentication credentials were not provided.")

        User = get_user_model()
        username = getattr(settings, "CHATBOT_DEV_USERNAME", "chatbot-dev")
        email = getattr(settings, "CHATBOT_DEV_EMAIL", "chatbot-dev@local.test")
        dev_user, _ = User.objects.get_or_create(
            username=username,
            defaults={"email": email, "is_active": True},
        )
        return dev_user


def seed_default_metrics(user):
    default_concepts = [
        # Laws of Motion
        ("Newton's First Law", "Laws of Motion", 82, 14, "flat"),
        ("Free Body Diagram", "Laws of Motion", 71, 11, "flat"),
        ("Constraint Motion", "Laws of Motion", 34, 6, "down"),
        ("Pseudo Force", "Laws of Motion", 52, 8, "flat"),
        ("Friction", "Laws of Motion", 68, 10, "flat"),
        ("Newton's Third Law", "Laws of Motion", 58, 9, "flat"),
        
        # Rotational Motion
        ("Torque", "Rotational Motion", 32, 18, "down"),
        ("Rolling with Slipping", "Rotational Motion", 38, 11, "up"),
        ("Moment of Inertia", "Rotational Motion", 58, 12, "flat"),
        ("Angular Momentum", "Rotational Motion", 45, 7, "flat"),
        ("Fixed Axis Rotation", "Rotational Motion", 61, 14, "flat"),
        
        # Work Energy & Power
        ("Work-Energy Theorem", "Work Energy & Power", 78, 24, "up"),
        ("Conservative Forces", "Work Energy & Power", 80, 15, "flat"),
        ("Power", "Work Energy & Power", 76, 12, "flat"),
        ("Potential Energy Curve", "Work Energy & Power", 65, 8, "flat"),
    ]
    
    for name, chapter, score, attempts, trend in default_concepts:
        ConceptProficiency.objects.get_or_create(
            user=user,
            concept_name=name,
            defaults={
                "chapter": chapter,
                "score": score,
                "attempts": attempts,
                "correct_attempts": int(attempts * (score / 100.0)),
                "trend": trend,
            }
        )
        
    # Seed StudyStreak
    StudyStreak.objects.get_or_create(
        user=user,
        defaults={
            "current_streak": 4,
            "longest_streak": 7,
            "last_study_date": timezone.now().date(),
        }
    )


class DashboardView(APIView, _SessionMixin):
    permission_classes = (AllowAny if getattr(settings, "CHATBOT_AUTH_DISABLED", False) else IsAuthenticated,)

    def get(self, request):
        user = self._get_actor_user(request)
        
        if not ConceptProficiency.objects.filter(user=user).exists():
            seed_default_metrics(user)
            
        proficiencies = ConceptProficiency.objects.filter(user=user)
        streak, _ = StudyStreak.objects.get_or_create(user=user)
        
        # 1. Calculate stats
        total_score = sum(p.score for p in proficiencies)
        total_concepts = proficiencies.count()
        overall_proficiency = int(total_score / total_concepts) if total_concepts > 0 else 0
        
        total_attempted = proficiencies.filter(attempts__gt=0).count()
        
        weak_concepts_queryset = proficiencies.filter(score__lt=50)
        weak_count = weak_concepts_queryset.count()
        weak_list = [p.concept_name for p in weak_concepts_queryset]
        
        # Build 7-day dots array (True if studied that day)
        today = timezone.now().date()
        dots = []
        for i in range(6, -1, -1):  # 7 days: 6 days ago → today
            day = today - datetime.timedelta(days=i)
            if streak.last_study_date and day <= streak.last_study_date:
                # Within the current streak window
                days_diff = (streak.last_study_date - day).days
                dots.append(days_diff < streak.current_streak)
            else:
                dots.append(False)

        stats = [
            {
                "id": "proficiency",
                "label": "Overall Proficiency",
                "value": f"{overall_proficiency}%",
                "subtext": f"across {total_attempted} attempted concepts"
            },
            {
                "id": "weak_concepts",
                "label": "Concepts Flagged Weak",
                "value": str(weak_count),
                "subtext": "need focused attention",
                "list": weak_list
            },
            {
                "id": "streak",
                "label": "Session Streak",
                "value": str(streak.current_streak),
                "subtext": "days in a row",
                "dots": dots
            }
        ]
        
        # 2. Group concepts by chapter
        chapters_names = ["Laws of Motion", "Work Energy & Power", "Rotational Motion"]
        chapters_data = []
        
        for ch_name in chapters_names:
            ch_proficiencies = proficiencies.filter(chapter=ch_name)
            ch_total_score = sum(p.score for p in ch_proficiencies)
            ch_count = ch_proficiencies.count()
            ch_overall_score = int(ch_total_score / ch_count) if ch_count > 0 else 0
            
            concepts_list = []
            for p in ch_proficiencies:
                tag = None
                if p.score < 50:
                    tag = "WEAK"
                elif p.score >= 75:
                    tag = "STRONG"

                accuracy = int((p.correct_attempts / p.attempts) * 100) if p.attempts > 0 else 0

                concepts_list.append({
                    "name": p.concept_name,
                    "score": p.score,
                    "attempts": p.attempts,
                    "accuracy": accuracy,
                    "tag": tag
                })
                
            chapters_data.append({
                "name": ch_name,
                "conceptCount": ch_count,
                "overallScore": ch_overall_score,
                "concepts": concepts_list
            })
            
        conceptHealth = {
            "title": "Physics — Concept Health",
            "tabs": chapters_names,
            "chapters": chapters_data
        }
        
        return Response({
            "stats": stats,
            "conceptHealth": conceptHealth
        })

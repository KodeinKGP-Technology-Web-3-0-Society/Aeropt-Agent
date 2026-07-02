from django.contrib import admin

from analytics.models import (
    ConceptProficiency,
    StudyStreak,
    DailyProficiencySnapshot,
    PracticeAttempt,
    WeeklyImprovement,
)


@admin.register(ConceptProficiency)
class ConceptProficiencyAdmin(admin.ModelAdmin):
    list_display = ("user", "concept_name", "chapter", "score", "attempts", "trend", "last_attempted")
    list_filter = ("chapter", "trend")
    search_fields = ("concept_name", "user__username")
    ordering = ("-score",)


@admin.register(StudyStreak)
class StudyStreakAdmin(admin.ModelAdmin):
    list_display = ("user", "current_streak", "longest_streak", "last_study_date")


@admin.register(DailyProficiencySnapshot)
class DailyProficiencySnapshotAdmin(admin.ModelAdmin):
    list_display = ("user", "chapter", "date", "avg_score")
    list_filter = ("chapter",)
    ordering = ("-date",)


@admin.register(PracticeAttempt)
class PracticeAttemptAdmin(admin.ModelAdmin):
    list_display = ("user", "concept_name", "chapter", "is_correct", "mistake_type", "attempted_at")
    list_filter = ("chapter", "is_correct", "mistake_type", "question_type")
    ordering = ("-attempted_at",)


@admin.register(WeeklyImprovement)
class WeeklyImprovementAdmin(admin.ModelAdmin):
    list_display = ("user", "concept_name", "score_7d_ago", "score_now", "delta", "week_start")
    ordering = ("-week_start", "-delta")

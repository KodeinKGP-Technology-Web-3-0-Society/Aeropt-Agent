import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatMessage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("role", models.CharField(choices=[("user", "User"), ("assistant", "Assistant")], max_length=20)),
                ("content", models.TextField()),
                ("structured_response", models.JSONField(blank=True, null=True)),
                ("tokens_used", models.PositiveIntegerField(default=0)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ("created_at",),
            },
        ),
        migrations.CreateModel(
            name="ChatSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(default="New Session", max_length=200)),
                ("subject", models.CharField(default="Physics", max_length=50)),
                ("active_chapters", models.JSONField(blank=True, default=list)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("system_prompt_used", models.TextField(blank=True, default="")),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-started_at",),
            },
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="session",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="messages",
                to="chatbot.chatsession",
            ),
        ),
        migrations.CreateModel(
            name="ConceptSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("concept_name", models.CharField(max_length=200)),
                ("score_at_time", models.PositiveSmallIntegerField(default=0)),
                ("trend", models.CharField(choices=[("up", "Up"), ("down", "Down"), ("flat", "Flat")], default="flat", max_length=10)),
                ("is_prerequisite", models.BooleanField(default=False)),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="concept_snapshots",
                        to="chatbot.chatmessage",
                    ),
                ),
            ],
            options={
                "ordering": ("id",),
            },
        ),
        migrations.AddConstraint(
            model_name="chatsession",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("user",),
                name="unique_active_chat_session_per_user",
            ),
        ),
    ]

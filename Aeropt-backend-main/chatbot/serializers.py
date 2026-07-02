from rest_framework import serializers
from django.utils import timezone

from chatbot.models import ChatMessage, ChatSession, ConceptSnapshot


class ConceptSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConceptSnapshot
        fields = ("concept_name", "score_at_time", "trend", "is_prerequisite")


class ChatMessageSerializer(serializers.ModelSerializer):
    concept_snapshots = ConceptSnapshotSerializer(many=True, read_only=True)

    class Meta:
        model = ChatMessage
        fields = (
            "id",
            "role",
            "content",
            "structured_response",
            "tokens_used",
            "latency_ms",
            "created_at",
            "concept_snapshots",
        )
        read_only_fields = fields


class ChatSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = ("id", "title", "subject", "active_chapters", "started_at", "ended_at", "is_active")
        read_only_fields = ("id", "started_at", "ended_at", "is_active")


class ChatSessionDetailSerializer(ChatSessionSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta(ChatSessionSerializer.Meta):
        fields = ChatSessionSerializer.Meta.fields + ("messages",)


class CreateChatSessionSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=50, required=False, default="Physics")
    active_chapters = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list,
    )

    def create(self, validated_data):
        request = self.context["request"]
        actor_user = self.context.get("actor_user", request.user)
        ChatSession.objects.filter(user=actor_user, is_active=True).update(
            is_active=False,
            ended_at=timezone.now(),
        )
        title = validated_data.get("title") or "New Session"
        return ChatSession.objects.create(
            user=actor_user,
            title=title,
            subject=validated_data.get("subject", "Physics"),
            active_chapters=validated_data.get("active_chapters", []),
            is_active=True,
        )


class SendMessageSerializer(serializers.Serializer):
    content = serializers.CharField(allow_blank=False, trim_whitespace=True)

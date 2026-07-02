import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Avg
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated, NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chatbot.models import ChatMessage, ChatSession, ConceptSnapshot
from chatbot.serializers import (
    ChatMessageSerializer,
    ChatSessionDetailSerializer,
    ChatSessionSerializer,
    CreateChatSessionSerializer,
    SendMessageSerializer,
)
from chatbot.services import ChatLLMService, ChatServiceError
from chatbot.signals import concept_mentioned

CHATBOT_PERMISSION = AllowAny if getattr(settings, "CHATBOT_AUTH_DISABLED", False) else IsAuthenticated


class DefaultPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


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

    def _get_session(self, user, session_id):
        try:
            return ChatSession.objects.get(id=session_id, user=user)
        except ChatSession.DoesNotExist as exc:
            raise NotFound("Session not found.") from exc


class ChatSessionListCreateView(APIView, _SessionMixin):
    permission_classes = (CHATBOT_PERMISSION,)

    def get(self, request):
        actor_user = self._get_actor_user(request)
        queryset = ChatSession.objects.filter(user=actor_user).order_by("-started_at")
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        data = ChatSessionSerializer(page, many=True).data
        return paginator.get_paginated_response(data)

    def post(self, request):
        actor_user = self._get_actor_user(request)
        serializer = CreateChatSessionSerializer(
            data=request.data,
            context={"request": request, "actor_user": actor_user},
        )
        serializer.is_valid(raise_exception=True)
        session = serializer.save()
        return Response(ChatSessionSerializer(session).data, status=status.HTTP_201_CREATED)


class ChatSessionDetailView(APIView, _SessionMixin):
    permission_classes = (CHATBOT_PERMISSION,)

    def get(self, request, session_id):
        actor_user = self._get_actor_user(request)
        session = self._get_session(actor_user, session_id)
        return Response(ChatSessionDetailSerializer(session).data)

    def delete(self, request, session_id):
        actor_user = self._get_actor_user(request)
        session = self._get_session(actor_user, session_id)
        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EndSessionView(APIView, _SessionMixin):
    permission_classes = (CHATBOT_PERMISSION,)

    def patch(self, request, session_id):
        actor_user = self._get_actor_user(request)
        session = self._get_session(actor_user, session_id)
        session.end()
        return Response(ChatSessionSerializer(session).data)


class ChatMessageListCreateView(APIView, _SessionMixin):
    permission_classes = (CHATBOT_PERMISSION,)

    def get(self, request, session_id):
        actor_user = self._get_actor_user(request)
        session = self._get_session(actor_user, session_id)
        queryset = session.messages.all().order_by("created_at")
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        data = ChatMessageSerializer(page, many=True).data
        return paginator.get_paginated_response(data)

    @transaction.atomic
    def post(self, request, session_id):
        actor_user = self._get_actor_user(request)
        session = self._get_session(actor_user, session_id)
        if not session.is_active:
            raise ValidationError({"session": "Session is not active."})

        input_serializer = SendMessageSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        content = input_serializer.validated_data["content"]

        ChatMessage.objects.create(
            session=session,
            role=ChatMessage.ROLE_USER,
            content=content,
        )
        if session.title == "New Session":
            session.title = content[:80]

        history = session.messages.all().order_by("created_at")
        llm_service = ChatLLMService()
        try:
            llm_result = llm_service.generate_reply(
                user=actor_user,
                session=session,
                history=history,
                user_text=content,
            )
        except ChatServiceError as exc:
            raise ValidationError({"llm": str(exc)}) from exc

        assistant_msg = ChatMessage.objects.create(
            session=session,
            role=ChatMessage.ROLE_ASSISTANT,
            content=llm_result["assistant_text"],
            structured_response=llm_result["structured"],
            tokens_used=llm_result["tokens_used"],
            latency_ms=llm_result["latency_ms"],
        )

        session.system_prompt_used = llm_result["system_prompt"]
        session.save(update_fields=("title", "system_prompt_used"))

        chain = llm_result["structured"].get("prerequisite_chain", []) or []
        chain_set = {item.get("concept", "") for item in chain if item.get("concept")}
        concepts = llm_result["structured"].get("concepts_mentioned", []) or []
        concept_rows = []
        for concept in concepts:
            score = 60
            trend = ConceptSnapshot.TREND_FLAT
            for item in chain:
                if item.get("concept") == concept:
                    score = int(item.get("score", 60))
                    break
            concept_rows.append(
                ConceptSnapshot(
                    message=assistant_msg,
                    concept_name=concept,
                    score_at_time=max(0, min(100, score)),
                    trend=trend,
                    is_prerequisite=concept in chain_set,
                )
            )
        if concept_rows:
            ConceptSnapshot.objects.bulk_create(concept_rows)

        concept_mentioned.send(
            sender=self.__class__,
            user=actor_user,
            session=session,
            message=assistant_msg,
            concepts_data=[
                {
                    "concept_name": row.concept_name,
                    "score_at_time": row.score_at_time,
                    "is_prerequisite": row.is_prerequisite,
                }
                for row in concept_rows
            ],
        )

        return Response(ChatMessageSerializer(assistant_msg).data, status=status.HTTP_201_CREATED)


class ChatMessageStreamView(APIView, _SessionMixin):
    permission_classes = (CHATBOT_PERMISSION,)

    def get(self, request, session_id, msg_id):
        actor_user = self._get_actor_user(request)
        session = self._get_session(actor_user, session_id)
        try:
            user_msg = ChatMessage.objects.get(
                id=msg_id,
                session=session,
                role=ChatMessage.ROLE_USER,
            )
        except ChatMessage.DoesNotExist as exc:
            raise NotFound("User message not found.") from exc

        assistant_msg = (
            ChatMessage.objects.filter(
                session=session,
                role=ChatMessage.ROLE_ASSISTANT,
                created_at__gte=user_msg.created_at,
            )
            .order_by("created_at")
            .first()
        )
        if not assistant_msg:
            raise NotFound("Assistant response not available yet.")

        def event_stream():
            for token in assistant_msg.content.split():
                yield f"data: {json.dumps({'token': token})}\n\n"
            payload = ChatMessageSerializer(assistant_msg).data
            yield f"data: {json.dumps({'done': True, 'message': payload})}\n\n"

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class ConceptSnapshotView(APIView, _SessionMixin):
    permission_classes = (CHATBOT_PERMISSION,)

    def get(self, request, session_id):
        actor_user = self._get_actor_user(request)
        session = self._get_session(actor_user, session_id)
        snapshots = ConceptSnapshot.objects.filter(message__session=session)

        concepts = (
            snapshots.values("concept_name")
            .annotate(
                avg_score=Avg("score_at_time"),
            )
            .order_by("concept_name")
        )

        data = []
        for row in concepts:
            latest = (
                snapshots.filter(concept_name=row["concept_name"])
                .order_by("-id")
                .first()
            )
            data.append(
                {
                    "concept_name": row["concept_name"],
                    "score_at_time": int(row["avg_score"] or 0),
                    "trend": latest.trend if latest else ConceptSnapshot.TREND_FLAT,
                    "is_prerequisite": bool(latest and latest.is_prerequisite),
                    "updated_at": latest.message.created_at if latest else timezone.now(),
                }
            )

        return Response(data)

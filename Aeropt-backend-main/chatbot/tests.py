from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import ChatMessage, ChatSession, ConceptSnapshot


class ChatbotApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="chat-user",
            password="chat-pass-123",
        )
        self.client.force_authenticate(self.user)

    def test_create_session(self):
        url = reverse("session-list-create")
        res = self.client.post(
            url,
            {"title": "Torque Discussion", "subject": "Physics", "active_chapters": ["rotational_motion"]},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ChatSession.objects.count(), 1)
        self.assertEqual(ChatSession.objects.first().title, "Torque Discussion")

    @patch("chatbot.views.ChatLLMService.generate_reply")
    def test_send_message_creates_assistant_and_snapshots(self, mock_generate_reply):
        session = ChatSession.objects.create(user=self.user, title="New Session")
        mock_generate_reply.return_value = {
            "assistant_text": "Torque is r cross F.",
            "structured": {
                "filling_gap": "Remember cross product basics.",
                "solution_steps": ["Identify pivot", "Compute moment arm", "Apply tau=rFsin(theta)"],
                "insight": "Direction matters.",
                "try_next": ["Change theta to 30 degrees."],
                "concepts_mentioned": ["Torque"],
                "prerequisite_chain": [{"concept": "Torque", "score": 34, "color": "orange"}],
            },
            "tokens_used": 12,
            "latency_ms": 9,
            "system_prompt": "prompt",
        }
        url = reverse("message-list-create", kwargs={"session_id": str(session.id)})
        res = self.client.post(url, {"content": "How to solve torque question?"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ChatMessage.objects.filter(session=session).count(), 2)
        self.assertEqual(ChatMessage.objects.filter(session=session, role=ChatMessage.ROLE_ASSISTANT).count(), 1)
        self.assertEqual(ConceptSnapshot.objects.filter(message__session=session).count(), 1)

    def test_session_concepts_endpoint(self):
        session = ChatSession.objects.create(user=self.user, title="S")
        assistant = ChatMessage.objects.create(
            session=session,
            role=ChatMessage.ROLE_ASSISTANT,
            content="a",
            structured_response={},
        )
        ConceptSnapshot.objects.create(
            message=assistant,
            concept_name="Torque",
            score_at_time=40,
            trend=ConceptSnapshot.TREND_FLAT,
            is_prerequisite=True,
        )
        url = reverse("session-concepts", kwargs={"session_id": str(session.id)})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["concept_name"], "Torque")

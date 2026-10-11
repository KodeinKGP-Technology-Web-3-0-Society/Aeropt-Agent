from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from api_server import app
from services.voice_service import (
    DEFAULT_SPEAKER,
    TUTOR_SPEAKERS,
    prepare_text_for_speech,
    synthesize_speech,
    transcribe_speech,
)


def test_prepare_text_for_speech_fractions_and_latex():
    raw = r"According to Newton's second law, $F_{net} = ma$, which implies $a = \frac{F_{net}}{m}$."
    spoken = prepare_text_for_speech(raw)
    assert r"\frac" not in spoken
    assert "over" in spoken
    assert "$" not in spoken
    assert "F net" in spoken


def test_prepare_text_for_speech_symbols_and_powers():
    raw = r"Centripetal acceleration is $a_c = \frac{v^2}{r}$ and angle is $\theta$."
    spoken = prepare_text_for_speech(raw)
    assert "v squared over r" in spoken
    assert "theta" in spoken
    assert r"\theta" not in spoken


def test_prepare_text_for_speech_markdown_cleaning():
    raw = "### Key Concept\n- **Step 1:** Calculate work done: `W = F * d`."
    spoken = prepare_text_for_speech(raw)
    assert "###" not in spoken
    assert "**" not in spoken
    assert "`" not in spoken
    assert "Step 1" in spoken


def test_api_speakers_endpoint():
    client = TestClient(app)
    res = client.get("/api/v1/voice/speakers")
    assert res.status_code == 200
    data = res.json()
    assert "speakers" in data
    assert data["default_speaker"] == DEFAULT_SPEAKER
    speaker_ids = [s["id"] for s in data["speakers"]]
    assert "aditya" in speaker_ids
    assert "ritu" in speaker_ids


def test_api_synthesize_endpoint():
    client = TestClient(app)
    # Empty text returns empty audio without error
    res_empty = client.post("/api/v1/voice/synthesize", json={"text": ""})
    assert res_empty.status_code == 200
    assert res_empty.json()["audio_base64"] == ""

    # Mock Sarvam client call for synthesis
    mock_response = MagicMock()
    mock_response.audios = ["dGVzdF9hdWRpb19kYXRh"]  # base64 "test_audio_data"

    with patch("services.voice_service.get_sarvam_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = mock_response
        mock_get_client.return_value = mock_client

        res = client.post(
            "/api/v1/voice/synthesize",
            json={"text": "What is torque?", "speaker": "ritu"},
        )
        assert res.status_code == 200
        payload = res.json()
        assert payload["audio_base64"] == "dGVzdF9hdWRpb19kYXRh"
        assert payload["speaker"] == "ritu"
        assert payload["format"] == "wav"


def test_api_transcribe_endpoint():
    client = TestClient(app)
    mock_response = MagicMock()
    mock_response.transcript = "Explain rotational inertia"
    mock_response.language_code = "en-IN"

    with patch("services.voice_service.get_sarvam_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.speech_to_text.transcribe.return_value = mock_response
        mock_get_client.return_value = mock_client

        files = {"file": ("doubt.webm", b"fake_audio_bytes", "audio/webm")}
        res = client.post("/api/v1/voice/transcribe", files=files)
        assert res.status_code == 200
        data = res.json()
        assert data["transcript"] == "Explain rotational inertia"
        assert data["language_code"] == "en-IN"

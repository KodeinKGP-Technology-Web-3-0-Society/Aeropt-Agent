"""
Aeropt — Voice Service (Sarvam AI: Saaras STT + Bulbul TTS)
============================================================

Provides voice capabilities for JEE Doubt-solving:
1. Speech-to-Text via Sarvam Saaras (v3/v4):
   Transcribes student spoken doubts from browser audio (WebM, WAV, OGG, etc.)
   into text, supporting Indian English, Hindi, and mixed technical terminology.

2. Text-to-Speech via Sarvam Bulbul (v3):
   Reads aloud explanations, hints, and tutor responses using natural Indian
   English/Hindi voices. Includes mathematical formula and LaTeX sanitization
   so equations are spoken naturally (e.g., "F net over m" instead of raw TeX).
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aeropt.voice")

# Available Bulbul v3 speakers suited for tutoring:
TUTOR_SPEAKERS = [
    {"id": "aditya", "name": "Aditya", "gender": "male", "description": "Clear & confident tutor voice"},
    {"id": "ritu", "name": "Ritu", "gender": "female", "description": "Engaging & supportive tutor voice"},
    {"id": "rohan", "name": "Rohan", "gender": "male", "description": "Warm & conversational voice"},
    {"id": "simran", "name": "Simran", "gender": "female", "description": "Crisp & articulated voice"},
    {"id": "priya", "name": "Priya", "gender": "female", "description": "Gentle & patient tutor voice"},
    {"id": "amit", "name": "Amit", "gender": "male", "description": "Steady academic voice"},
]

DEFAULT_SPEAKER = "aditya"
DEFAULT_TTS_MODEL = "bulbul:v3"
DEFAULT_STT_MODEL = "saaras:v3"

_sarvam_client = None


def get_sarvam_client():
    """Lazy initialization of the SarvamAI client."""
    global _sarvam_client
    if _sarvam_client is None:
        from sarvamai import SarvamAI

        key = os.getenv("SARVAM_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "SARVAM_API_KEY is not set in environment. "
                "Voice features require a valid Sarvam AI API key."
            )
        _sarvam_client = SarvamAI(api_subscription_key=key)
    return _sarvam_client


def prepare_text_for_speech(text: str, max_chars: int = 2500) -> str:
    """
    Sanitize markdown and LaTeX equations into human-readable spoken language.
    For example:
      `$F_{net} = ma$` -> "F net equals m a"
      `\\frac{a}{b}`    -> "a over b"
      `\\sqrt{x}`       -> "square root of x"
      `m/s^2`          -> "meters per second squared"
    """
    if not text:
        return ""

    # 1. Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # 2. Remove inline code backticks but keep text
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # 3. Fractions: \\frac{num}{den} -> "num over den"
    while r"\frac" in text:
        match = re.search(r"\\frac\{((?:[^{}]|\{[^{}]*\})+)\}\{((?:[^{}]|\{[^{}]*\})+)\}", text)
        if not match:
            break
        num, den = match.group(1), match.group(2)
        text = text[:match.start()] + f" {num} over {den} " + text[match.end():]

    # 4. Square roots: \\sqrt{x} -> "square root of x"
    text = re.sub(r"\\sqrt\{((?:[^{}]|\{[^{}]*\})+)\}", r" square root of \1 ", text)

    # 5. Powers and exponents
    text = re.sub(r"\^2\b", " squared", text)
    text = re.sub(r"\^3\b", " cubed", text)
    text = re.sub(r"\^\{([^{}]+)\}", r" to the power \1", text)
    text = re.sub(r"\^([a-zA-Z0-9])", r" to the power \1", text)

    # 6. Common Greek letters & mathematical symbols
    symbols = {
        r"\\theta": " theta ",
        r"\\alpha": " alpha ",
        r"\\beta": " beta ",
        r"\\gamma": " gamma ",
        r"\\omega": " omega ",
        r"\\Delta": " delta ",
        r"\\pi": " pi ",
        r"\\lambda": " lambda ",
        r"\\mu": " mu ",
        r"\\sigma": " sigma ",
        r"\\tau": " tau ",
        r"\\times": " times ",
        r"\\cdot": " times ",
        r"\\approx": " approximately ",
        r"\\neq": " not equal to ",
        r"\\le\b": " less than or equal to ",
        r"\\leq\b": " less than or equal to ",
        r"\\ge\b": " greater than or equal to ",
        r"\\geq\b": " greater than or equal to ",
        r"\\pm": " plus or minus ",
        r"\\mp": " minus or plus ",
        r"\\rightarrow": " gives ",
        r"\\infty": " infinity ",
        r"\\int": " integral of ",
    }
    for sym, rep in symbols.items():
        text = re.sub(sym, rep, text)

    # 7. Subscripts: F_{net} -> F net
    text = re.sub(r"_\{([^{}]+)\}", r" \1", text)
    text = re.sub(r"_([a-zA-Z0-9])", r" \1", text)

    # 8. Units and standard replacements
    text = re.sub(r"\bm/s\b", " meters per second ", text)
    text = re.sub(r"\bm/s\^2\b", " meters per second squared ", text)
    text = re.sub(r"\bkg\b", " kilograms ", text)
    text = re.sub(r"\bN\b(?=\s|[,.]|$)", " Newtons ", text)

    # 9. Clean remaining LaTeX commands like \\text{...}, \\mathbf{...}
    text = re.sub(r"\\[a-zA-Z]+\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+", " ", text)

    # 10. Strip math delimiters $ and $$
    text = text.replace("$$", " ").replace("$", " ")

    # 11. Clean Markdown formatting (headers, bold, italics, bullets)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\*\-\+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)  # Links

    # 12. Collapse extra whitespace
    text = re.sub(r"[ \t]+", " ", text).strip()

    # 13. Truncate gracefully at sentence boundary if too long
    if len(text) > max_chars:
        cutoff = text[:max_chars].rfind(".")
        if cutoff > max_chars // 2:
            text = text[: cutoff + 1]
        else:
            text = text[:max_chars].strip() + "..."

    return text


def normalize_audio_mime(mime: str, filename: str = "") -> str:
    """Normalize audio MIME type to match Sarvam AI exact accepted formats (strip ;codecs=opus etc)."""
    clean = (mime or "").split(";")[0].strip().lower()
    allowed = {
        "audio/mpeg", "audio/mp3", "audio/mpeg3", "audio/x-mpeg-3", "audio/x-mp3",
        "audio/wav", "audio/x-wav", "audio/wave", "audio/pcm_s16le", "audio/l16",
        "audio/raw", "application/octet-stream", "audio/aac", "audio/x-aac",
        "audio/aiff", "audio/x-aiff", "audio/ogg", "audio/opus", "audio/flac",
        "audio/x-flac", "audio/mp4", "audio/x-m4a", "audio/amr", "audio/x-ms-wma",
        "audio/webm", "video/webm"
    }
    if clean in allowed:
        return clean
    if "webm" in clean or filename.lower().endswith(".webm"):
        return "audio/webm"
    if "wav" in clean or filename.lower().endswith(".wav"):
        return "audio/wav"
    if "ogg" in clean or "opus" in clean or filename.lower().endswith(".ogg"):
        return "audio/ogg"
    if "mp4" in clean or "m4a" in clean or filename.lower().endswith((".mp4", ".m4a")):
        return "audio/mp4"
    if "mp3" in clean or filename.lower().endswith(".mp3"):
        return "audio/mp3"
    return "audio/webm"


def transcribe_speech(
    file_bytes: bytes,
    filename: str = "audio.webm",
    content_type: str = "audio/webm",
    language_code: Optional[str] = None,
    model: str = DEFAULT_STT_MODEL,
) -> Dict[str, Any]:
    """
    Transcribe spoken doubt audio using Sarvam Saaras STT.

    Args:
        file_bytes: Raw audio bytes from browser recording.
        filename: Original or default audio filename.
        content_type: MIME type (e.g. audio/webm, audio/wav, audio/ogg).
        language_code: Target or detected language (defaults to 'unknown' for auto-detection).
        model: Saaras model version (default 'saaras:v3').

    Returns:
        Dict with keys `transcript`, `language_code`.
    """
    client = get_sarvam_client()

    clean_content_type = normalize_audio_mime(content_type, filename)
    file_tuple = (filename, file_bytes, clean_content_type)
    target_lang = language_code or "unknown"

    try:
        response = client.speech_to_text.transcribe(
            file=file_tuple,
            model=model,
            language_code=target_lang,
        )
    except Exception as exc:
        logger.error("Sarvam STT failed: %s", exc)
        raise

    transcript = (getattr(response, "transcript", "") or "").strip()
    detected_lang = getattr(response, "language_code", target_lang)

    return {
        "transcript": transcript,
        "language_code": detected_lang,
    }


def synthesize_speech(
    text: str,
    speaker: str = DEFAULT_SPEAKER,
    language_code: str = "en-IN",
    model: str = DEFAULT_TTS_MODEL,
    output_audio_codec: str = "wav",
) -> Dict[str, Any]:
    """
    Convert tutor explanation or doubt answer to speech using Sarvam Bulbul TTS.

    Args:
        text: Explanation text (can include markdown and LaTeX formulas).
        speaker: Voice name (e.g. 'aditya', 'ritu', 'rohan', 'simran').
        language_code: Language code (default 'en-IN').
        model: Bulbul model version (default 'bulbul:v3').
        output_audio_codec: Audio output codec (default 'wav').

    Returns:
        Dict with keys `audio_base64`, `format`, `speaker`, `cleaned_text`.
    """
    client = get_sarvam_client()

    clean_text = prepare_text_for_speech(text)
    if not clean_text:
        return {
            "audio_base64": "",
            "format": output_audio_codec,
            "speaker": speaker,
            "cleaned_text": "",
        }

    valid_speakers = {s["id"] for s in TUTOR_SPEAKERS}
    selected_speaker = speaker if speaker in valid_speakers else DEFAULT_SPEAKER

    try:
        response = client.text_to_speech.convert(
            text=clean_text,
            language_code=language_code,
            speaker=selected_speaker,
            model=model,
            output_audio_codec=output_audio_codec,
        )
    except Exception as exc:
        logger.error("Sarvam TTS failed for text '%s...': %s", clean_text[:60], exc)
        raise

    audios = getattr(response, "audios", [])
    audio_base64 = audios[0] if audios else ""

    return {
        "audio_base64": audio_base64,
        "format": output_audio_codec,
        "speaker": selected_speaker,
        "cleaned_text": clean_text,
    }

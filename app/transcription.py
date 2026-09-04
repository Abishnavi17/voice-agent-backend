"""transcription.py - speech-to-text via faster-whisper (unchanged)."""

from __future__ import annotations

from functools import lru_cache

from faster_whisper import WhisperModel

_MODEL_SIZE = "base"


@lru_cache(maxsize=1)
def _get_model() -> WhisperModel:
    return WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")


def transcribe_audio(audio_path: str) -> dict[str, object]:
    model = _get_model()
    segments, info = model.transcribe(audio_path, beam_size=1)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return {
        "text": text,
        "language": info.language,
        "duration_seconds": round(info.duration, 2),
    }

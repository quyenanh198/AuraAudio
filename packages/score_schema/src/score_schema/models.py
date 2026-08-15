from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JobErrorCode(str, Enum):
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    MEDIA_TOO_LARGE = "MEDIA_TOO_LARGE"
    DECODE_FAILED = "DECODE_FAILED"
    NO_MUSIC_DETECTED = "NO_MUSIC_DETECTED"
    MODEL_FAILED = "MODEL_FAILED"
    EXPORT_FAILED = "EXPORT_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class NoteEvent:
    """A raw, performed-time note prediction from the inference stage."""

    pitch: int  # MIDI note number
    onset_s: float
    offset_s: float
    velocity: int  # 0-127
    confidence: float  # 0.0-1.0


def build_score(
    instrument: str,
    time_map: list[dict],
    measures: list[dict],
) -> dict:
    """Assemble the canonical schemaVersion-1 score JSON (ARCHITECTURE.md §5)."""
    return {
        "schemaVersion": 1,
        "timeMap": time_map,
        "parts": [
            {
                "instrument": instrument,
                "measures": measures,
            }
        ],
    }

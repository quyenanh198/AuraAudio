from __future__ import annotations

import jsonschema

_EVENT_SCHEMA = {
    "type": "object",
    "required": [
        "id", "pitch", "onsetSeconds", "offsetSeconds",
        "notatedOnset", "notatedDuration", "voice", "confidence", "locked",
    ],
    "properties": {
        "id": {"type": "string"},
        "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
        "onsetSeconds": {"type": "number", "minimum": 0},
        "offsetSeconds": {"type": "number", "minimum": 0},
        "notatedOnset": {"type": "string"},
        "notatedDuration": {"type": "string"},
        "voice": {"type": "integer", "minimum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "locked": {"type": "boolean"},
        "string": {"type": ["integer", "null"], "minimum": 0, "maximum": 5},
        "fret": {"type": ["integer", "null"], "minimum": 0, "maximum": 20},
        "hand": {"enum": ["left", "right", None]},
    },
    "additionalProperties": False,
}

_CONFIDENCE_SCHEMA = {
    "type": "object",
    "required": ["tempo", "meter", "key"],
    "properties": {
        "tempo": {"type": "number", "minimum": 0, "maximum": 1},
        "meter": {"type": "number", "minimum": 0, "maximum": 1},
        "key": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": False,
}

_SCORE_SCHEMA = {
    "type": "object",
    "required": ["schemaVersion", "timeMap", "parts"],
    "properties": {
        "schemaVersion": {"const": 4},
        "timeMap": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["beat", "seconds"],
                "properties": {
                    "beat": {"type": "number"},
                    "seconds": {"type": "number"},
                },
            },
        },
        "parts": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["instrument", "tempoBpm", "meter", "key", "confidence", "measures"],
                "properties": {
                    "instrument": {"enum": ["guitar", "piano"]},
                    "tempoBpm": {"type": "number", "exclusiveMinimum": 0},
                    "meter": {"enum": ["4/4", "3/4"]},
                    "key": {"type": "string", "pattern": "^[A-G](#|-)? (major|minor)$"},
                    "confidence": _CONFIDENCE_SCHEMA,
                    "measures": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["number", "events"],
                            "properties": {
                                "number": {"type": "integer", "minimum": 1},
                                "events": {"type": "array", "items": _EVENT_SCHEMA},
                            },
                        },
                    },
                },
            },
        },
    },
}


class ScoreValidationError(ValueError):
    pass


def validate_score(score: dict) -> None:
    try:
        jsonschema.validate(instance=score, schema=_SCORE_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ScoreValidationError(str(exc)) from exc

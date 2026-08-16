from pathlib import Path

from score_schema.models import build_score

from musicxml.export import score_json_to_musicxml


def _sample_score(meter: str = "4/4", key: str = "C major", tempo_bpm: float = 120.0):
    return build_score(
        instrument="guitar",
        tempo_bpm=tempo_bpm,
        meter=meter,
        key=key,
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": 0.5}],
        measures=[
            {
                "number": 1,
                "events": [
                    {
                        "id": "note_00", "pitch": 64, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                        "confidence": 0.9, "locked": False,
                    },
                    {
                        "id": "note_01", "pitch": 67, "onsetSeconds": 0.5, "offsetSeconds": 1.0,
                        "notatedOnset": "1/4", "notatedDuration": "1/4", "voice": 1,
                        "confidence": 0.85, "locked": False,
                    },
                ],
            }
        ],
    )


def test_score_json_to_musicxml_writes_a_file(tmp_path: Path):
    out_path = tmp_path / "out.musicxml"
    result_path = score_json_to_musicxml(_sample_score(), out_path)

    assert result_path == out_path
    assert out_path.exists()
    content = out_path.read_text()
    assert "<score-partwise" in content
    assert content.count("<note>") == 2


def test_score_json_to_musicxml_uses_detected_time_signature(tmp_path: Path):
    out_path = tmp_path / "out34.musicxml"
    score_json_to_musicxml(_sample_score(meter="3/4"), out_path)
    content = out_path.read_text()
    assert "<beats>3</beats>" in content
    assert "<beat-type>4</beat-type>" in content


def test_score_json_to_musicxml_uses_detected_tempo(tmp_path: Path):
    out_path = tmp_path / "out_tempo.musicxml"
    score_json_to_musicxml(_sample_score(tempo_bpm=90.0), out_path)
    content = out_path.read_text()
    assert "<per-minute>90</per-minute>" in content
    assert 'sound tempo="90"' in content


def test_score_json_to_musicxml_uses_detected_key(tmp_path: Path):
    out_path = tmp_path / "out_key.musicxml"
    score_json_to_musicxml(_sample_score(key="D major"), out_path)
    content = out_path.read_text()
    assert "<fifths>2</fifths>" in content
    assert "<mode>major</mode>" in content


def test_score_json_to_musicxml_spells_pitch_using_key_context(tmp_path: Path):
    # Pitch 66 (F#/Gb) is diatonic in D major as F#, but NOT diatonic in F
    # major (which uses flats) — so the same MIDI pitch should spell
    # differently depending on the score's detected key.
    score_sharp_key = _sample_score(key="D major")
    score_sharp_key["parts"][0]["measures"][0]["events"] = [{
        "id": "note_00", "pitch": 66, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False,
    }]
    out_path = tmp_path / "sharp.musicxml"
    score_json_to_musicxml(score_sharp_key, out_path)
    assert "<step>F</step>" in out_path.read_text()

    score_flat_key = _sample_score(key="F major")
    score_flat_key["parts"][0]["measures"][0]["events"] = [{
        "id": "note_00", "pitch": 66, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False,
    }]
    out_path2 = tmp_path / "flat.musicxml"
    score_json_to_musicxml(score_flat_key, out_path2)
    assert "<step>G</step>" in out_path2.read_text()

import json

import mido
import pytest
from aura_worker.eval.manifest import load_manifest, load_reference_events_from_midi


def _write_manifest(tmp_path, entries):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"entries": entries}))
    return manifest_path


def test_load_manifest_parses_valid_entries(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"fake")
    (tmp_path / "a.mid").write_bytes(b"fake")
    manifest_path = _write_manifest(
        tmp_path,
        [
            {
                "name": "real_riff_1",
                "audio_path": "a.wav",
                "reference_midi_path": "a.mid",
                "instrument": "guitar",
            }
        ],
    )

    entries = load_manifest(manifest_path)

    assert len(entries) == 1
    assert entries[0].name == "real_riff_1"
    assert entries[0].instrument == "guitar"
    assert entries[0].audio_path == tmp_path / "a.wav"
    assert entries[0].reference_midi_path == tmp_path / "a.mid"


def test_load_manifest_resolves_relative_paths_against_manifest_dir(tmp_path):
    sub = tmp_path / "clips"
    sub.mkdir()
    (sub / "b.wav").write_bytes(b"x")
    (sub / "b.mid").write_bytes(b"x")
    manifest_path = _write_manifest(
        tmp_path,
        [
            {
                "name": "b",
                "audio_path": "clips/b.wav",
                "reference_midi_path": "clips/b.mid",
                "instrument": "piano",
            }
        ],
    )

    entries = load_manifest(manifest_path)
    assert entries[0].audio_path == tmp_path / "clips" / "b.wav"


def test_load_manifest_accepts_absolute_paths(tmp_path):
    abs_wav = tmp_path / "abs.wav"
    abs_wav.write_bytes(b"x")
    manifest_path = _write_manifest(
        tmp_path,
        [
            {
                "name": "abs",
                "audio_path": str(abs_wav),
                "reference_midi_path": str(abs_wav),
                "instrument": "guitar",
            }
        ],
    )
    entries = load_manifest(manifest_path)
    assert entries[0].audio_path == abs_wav


def test_load_manifest_rejects_missing_entries_key(tmp_path):
    manifest_path = tmp_path / "bad.json"
    manifest_path.write_text(json.dumps({"not_entries": []}))
    with pytest.raises(ValueError):
        load_manifest(manifest_path)


def test_load_manifest_rejects_entry_missing_required_field(tmp_path):
    manifest_path = _write_manifest(
        tmp_path, [{"name": "x", "audio_path": "a.wav", "instrument": "guitar"}]
    )
    with pytest.raises(ValueError, match="reference_midi_path"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_invalid_instrument(tmp_path):
    manifest_path = _write_manifest(
        tmp_path,
        [
            {
                "name": "x",
                "audio_path": "a.wav",
                "reference_midi_path": "a.mid",
                "instrument": "violin",
            }
        ],
    )
    with pytest.raises(ValueError, match="instrument"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_entries_not_a_list(tmp_path):
    manifest_path = tmp_path / "bad.json"
    manifest_path.write_text(json.dumps({"entries": "not-a-list"}))
    with pytest.raises(ValueError):
        load_manifest(manifest_path)


def _write_test_midi(path, notes):
    """notes: list of (pitch, onset_beats, duration_beats) at 120bpm, 480 ticks/beat."""
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))

    events = []
    for pitch, onset_beats, duration_beats in notes:
        events.append((onset_beats, "on", pitch))
        events.append((onset_beats + duration_beats, "off", pitch))
    events.sort(key=lambda e: (e[0], e[1] == "on"))  # offs before ons at the same tick, if tied

    ticks_per_beat = mid.ticks_per_beat
    last_tick = 0
    for beat_time, kind, pitch in events:
        tick = round(beat_time * ticks_per_beat)
        delta = tick - last_tick
        last_tick = tick
        if kind == "on":
            track.append(mido.Message("note_on", note=pitch, velocity=100, time=delta))
        else:
            track.append(mido.Message("note_off", note=pitch, velocity=0, time=delta))

    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(path))


def test_load_reference_events_from_midi_extracts_correct_onset_offset(tmp_path):
    midi_path = tmp_path / "clip.mid"
    _write_test_midi(midi_path, [(60, 0.0, 1.0), (64, 1.0, 1.0)])

    events = load_reference_events_from_midi(midi_path)

    assert len(events) == 2
    assert events[0].pitch == 60
    assert abs(events[0].onset_s - 0.0) < 1e-6
    assert abs(events[0].offset_s - 0.5) < 1e-6  # 1 beat at 120bpm = 0.5s
    assert events[1].pitch == 64
    assert abs(events[1].onset_s - 0.5) < 1e-6


def test_load_reference_events_from_midi_handles_overlapping_same_pitch_notes(tmp_path):
    # Two separate note_on/note_off pairs for the same pitch, non-overlapping
    # in time -- must pair onsets to offsets in FIFO order, not get confused.
    midi_path = tmp_path / "repeat.mid"
    _write_test_midi(midi_path, [(60, 0.0, 0.5), (60, 1.0, 0.5)])

    events = load_reference_events_from_midi(midi_path)

    assert len(events) == 2
    assert [round(e.onset_s, 3) for e in events] == [0.0, 0.5]


def test_load_reference_events_from_midi_sorted_by_onset(tmp_path):
    midi_path = tmp_path / "chord.mid"
    _write_test_midi(midi_path, [(60, 0.0, 1.0), (64, 0.0, 1.0), (67, 0.0, 1.0)])

    events = load_reference_events_from_midi(midi_path)
    onsets = [e.onset_s for e in events]
    assert onsets == sorted(onsets)

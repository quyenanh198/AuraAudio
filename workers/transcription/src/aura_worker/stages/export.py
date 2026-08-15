from __future__ import annotations

from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from musicxml.export import score_json_to_musicxml
from musicxml.validate import MusicXmlValidationError, reopen_and_check
from score_schema.models import JobErrorCode, NoteEvent


def _write_midi(notes: list[NoteEvent], out_path) -> None:
    import mido

    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    ticks_per_beat = mid.ticks_per_beat  # default 480
    tempo_us = mido.bpm2tempo(120)
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))

    events = []
    for note_event in notes:
        events.append((note_event.onset_s, "on", note_event))
        events.append((note_event.offset_s, "off", note_event))
    events.sort(key=lambda e: (e[0], e[1] == "on"))

    seconds_per_tick = (tempo_us / 1_000_000) / ticks_per_beat
    last_tick = 0
    for seconds, kind, note_event in events:
        tick = int(seconds / seconds_per_tick)
        delta = max(tick - last_tick, 0)
        last_tick = tick
        msg_type = "note_on" if kind == "on" else "note_off"
        velocity = note_event.velocity if kind == "on" else 0
        track.append(mido.Message(msg_type, note=note_event.pitch, velocity=velocity, time=delta))

    mid.save(str(out_path))


def run(ctx: StageContext, notes: list[NoteEvent], score: dict) -> dict:
    from aura_api.models import Export

    midi_path = ctx.workdir / "output.mid"
    musicxml_path = ctx.workdir / "output.musicxml"

    _write_midi(notes, midi_path)
    score_json_to_musicxml(score, musicxml_path)

    expected_note_count = sum(
        len(measure["events"]) for measure in score["parts"][0]["measures"]
    )
    try:
        reopen_and_check(musicxml_path, expected_note_count=expected_note_count)
    except MusicXmlValidationError as exc:
        raise JobFailure(JobErrorCode.EXPORT_FAILED, str(exc)) from exc

    midi_key = f"jobs/{ctx.job.id}/exports/output.mid"
    musicxml_key = f"jobs/{ctx.job.id}/exports/output.musicxml"
    ctx.storage.put_bytes(midi_key, midi_path.read_bytes())
    ctx.storage.put_bytes(musicxml_key, musicxml_path.read_bytes())

    ctx.session.add(Export(
        project_id=ctx.job.project_id, job_id=ctx.job.id, revision=0,
        format="midi", status="succeeded", object_key=midi_key,
    ))
    ctx.session.add(Export(
        project_id=ctx.job.project_id, job_id=ctx.job.id, revision=0,
        format="musicxml", status="succeeded", object_key=musicxml_key,
    ))
    ctx.job.status = "succeeded"
    ctx.job.stage = "export"
    ctx.job.progress = 100
    ctx.session.commit()

    return {"midi_key": midi_key, "musicxml_key": musicxml_key}

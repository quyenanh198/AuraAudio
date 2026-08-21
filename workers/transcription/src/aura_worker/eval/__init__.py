"""Detection-quality benchmark harness (docs/superpowers/SESSION-HANDOFF.md's
"Detection-quality roadmap", item 0). Every later quality change to the
transcription pipeline is gated on this harness — no tuning without
measurement.

Run: `uv run --package aura-worker python -m aura_worker.eval.benchmark --out docs/benchmarks`
"""

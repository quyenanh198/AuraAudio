# Per-instrument basic-pitch threshold grid search — evidence

Rerunnable evidence for `aura_worker.instrument_thresholds`'s tuned
`onset_threshold`/`frame_threshold` values. Reproduce with:

```
uv run --package aura-worker python workers/transcription/scripts/tune_instrument_thresholds.py
```

See that script's module docstring for the full methodology and the
held-out-set caveat (there isn't one -- see
docs/superpowers/SESSION-HANDOFF.md's item 1 entry).

### guitar (onset sweep, frame=0.3): onset/frame threshold grid (post-ghost-filter onset F1)

| onset | frame | mean_f1 | min_f1 | arpeggio_a_m | melody_c_maj | melody_d_maj | melody_g_maj | sixteenth_ru | two_voice_ch | two_voice_ch |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.5 | 0.3 | 0.675 | 0.519 | 0.84 | 0.59 | 0.70 | 0.52 | 0.67 | 0.80 | 0.61 |
| 0.6 | 0.3 | 0.784 | 0.696 | 0.94 | 0.76 | 0.73 | 0.70 | 0.73 | 0.89 | 0.74 |
| 0.7 | 0.3 | 0.907 | 0.828 | 0.94 | 0.94 | 0.94 | 0.84 | 0.83 | 0.94 | 0.91 |
| 0.75 | 0.3 | 0.934 | 0.828 | 0.94 | 1.00 | 1.00 | 0.89 | 0.83 | 0.94 | 0.94 |
| 0.8 | 0.3 | 0.943 | 0.828 | 1.00 | 1.00 | 1.00 | 0.89 | 0.83 | 0.94 | 0.94 |
| 0.85 | 0.3 | 0.936 | 0.786 | 1.00 | 1.00 | 1.00 | 0.89 | 0.79 | 0.97 | 0.91 |
| 0.9 | 0.3 | 0.916 | 0.786 | 0.94 | 1.00 | 1.00 | 0.89 | 0.79 | 0.97 | 0.82 |
| 0.95 | 0.3 | 0.803 | 0.667 | 0.88 | 1.00 | 0.75 | 0.78 | 0.67 | 0.79 | 0.76 |

### guitar (frame sweep, onset=0.8): onset/frame threshold grid (post-ghost-filter onset F1)

| onset | frame | mean_f1 | min_f1 | arpeggio_a_m | melody_c_maj | melody_d_maj | melody_g_maj | sixteenth_ru | two_voice_ch | two_voice_ch |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.8 | 0.1 | 0.935 | 0.769 | 0.93 | 1.00 | 0.88 | 1.00 | 0.77 | 0.97 | 1.00 |
| 0.8 | 0.2 | 0.954 | 0.828 | 0.94 | 0.94 | 1.00 | 1.00 | 0.83 | 0.97 | 1.00 |
| 0.8 | 0.3 | 0.943 | 0.828 | 1.00 | 1.00 | 1.00 | 0.89 | 0.83 | 0.94 | 0.94 |
| 0.8 | 0.4 | 0.966 | 0.857 | 1.00 | 1.00 | 1.00 | 1.00 | 0.86 | 0.97 | 0.94 |
| 0.8 | 0.5 | 0.965 | 0.846 | 1.00 | 1.00 | 1.00 | 1.00 | 0.85 | 0.97 | 0.94 |
| 0.8 | 0.6 | 0.943 | 0.696 | 1.00 | 1.00 | 1.00 | 1.00 | 0.70 | 0.94 | 0.97 |

### piano (onset sweep, frame=0.3): onset/frame threshold grid (post-ghost-filter onset F1)

| onset | frame | mean_f1 | min_f1 | melody_c_maj | melody_d_min | sixteenth_ru | two_hand_cho | two_hand_wid |
|---|---|---|---|---|---|---|---|---|
| 0.5 | 0.3 | 0.554 | 0.375 | 0.40 | 0.38 | 0.71 | 0.56 | 0.73 |
| 0.6 | 0.3 | 0.595 | 0.429 | 0.44 | 0.43 | 0.79 | 0.59 | 0.73 |
| 0.7 | 0.3 | 0.724 | 0.609 | 0.61 | 0.67 | 0.89 | 0.73 | 0.73 |
| 0.75 | 0.3 | 0.788 | 0.727 | 0.80 | 0.76 | 0.89 | 0.76 | 0.73 |
| 0.8 | 0.3 | 0.807 | 0.727 | 0.89 | 0.80 | 0.85 | 0.77 | 0.73 |
| 0.85 | 0.3 | 0.790 | 0.689 | 0.89 | 0.80 | 0.85 | 0.69 | 0.73 |
| 0.9 | 0.3 | 0.774 | 0.610 | 0.89 | 0.80 | 0.85 | 0.61 | 0.73 |
| 0.95 | 0.3 | 0.636 | 0.522 | 0.67 | 0.60 | 0.80 | 0.59 | 0.52 |

### piano (frame sweep, coarse, onset=0.8): onset/frame threshold grid (post-ghost-filter onset F1)

| onset | frame | mean_f1 | min_f1 | melody_c_maj | melody_d_min | sixteenth_ru | two_hand_cho | two_hand_wid |
|---|---|---|---|---|---|---|---|---|
| 0.8 | 0.1 | 0.855 | 0.476 | 1.00 | 0.94 | 0.48 | 0.86 | 1.00 |
| 0.8 | 0.2 | 0.871 | 0.800 | 0.94 | 0.94 | 0.85 | 0.83 | 0.80 |
| 0.8 | 0.3 | 0.807 | 0.727 | 0.89 | 0.80 | 0.85 | 0.77 | 0.73 |
| 0.8 | 0.4 | 0.862 | 0.793 | 0.94 | 0.89 | 0.85 | 0.79 | 0.84 |
| 0.8 | 0.5 | 0.904 | 0.750 | 1.00 | 1.00 | 0.75 | 0.83 | 0.94 |
| 0.8 | 0.6 | 0.794 | 0.316 | 1.00 | 1.00 | 0.32 | 0.65 | 1.00 |

### piano (frame sweep, fine around 0.1, onset=0.8): onset/frame threshold grid (post-ghost-filter onset F1)

| onset | frame | mean_f1 | min_f1 | melody_c_maj | melody_d_min | sixteenth_ru | two_hand_cho | two_hand_wid |
|---|---|---|---|---|---|---|---|---|
| 0.8 | 0.05 | 0.614 | 0.421 | 0.67 | 0.77 | 0.42 | 0.67 | 0.55 |
| 0.8 | 0.08 | 0.614 | 0.421 | 0.67 | 0.77 | 0.42 | 0.67 | 0.55 |
| 0.8 | 0.1 | 0.855 | 0.476 | 1.00 | 0.94 | 0.48 | 0.86 | 1.00 |
| 0.8 | 0.12 | 0.878 | 0.640 | 0.94 | 0.94 | 0.64 | 0.87 | 1.00 |
| 0.8 | 0.15 | 0.870 | 0.800 | 0.94 | 0.94 | 0.81 | 0.85 | 0.80 |

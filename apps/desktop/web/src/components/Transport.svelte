<script lang="ts">
  import { playback, type PlaybackSourceKind } from "../lib/playback";

  interface Props {
    /** ScoreView owns the single seek path so it can move the audio AND the
     * OSMD cursor together in one lookup, whether the transport is playing
     * or paused (playback.seek() alone wouldn't touch the cursor). */
    onSeek: (t: number) => void;
  }

  let { onSeek }: Props = $props();

  // Dragging the scrubber needs to read back what the user is dragging to,
  // not the live position (which the rAF loop is also writing to every
  // frame while playing) — otherwise playback fights the drag.
  let dragging = $state(false);
  let dragValue = $state(0);

  let sliderMax = $derived(Math.max($playback.duration, 0.001));
  let sliderValue = $derived(dragging ? dragValue : $playback.position);

  function formatTime(seconds: number): string {
    const t = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
    const totalTenths = Math.floor(t * 10);
    const minutes = Math.floor(totalTenths / 600);
    const secs = Math.floor((totalTenths % 600) / 10);
    const tenths = totalTenths % 10;
    return `${minutes}:${String(secs).padStart(2, "0")}.${tenths}`;
  }

  function handleScrubberInput(event: Event): void {
    const value = Number((event.currentTarget as HTMLInputElement).value);
    dragValue = value;
    onSeek(value);
  }

  function togglePlay(): void {
    if ($playback.playing) {
      playback.pause();
    } else {
      playback.play();
    }
  }

  function skipToStart(): void {
    onSeek(0);
  }

  function selectSource(kind: PlaybackSourceKind): void {
    playback.setSource(kind);
  }

  function handleVolumeInput(event: Event): void {
    const value = Number((event.currentTarget as HTMLInputElement).value);
    playback.setVolume(value);
  }
</script>

<footer class="transport">
  <button type="button" class="icon-button" onclick={skipToStart} aria-label="Skip to start" title="Skip to start">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M6 5h2v14H6zM19 5v14l-11-7z" />
    </svg>
  </button>

  <button
    type="button"
    class="play-button"
    onclick={togglePlay}
    aria-label={$playback.playing ? "Pause" : "Play"}
    title={$playback.playing ? "Pause" : "Play"}
  >
    {#if $playback.playing}
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M7 5h4v14H7zM13 5h4v14h-4z" />
      </svg>
    {:else}
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M8 5v14l11-7z" />
      </svg>
    {/if}
  </button>

  <span class="time" aria-live="off">{formatTime($playback.position)} / {formatTime($playback.duration)}</span>

  <input
    class="scrubber"
    type="range"
    min="0"
    max={sliderMax}
    step="0.01"
    value={sliderValue}
    oninput={handleScrubberInput}
    onpointerdown={() => (dragging = true)}
    onpointerup={() => (dragging = false)}
    aria-label="Playback position"
  />

  <div class="source-toggle" role="group" aria-label="Playback source">
    <button
      type="button"
      class="source-button"
      class:active={$playback.source === "recording"}
      onclick={() => selectSource("recording")}
    >
      Recording
    </button>
    <button
      type="button"
      class="source-button"
      class:active={$playback.source === "synth"}
      onclick={() => selectSource("synth")}
    >
      Synth
    </button>
  </div>

  <div class="volume">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M3 9v6h4l5 5V4L7 9H3z" />
    </svg>
    <input
      class="volume-slider"
      type="range"
      min="0"
      max="1"
      step="0.01"
      value={$playback.volume}
      oninput={handleVolumeInput}
      aria-label="Volume"
    />
  </div>
</footer>

<style>
  .transport {
    flex: none;
    height: 64px;
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 0 20px;
    background: var(--panel);
    border-top: 1px solid var(--border);
    font: 13px/1.4 system-ui, sans-serif;
    color: var(--text);
  }

  .icon-button {
    width: 30px;
    height: 30px;
    border-radius: 8px;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
    display: flex;
    align-items: center;
    justify-content: center;
    flex: none;
    cursor: pointer;
  }

  .icon-button:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .play-button {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: var(--accent);
    color: #1e1d21;
    border: none;
    display: flex;
    align-items: center;
    justify-content: center;
    flex: none;
    cursor: pointer;
  }

  .play-button:hover {
    filter: brightness(1.08);
  }

  .time {
    flex: none;
    font-variant-numeric: tabular-nums;
    color: var(--dim);
    min-width: 92px;
  }

  .scrubber {
    flex: 1;
    min-width: 80px;
    accent-color: var(--accent);
  }

  .source-toggle {
    display: flex;
    flex: none;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }

  .source-button {
    background: transparent;
    border: none;
    color: var(--dim);
    padding: 6px 10px;
    font-size: 12px;
    cursor: pointer;
  }

  .source-button:not(:first-child) {
    border-left: 1px solid var(--border);
  }

  .source-button.active {
    background: var(--accent);
    color: #1e1d21;
  }

  .source-button:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  .volume {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: none;
    color: var(--dim);
  }

  .volume-slider {
    width: 80px;
    accent-color: var(--accent);
  }
</style>

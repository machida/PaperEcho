import { convertFileSrc } from "@tauri-apps/api/core";

export interface TrackState {
  volume: number; // 0..1
  muted: boolean;
  soloed: boolean;
}

export interface LoadResult {
  loaded: string[];
  failed: { name: string; error: string }[];
}

/**
 * Synchronised multi-stem player. All stems share one timeline so solo/mute/
 * volume changes apply live. Audio is loaded from local files via Tauri's
 * asset protocol (convertFileSrc).
 *
 * Stems are decoded into Web Audio buffers and played as BufferSources that all
 * start at the same AudioContext time — this is **sample-accurate**, which a
 * stem mixer needs: the parts sum back into the original song, so any timing
 * skew between them comb-filters/garbles the mix. (Streaming <audio> elements
 * each have independent clocks and drift, which sounds "ぐちゃぐちゃ" — don't.)
 *
 * To stay within webview memory on long songs, each decoded stem is downmixed
 * to mono and decimated to ~PREVIEW_RATE before we keep it. A 7-min song is
 * ~150 MB decoded stereo/44.1k × 7 stems ≈ 1 GB (which makes decodeAudioData
 * fail silently); mono/22k is ~4× smaller. Decode is sequential so peak memory
 * is ~one decode at a time. The AudioContext stays at the **hardware rate** —
 * forcing a non-native rate on an AudioContext (or OfflineAudioContext) makes
 * some WebKit builds render **silence**; the low-rate buffer just resamples on
 * playback, which is reliable.
 */
const PREVIEW_RATE = 22050; // Hz — target rate for the stored playback buffers

export class StemMixer {
  private ctx: AudioContext | null = null;
  private buffers = new Map<string, AudioBuffer>();
  private gains = new Map<string, GainNode>();
  private sources = new Map<string, AudioBufferSourceNode>();
  private state = new Map<string, TrackState>();

  private playing = false;
  private startedAt = 0; // ctx time when playback (re)started
  private offset = 0; // seconds into the timeline when paused
  private duration = 0;

  /** Decode every stem (downmixed to mono/22k to bound memory). Safe to call
   * once per analysis result. A single stem that fails to fetch/decode is
   * skipped (not fatal) so the rest still play. Returns the names that decoded
   * successfully. */
  async load(stems: Record<string, string>): Promise<LoadResult> {
    const ctx = this.ensureCtx();
    const loaded: string[] = [];
    const failed: { name: string; error: string }[] = [];
    for (const [name, path] of Object.entries(stems)) {
      try {
        const res = await fetch(convertFileSrc(path));
        if (!res.ok) throw new Error(`fetch ${res.status}`);
        const data = await res.arrayBuffer();
        const decoded = await ctx.decodeAudioData(data);
        const buf = monoDownsample(ctx, decoded, PREVIEW_RATE);
        this.buffers.set(name, buf);
        this.duration = Math.max(this.duration, buf.duration);
        if (!this.state.has(name)) {
          this.state.set(name, { volume: 0.9, muted: false, soloed: false });
        }
        loaded.push(name);
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        console.error(`StemMixer: failed to load stem "${name}"`, e);
        failed.push({ name, error });
      }
    }
    return { loaded, failed };
  }

  /** How many stems decoded successfully. */
  loadedCount(): number {
    return this.buffers.size;
  }

  /** Current AudioContext state ("running" | "suspended" | "closed" | "none"). */
  contextState(): string {
    return this.ctx?.state ?? "none";
  }

  /** Hardware sample rate of the AudioContext (0 if not created yet). */
  contextRate(): number {
    return this.ctx ? Math.round(this.ctx.sampleRate) : 0;
  }

  private ensureCtx(): AudioContext {
    if (!this.ctx) {
      // Always the hardware rate — forcing a custom sampleRate here makes some
      // WebKit builds output silence. Memory is bounded by shrinking the buffers
      // instead (monoDownsample), not the context.
      this.ctx = new AudioContext();
    }
    return this.ctx;
  }

  getDuration(): number {
    return this.duration;
  }

  getPosition(): number {
    if (!this.ctx) return this.offset;
    return this.playing
      ? Math.min(this.duration, this.offset + (this.ctx.currentTime - this.startedAt))
      : this.offset;
  }

  isPlaying(): boolean {
    return this.playing;
  }

  async play(): Promise<void> {
    const ctx = this.ensureCtx();
    if (ctx.state === "suspended") await ctx.resume();
    if (this.playing) return;
    if (this.offset >= this.duration) this.offset = 0;

    this.startedAt = ctx.currentTime;
    for (const [name, buf] of this.buffers) {
      const gain = ctx.createGain();
      gain.connect(ctx.destination);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(gain);
      src.start(0, this.offset);
      this.gains.set(name, gain);
      this.sources.set(name, src);
    }
    this.playing = true;
    this.applyGains();

    // Auto-stop bookkeeping when the longest stem ends.
    const remaining = (this.duration - this.offset) * 1000;
    window.setTimeout(() => {
      if (this.playing && this.getPosition() >= this.duration - 0.05) this.stop();
    }, remaining + 60);
  }

  pause(): void {
    if (!this.playing) return;
    this.offset = this.getPosition();
    this.teardownSources();
    this.playing = false;
  }

  stop(): void {
    this.teardownSources();
    this.playing = false;
    this.offset = 0;
  }

  seek(seconds: number): void {
    const wasPlaying = this.playing;
    if (wasPlaying) this.teardownSources();
    this.offset = Math.max(0, Math.min(this.duration, seconds));
    this.playing = false;
    if (wasPlaying) void this.play();
  }

  private teardownSources(): void {
    for (const src of this.sources.values()) {
      try {
        src.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear();
    this.gains.clear();
  }

  setVolume(name: string, volume: number): void {
    this.mutateState(name, (s) => (s.volume = volume));
  }

  toggleMute(name: string): void {
    this.mutateState(name, (s) => (s.muted = !s.muted));
  }

  toggleSolo(name: string): void {
    this.mutateState(name, (s) => (s.soloed = !s.soloed));
  }

  getState(name: string): TrackState {
    return this.state.get(name) ?? { volume: 0.9, muted: false, soloed: false };
  }

  private mutateState(name: string, fn: (s: TrackState) => void): void {
    const s = { ...this.getState(name) };
    fn(s);
    this.state.set(name, s);
    this.applyGains();
  }

  /** Recompute every stem's effective gain (handles solo precedence). */
  private applyGains(): void {
    const anySolo = [...this.state.values()].some((s) => s.soloed);
    for (const [name, gain] of this.gains) {
      const s = this.getState(name);
      const audible = anySolo ? s.soloed : !s.muted;
      gain.gain.value = audible ? s.volume : 0;
    }
  }

  dispose(): void {
    this.stop();
    void this.ctx?.close();
    this.ctx = null;
    this.buffers.clear();
    this.state.clear();
  }
}

/** Downmix to mono AND decimate to ~`targetRate` in one pass, into a new buffer.
 * ~4× smaller than stereo/44.1k so long songs fit in memory. Integer-factor
 * decimation (no anti-alias filter) is crude but fine for a mono reference
 * preview, and a plain createBuffer + loop works on every WebKit (no
 * OfflineAudioContext / custom-rate context, which can render silence). The
 * low-rate buffer resamples back up on playback in the hardware-rate context. */
function monoDownsample(
  ctx: BaseAudioContext,
  buf: AudioBuffer,
  targetRate: number,
): AudioBuffer {
  const factor = Math.max(1, Math.round(buf.sampleRate / targetRate));
  const outLen = Math.max(1, Math.floor(buf.length / factor));
  const out = ctx.createBuffer(1, outLen, buf.sampleRate / factor);
  const dst = out.getChannelData(0);
  const chans: Float32Array[] = [];
  for (let c = 0; c < buf.numberOfChannels; c++) chans.push(buf.getChannelData(c));
  for (let i = 0; i < outLen; i++) {
    const j = i * factor;
    let sum = 0;
    for (const ch of chans) sum += ch[j];
    dst[i] = sum / chans.length;
  }
  return out;
}

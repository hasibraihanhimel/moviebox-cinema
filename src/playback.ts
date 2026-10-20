import type { Stream } from "./types";
import { resolveApiUrl } from "./api";

export type PlaybackError = { code?: number; message: string; source: string };

export interface PlaybackEngine {
  load(stream: Stream, startAt?: number): Promise<void>;
  play(): Promise<void>;
  pause(): void;
  seek(position: number): void;
  setQuality(quality: string): Promise<void>;
  setSubtitle(_url: string | null): void;
  destroy(): void;
}

function sourceFor(stream: Stream): string {
  const resolved = resolveApiUrl(stream.playback_url || stream.url);
  return new URL(resolved, window.location.href).href;
}

export class NativeVideoEngine implements PlaybackEngine {
  constructor(private readonly video: HTMLVideoElement) {}

  load(stream: Stream, startAt = 0): Promise<void> {
    return new Promise((resolve, reject) => {
      const onReady = () => {
        this.video.removeEventListener("loadedmetadata", onReady);
        this.video.removeEventListener("error", onError);
        if (startAt > 0 && Number.isFinite(this.video.duration)) this.video.currentTime = Math.min(startAt, Math.max(0, this.video.duration - 4));
        resolve();
      };
      const onError = () => {
        this.video.removeEventListener("loadedmetadata", onReady);
        this.video.removeEventListener("error", onError);
        reject(new Error(this.video.error?.message || "Native media metadata could not be loaded."));
      };
      this.video.addEventListener("loadedmetadata", onReady, { once: true });
      this.video.addEventListener("error", onError, { once: true });
      this.video.src = sourceFor(stream);
      this.video.load();
    });
  }

  play(): Promise<void> { return this.video.play(); }
  pause(): void { this.video.pause(); }
  seek(position: number): void { this.video.currentTime = position; }
  setQuality(_quality: string): Promise<void> { return Promise.resolve(); }
  setSubtitle(_url: string | null): void {}
  destroy(): void { this.video.removeAttribute("src"); this.video.load(); }
}

export class DashPlaybackEngine implements PlaybackEngine {
  private player: any = null;
  constructor(private readonly video: HTMLVideoElement, private readonly onError: (error: PlaybackError) => void) {}

  async load(stream: Stream, startAt = 0): Promise<void> {
    const dashjs = await import("dashjs");
    return new Promise((resolve, reject) => {
      let settled = false;
      const finishError = (error: Error) => { if (!settled) { settled = true; reject(error); } };
      const player: any = dashjs.MediaPlayer().create();
      this.player = player;
      player.on(dashjs.MediaPlayer.events.ERROR, (event: unknown) => {
        const data = event as { error?: { message?: string; code?: number } };
        const error = { code: data.error?.code, message: data.error?.message || "DASH manifest or segment failed.", source: "dash" };
        this.onError(error);
        finishError(new Error(error.message));
      });
      player.on(dashjs.MediaPlayer.events.STREAM_INITIALIZED, () => {
        if (startAt > 0 && Number.isFinite(this.video.duration)) this.video.currentTime = Math.min(startAt, Math.max(0, this.video.duration - 4));
        if (!settled) { settled = true; resolve(); }
      });
      player.initialize(this.video, sourceFor(stream), false);
      player.updateSettings({ streaming: { errors: { recoverAttempts: { mediaErrorDecode: 2 } } } });
    });
  }

  play(): Promise<void> { return this.video.play(); }
  pause(): void { this.video.pause(); }
  seek(position: number): void { this.video.currentTime = position; }
  setQuality(quality: string): Promise<void> {
    const player = this.player;
    if (!player) return Promise.resolve();
    const value = Number.parseInt(quality, 10);
    const list = player.getBitrateInfoListFor("video") || [];
    const index = list.findIndex((item: { height?: number }) => item.height === value);
    if (index >= 0) {
      player.updateSettings({ streaming: { abr: { autoSwitchBitrate: { video: false } } } });
      player.setQualityFor("video", index);
    }
    return Promise.resolve();
  }
  setSubtitle(_url: string | null): void {}
  destroy(): void { this.player?.reset(); this.player = null; }
}

export function createPlaybackEngine(video: HTMLVideoElement, stream: Stream, onError: (error: PlaybackError) => void): PlaybackEngine {
  return stream.is_dash || stream.format?.toLowerCase() === "dash" || stream.url.toLowerCase().includes(".mpd") ? new DashPlaybackEngine(video, onError) : new NativeVideoEngine(video);
}

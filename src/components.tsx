import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ChevronDown, ChevronLeft, ChevronRight, Clock3, Expand, Film, Gauge, Headphones, LoaderCircle, Menu, Pause, PictureInPicture, Play, RotateCcw, Search, SkipBack, SkipForward, Sparkles, Star, Subtitles, Volume2, VolumeX, X } from "lucide-react";
import { getSuggestions, resolveApiUrl } from "./api";
import { createPlaybackEngine, type PlaybackEngine, type PlaybackError } from "./playback";
import { getProgress } from "./storage";
import type { AdConfig, AdGateState, AudioTrack, MediaItem, ProgressRecord, Stream, Subtitle } from "./types";

export const adConfig: AdConfig = {
  enabled: import.meta.env.VITE_ENABLE_ADS !== "false",
  preplayEnabled: import.meta.env.VITE_ENABLE_PREPLAY_AD !== "false",
  smartLinkUrl: typeof import.meta.env.VITE_SMART_LINK_URL === "string" ? import.meta.env.VITE_SMART_LINK_URL : "",
  countdownSeconds: Math.max(1, Number(import.meta.env.VITE_AD_COUNTDOWN_SECONDS || 3)),
  bannerUrl: typeof import.meta.env.VITE_AD_BANNER_URL === "string" ? import.meta.env.VITE_AD_BANNER_URL : "",
  bannerImageUrl: typeof import.meta.env.VITE_AD_BANNER_IMAGE_URL === "string" ? import.meta.env.VITE_AD_BANNER_IMAGE_URL : "",
  bannerLabel: typeof import.meta.env.VITE_AD_BANNER_LABEL === "string" ? import.meta.env.VITE_AD_BANNER_LABEL : "Sponsored placement",
};

export function valueOr(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

export function getYear(item: MediaItem): string {
  return valueOr(item.year || item.release_date?.slice(0, 4), "");
}

export function getRating(item: MediaItem): string {
  const value = item.imdb_rating ?? item.rating;
  return value === null || value === undefined || value === "" ? "" : Number(value).toFixed(1);
}

export function getGenres(item: MediaItem): string {
  if (Array.isArray(item.genre)) return item.genre.join(" · ");
  return valueOr(item.genre, "");
}

export function getMediaPath(item: MediaItem): string {
  const path = item.type === "Series" ? `/series/${encodeURIComponent(item.subject_id)}` : `/movie/${encodeURIComponent(item.subject_id)}`;
  return item.detail_path ? `${path}?detail_path=${encodeURIComponent(item.detail_path)}` : path;
}

export function getWatchPath(item: MediaItem, season?: number, episode?: number): string {
  const path = `/watch/${encodeURIComponent(item.subject_id)}`;
  const params = new URLSearchParams();
  if (season) params.set("season", String(season));
  if (episode) params.set("episode", String(episode));
  if (item.detail_path) params.set("detail_path", item.detail_path);
  return params.size ? `${path}?${params}` : path;
}

function initials(title: string): string {
  return title.split(/\s+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase();
}

export function PosterImage({ item, className = "", priority = false }: { item: Pick<MediaItem, "title" | "cover">; className?: string; priority?: boolean }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className={`poster-image ${className}`}>
      {item.cover && !failed ? <img src={item.cover} alt={`${item.title} poster`} loading={priority ? "eager" : "lazy"} onError={() => setFailed(true)} /> : <span className="poster-fallback">{initials(item.title || "MB")}</span>}
    </div>
  );
}

export function SearchBar({ compact = false }: { compact?: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const queryParam = new URLSearchParams(location.search).get("q") || "";
  const [value, setValue] = useState(queryParam);
  const [suggestions, setSuggestions] = useState<{ title: string; subject_id?: string | null; type?: "Movie" | "Series" }[]>([]);
  const [focused, setFocused] = useState(false);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => setValue(queryParam), [queryParam]);
  useEffect(() => {
    if (value.trim().length < 2) { setSuggestions([]); return; }
    const controller = new AbortController();
    let active = true;
    const timer = window.setTimeout(() => {
      setLoading(true);
      getSuggestions(value, controller.signal).then((items) => { if (active) setSuggestions(items); }).catch(() => { if (active) setSuggestions([]); }).finally(() => { if (active) setLoading(false); });
    }, 280);
    return () => { active = false; window.clearTimeout(timer); controller.abort(); };
  }, [value]);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const clean = value.trim();
    if (clean) navigate(`/search?q=${encodeURIComponent(clean)}`);
    setFocused(false);
  }

  return (
    <form className={`search-bar ${compact ? "search-bar-compact" : ""}`} onSubmit={submit} role="search">
      <Search size={17} aria-hidden="true" />
      <input ref={inputRef} value={value} onChange={(event) => setValue(event.target.value)} onFocus={() => setFocused(true)} onKeyDown={(event) => { if (event.key === "Escape") { setFocused(false); inputRef.current?.blur(); } }} placeholder="Search films, series, people" aria-label="Search films and series" />
      {loading && <LoaderCircle size={15} className="spin" aria-label="Loading suggestions" />}
      {focused && suggestions.length > 0 && <div className="suggestions" role="listbox">
        {suggestions.slice(0, 6).map((suggestion, index) => <button type="button" key={`${suggestion.title}-${index}`} onMouseDown={() => { setValue(suggestion.title); navigate(suggestion.subject_id ? `/${suggestion.type === "Series" ? "series" : "movie"}/${encodeURIComponent(suggestion.subject_id)}` : `/search?q=${encodeURIComponent(suggestion.title)}`); setFocused(false); }} role="option"><Search size={14} /><span>{suggestion.title}</span><ChevronRight size={14} /></button>)}
        <button type="submit" className="suggestion-all">See all results for “{value.trim()}”</button>
      </div>}
    </form>
  );
}

export function Navbar() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);
  return (
    <header className="site-header">
      <div className="nav-shell">
        <Link to="/" className="brand" aria-label="MovieBox Cinema home"><span className="brand-mark"><span /></span><span>moviebox</span><b>cinema</b></Link>
        <nav className={`main-nav ${open ? "is-open" : ""}`} aria-label="Main navigation">
          <Link className={location.pathname === "/" ? "active" : ""} to="/">Home</Link>
          <Link className={location.pathname.startsWith("/search") ? "active" : ""} to="/search">Discover</Link>
          <Link className={location.pathname.startsWith("/movie") ? "active" : ""} to="/search?type=Movie">Movies</Link>
          <Link className={location.pathname.startsWith("/series") ? "active" : ""} to="/search?type=Series">Series</Link>
        </nav>
        <div className="nav-tools"><SearchBar compact /><button className="icon-button nav-menu-button" onClick={() => setOpen((current) => !current)} aria-label={open ? "Close menu" : "Open menu"}>{open ? <X size={20} /> : <Menu size={20} />}</button></div>
      </div>
    </header>
  );
}

export function AdBanner({ placement = "content" }: { placement?: string }) {
  if (!adConfig.enabled) return null;
  const content = adConfig.bannerImageUrl ? <img src={adConfig.bannerImageUrl} alt={adConfig.bannerLabel} /> : <><span className="ad-kicker">{adConfig.bannerLabel}</span><span className="ad-copy">Keep the lights low. The good stuff is on.</span><span className="ad-placement">{placement}</span></>;
  return adConfig.bannerUrl ? <a className="ad-banner" href={adConfig.bannerUrl} target="_blank" rel="noopener noreferrer" aria-label={`${adConfig.bannerLabel}, opens in a new tab`}>{content}</a> : <div className="ad-banner ad-banner-muted" aria-label={adConfig.bannerLabel}>{content}</div>;
}

export function MovieCard({ item, compact = false }: { item: MediaItem; compact?: boolean }) {
  const rating = getRating(item);
  return <Link className={`movie-card ${compact ? "movie-card-compact" : ""}`} to={getMediaPath(item)}>
    <div className="card-art"><PosterImage item={item} /><span className="card-play"><Play size={16} fill="currentColor" /></span>{item.type === "Series" && <span className="card-type">Series</span>}</div>
    <div className="card-copy"><strong>{valueOr(item.title, "Untitled")}</strong><div className="card-meta"><span>{getYear(item)}</span>{rating && <><span className="meta-dot" /> <Star size={12} fill="currentColor" /> <span>{rating}</span></>}</div></div>
  </Link>;
}

export function MovieRow({ title, eyebrow = "CURATED FOR YOU", items, action }: { title: string; eyebrow?: string; items: MediaItem[]; action?: ReactNode }) {
  const [offset, setOffset] = useState(0);
  const visible = items.slice(offset, offset + 8);
  if (!items.length) return null;
  return <section className="content-section"><div className="section-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div><div className="section-actions">{action}{items.length > 8 && <><button className="round-button" onClick={() => setOffset((current) => Math.max(0, current - 4))} disabled={offset === 0} aria-label={`Previous ${title}`}><ChevronLeft size={18} /></button><button className="round-button" onClick={() => setOffset((current) => Math.min(Math.max(0, items.length - 8), current + 4))} disabled={offset + 8 >= items.length} aria-label={`Next ${title}`}><ChevronRight size={18} /></button></>}</div></div><div className="movie-row">{visible.map((item) => <MovieCard key={item.subject_id} item={item} />)}</div></section>;
}

export function HeroBanner({ item }: { item: MediaItem | null }) {
  if (!item) return <section className="hero hero-empty"><div><span className="eyebrow">MOVIEBOX CINEMA</span><h1>Your next favorite<br /><em>is waiting.</em></h1><p>Browse the latest films and series, with real availability from the MovieBox catalog.</p><Link className="button button-primary" to="/search">Explore the catalog <ChevronRight size={17} /></Link></div></section>;
  return <section className="hero" style={item.backdrop || item.cover ? { backgroundImage: `linear-gradient(90deg, #090a0c 4%, rgba(9,10,12,.88) 33%, rgba(9,10,12,.35) 70%, #090a0c 100%), url(${item.backdrop || item.cover})` } : undefined}><div className="hero-copy"><span className="eyebrow"><Sparkles size={13} /> TONIGHT'S STRONGEST SIGNAL</span><h1>{item.title}</h1><div className="hero-meta"><span>{getYear(item)}</span>{getRating(item) && <><span className="meta-dot" /><Star size={13} fill="currentColor" /><span>{getRating(item)}</span></>} {item.type === "Series" && <><span className="meta-dot" /><span>Series</span></>}</div><p>{valueOr(item.description, "A story worth making time for.")}</p><div className="hero-actions"><Link className="button button-primary" to={getWatchPath(item)}><Play size={17} fill="currentColor" /> Watch now</Link><Link className="button button-ghost" to={getMediaPath(item)}>View details <ChevronRight size={16} /></Link></div></div><div className="hero-signal">01 <span /> 05</div></section>;
}

export function LoadingGrid({ count = 6 }: { count?: number }) { return <div className="movie-row skeleton-row">{Array.from({ length: count }, (_, index) => <div className="movie-card skeleton-card" key={index}><div className="skeleton-art shimmer" /><div className="skeleton-line shimmer" /><div className="skeleton-line short shimmer" /></div>)}</div>; }

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) { return <div className="error-state"><div className="error-icon"><Film size={20} /></div><div><strong>Something interrupted the screening.</strong><p>{message}</p></div>{onRetry && <button className="button button-ghost" onClick={onRetry}>Try again</button>}</div>; }

function progressLabel(record: ProgressRecord): string { return record.type === "Series" && record.season && record.episode ? `Continue S${record.season}:E${record.episode}` : "Continue watching"; }

export function ContinueWatching({ onPick }: { onPick?: (record: ProgressRecord) => void }) {
  const items = getProgress().filter((item) => item.position > 5 && (item.duration === 0 || item.position < item.duration - 20));
  if (!items.length) return null;
  return <section className="content-section continue-section"><div className="section-heading"><div><span className="eyebrow">PICK UP THE STORY</span><h2>Continue watching</h2></div></div><div className="continue-row">{items.slice(0, 5).map((record) => <button type="button" className="continue-card" key={record.key} onClick={() => onPick?.(record)}><PosterImage item={{ title: record.title, cover: record.cover }} /><span className="continue-shade" /><span className="continue-play"><Play size={15} fill="currentColor" /></span><span className="continue-copy"><b>{progressLabel(record)}</b><strong>{record.title}</strong><span className="progress-track"><span style={{ width: record.duration ? `${Math.min(100, (record.position / record.duration) * 100)}%` : "18%" }} /></span></span></button>)}</div></section>;
}

export function PrePlayAdGate({ onUnlocked }: { onUnlocked: () => void }) {
  const [state, setState] = useState<AdGateState>(adConfig.preplayEnabled ? "AD_REQUIRED" : "PLAYBACK_ALLOWED");
  const [remaining, setRemaining] = useState(adConfig.countdownSeconds);
  const [popupBlocked, setPopupBlocked] = useState(false);
  const timerRef = useRef<number | undefined>(undefined);
  useEffect(() => () => { if (timerRef.current) window.clearInterval(timerRef.current); }, []);
  if (state === "PLAYBACK_ALLOWED") return null;
  async function openAd() {
    if (state !== "AD_REQUIRED" || !adConfig.smartLinkUrl) { setState(adConfig.smartLinkUrl ? "ERROR" : "PLAYBACK_ALLOWED"); if (!adConfig.smartLinkUrl) onUnlocked(); return; }
    setState("AD_OPENING");
    const adWindow = window.open(adConfig.smartLinkUrl, "_blank");
    if (adWindow) adWindow.opener = null;
    if (!adWindow) setPopupBlocked(true);
    setState("AD_WAITING");
    setRemaining(adConfig.countdownSeconds);
    timerRef.current = window.setInterval(() => setRemaining((current) => { if (current <= 1) { if (timerRef.current) window.clearInterval(timerRef.current); setState("AD_COMPLETED"); return 0; } return current - 1; }), 1000);
  }
  const completed = state === "AD_COMPLETED";
  return <div className="gate-overlay" role="dialog" aria-modal="true" aria-labelledby="ad-gate-title"><div className="gate-card"><div className="gate-icon"><Sparkles size={22} /></div><span className="eyebrow">BEFORE THE SCREENING</span><h2 id="ad-gate-title">Watch a short ad to start the video</h2>{state === "ERROR" ? <p>We couldn't open the sponsor link safely. You can continue without it.</p> : completed ? <p className="gate-complete">Ad completed — video starts in <b>now</b>.</p> : state === "AD_WAITING" ? <p>Thanks for supporting the service. Video unlocks in <b>{remaining}</b>.</p> : <p>Playback stays paused until you choose to open the sponsor link. No surprise tabs.</p>}{popupBlocked && <p className="gate-warning">Your browser blocked the new tab. <a href={adConfig.smartLinkUrl} target="_blank" rel="noopener noreferrer">Open the sponsor link</a>, then return here.</p>}<div className="gate-actions">{completed || state === "ERROR" ? <button className="button button-primary" onClick={onUnlocked}><Play size={16} fill="currentColor" /> Start playback</button> : <button className="button button-primary" onClick={openAd} disabled={state === "AD_OPENING" || state === "AD_WAITING"}>{state === "AD_WAITING" ? `Unlocking · ${remaining}` : "Watch ad"}</button>}{!adConfig.smartLinkUrl && <button className="button button-ghost" onClick={onUnlocked}>Continue without ad</button>}</div></div></div>;
}

function formatTime(seconds: number): string { if (!Number.isFinite(seconds) || seconds < 0) return "0:00"; const minutes = Math.floor(seconds / 60); const remainder = Math.floor(seconds % 60).toString().padStart(2, "0"); return `${minutes}:${remainder}`; }

function subtitleSource(url: string, signal: AbortSignal): Promise<string> {
  return fetch(resolveApiUrl(url), { signal }).then((response) => { if (!response.ok) throw new Error("Subtitle unavailable"); return response.text(); }).then((text) => { const clean = text.replace(/^\uFEFF/, "").replace(/\r/g, "").trim(); if (/^WEBVTT/i.test(clean)) return clean; const converted = clean.replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, "$1.$2"); return `WEBVTT\n\n${converted}`; });
}

function isHevcStream(stream: Stream): boolean {
  const codec = String(stream.codec || "").toLowerCase();
  const url = String(stream.url || "").toLowerCase();
  return /hevc|h\.?265|x265|hvc1|hev1/.test(codec) || /h265|hevc|hvc1|hev1/.test(url);
}

function browserCanPlayStream(stream: Stream): boolean {
  if (stream.is_browser_playable === false) return false;
  if (!isHevcStream(stream)) return true;
  const profiles = ["hvc1", "hev1", "hvc1.1.6.L93.B0", "hev1.1.6.L93.B0"];
  const isDash = stream.is_dash || stream.format?.toLowerCase() === "dash" || /\.mpd(?:$|[?#])/i.test(stream.url);
  // DASH.js requires MSE support; progressive MP4 can also use the browser's
  // native decoder, which may support HEVC even when MSE does not.
  const mediaSource = window.MediaSource;
  const mseSupported = Boolean(mediaSource && typeof mediaSource.isTypeSupported === "function" && profiles.some((profile) => mediaSource.isTypeSupported(`video/mp4; codecs="${profile}"`)));
  if (isDash) return mseSupported;
  const video = document.createElement("video");
  return mseSupported || profiles.some((profile) => video.canPlayType(`video/mp4; codecs="${profile}"`) !== "");
}

function playbackFailureMessage(code?: number): string {
  if (code === 1) return "Playback was interrupted. Reload the source or resolve it again.";
  if (code === 2) return "The video connection was interrupted. Resolve the stream to try another source.";
  if (code === 3) return "This video could not be decoded by the browser. Choose another quality or resolve the stream.";
  if (code === 4) return "This video format is not supported by the browser. Choose another quality or resolve the stream.";
  return "The selected stream could not be loaded. Reload it or resolve another source.";
}

export function VideoPlayer({ streams, captions, allowed, initialPosition = 0, onProgress, onRetry, onEnded }: { streams: Stream[]; captions: Subtitle[]; allowed: boolean; initialPosition?: number; onProgress?: (position: number, duration: number) => void; onRetry?: () => void; onEnded?: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const engineRef = useRef<PlaybackEngine | null>(null);
  const expectedMediaSourceRef = useRef("");
  const resumeAfterLoadRef = useRef(false);
  const saveRef = useRef(0);
  const [streamSelection, setStreamSelection] = useState<{ streams: Stream[]; index: number } | null>(null);
  const [compatibilityMode, setCompatibilityMode] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [buffering, setBuffering] = useState(true);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [showControls, setShowControls] = useState(true);
  const [error, setError] = useState("");
  const [selectedCaption, setSelectedCaption] = useState("");
  const [captionSrc, setCaptionSrc] = useState("");
  const [fullscreen, setFullscreen] = useState(false);
  const [positionApplied, setPositionApplied] = useState(false);
  const [sourceAttempt, setSourceAttempt] = useState(0);
  const hideTimer = useRef<number | undefined>(undefined);
  const failedStreamIndices = useRef(new Set<number>());
  const lastAutoFallbackFrom = useRef<number | null>(null);
  const compatibilityAttemptedFor = useRef<number | null>(null);
  const playableStreams = useMemo(() => {
    const browserPlayable = streams.filter((candidate) => Boolean(candidate.compatibility_url) || browserCanPlayStream(candidate));
    // Prefer broadly supported H.264 MP4 sources when the provider returns
    // them; some Chromium builds claim HEVC support but still cannot decode it.
    const hasH264Mp4 = browserPlayable.some((candidate) =>
      candidate.format?.toLowerCase() === "mp4" && /h264|avc/i.test(candidate.codec || ""),
    );
    return hasH264Mp4 ? browserPlayable.filter((candidate) => !isHevcStream(candidate)) : browserPlayable;
  }, [streams]);
  const startupIndex = useMemo(() => playableStreams.reduce((best, candidate, index, list) => {
    const quality = Number(candidate.quality_value || candidate.quality.match(/\d+/)?.[0] || 0);
    const bestQuality = best < 0 ? 0 : Number(list[best]?.quality_value || list[best]?.quality.match(/\d+/)?.[0] || 0);
    return quality <= 720 && quality > bestQuality ? index : best;
  }, -1), [playableStreams]);
  const streamIndex = streamSelection?.streams === playableStreams ? streamSelection.index : startupIndex < 0 ? 0 : startupIndex;
  const stream = playableStreams[streamIndex];
  const effectiveStream = stream && compatibilityMode && stream.compatibility_url
    ? { ...stream, playback_url: stream.compatibility_url, format: "MP4", is_dash: false }
    : stream;
  const qualityOptions = useMemo(() => playableStreams.filter((value, index, list) => list.findIndex((candidate) => candidate.quality === value.quality) === index), [playableStreams]);

  function selectStream(index: number) { setStreamSelection({ streams: playableStreams, index }); }

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = volume;
    video.muted = muted;
  }, [volume, muted]);

  function advanceAfterFailure(index: number): boolean {
    if (failedStreamIndices.current.has(index)) return lastAutoFallbackFrom.current === index;
    failedStreamIndices.current.add(index);
    const nextIndex = playableStreams.findIndex((_candidate, candidateIndex) => !failedStreamIndices.current.has(candidateIndex));
    if (nextIndex < 0) return false;
    lastAutoFallbackFrom.current = index;
    resumeAfterLoadRef.current = Boolean(videoRef.current && !videoRef.current.paused && allowed);
    setError(""); setBuffering(true); setCompatibilityMode(false); selectStream(nextIndex);
    return true;
  }

  function tryCompatibilityPlayback(index: number, code?: number): boolean {
    if (compatibilityMode || (code !== 2 && code !== 3 && code !== 4 && !playableStreams[index]?.is_dash)) return false;
    const preferredIndex = playableStreams.reduce((best, candidate, candidateIndex, list) => {
      if (!candidate.compatibility_url) return best;
      const quality = Number(candidate.quality_value || candidate.quality.match(/\d+/)?.[0] || 0);
      const bestQuality = best < 0 ? 0 : Number(list[best]?.quality_value || list[best]?.quality.match(/\d+/)?.[0] || 0);
      return quality <= 720 && quality > bestQuality ? candidateIndex : best;
    }, -1);
    const candidateIndex = preferredIndex >= 0 ? preferredIndex : playableStreams.findIndex((candidate) => Boolean(candidate.compatibility_url));
    if (candidateIndex < 0) return false;
    if (compatibilityAttemptedFor.current === candidateIndex) return true;
    compatibilityAttemptedFor.current = candidateIndex;
    resumeAfterLoadRef.current = Boolean(videoRef.current && !videoRef.current.paused && allowed);
    setError(""); setBuffering(true); setCompatibilityMode(true);
    if (candidateIndex !== index) selectStream(candidateIndex);
    return true;
  }

  function recoverPlaybackFailure(index: number, code?: number, message?: string) {
    if (!compatibilityMode && advanceAfterFailure(index)) return;
    if (!compatibilityMode && tryCompatibilityPlayback(index, code)) return;
    setBuffering(false);
    setError(compatibilityMode
      ? "This browser couldn't play the converted stream. Resolve again or try another source."
      : message || playbackFailureMessage(code));
  }

  useEffect(() => {
    failedStreamIndices.current.clear();
    lastAutoFallbackFrom.current = null;
    compatibilityAttemptedFor.current = null;
    setCompatibilityMode(false);
    setPositionApplied(false);
    setStreamSelection({ streams: playableStreams, index: startupIndex < 0 ? 0 : startupIndex });
  }, [playableStreams]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !stream || !effectiveStream) return;
    let active = true;
    const startAt = compatibilityMode ? 0 : video.currentTime > 0 ? video.currentTime : initialPosition;
    const shouldResume = Boolean(!video.paused && allowed);
    setError(""); setBuffering(true);
    video.pause();
    expectedMediaSourceRef.current = new URL(resolveApiUrl(effectiveStream.playback_url || effectiveStream.url), window.location.href).href;
    engineRef.current?.destroy();
    const engine = createPlaybackEngine(video, effectiveStream, (diagnostic: PlaybackError) => {
      if (import.meta.env.DEV) console.error("[MovieBox playback] DASH error", diagnostic);
      if (active) recoverPlaybackFailure(streamIndex, diagnostic.code, diagnostic.message || "The selected stream could not be loaded.");
    });
    engineRef.current = engine;
    engine.load(effectiveStream, startAt).then(() => {
      if (active) setBuffering(false);
    }).catch(() => {
      if (active) recoverPlaybackFailure(streamIndex, video.error?.code);
    });
    return () => { active = false; engine.destroy(); if (engineRef.current === engine) engineRef.current = null; if (shouldResume) resumeAfterLoadRef.current = true; };
  }, [effectiveStream?.playback_url, effectiveStream?.url, compatibilityMode, sourceAttempt]);
  useEffect(() => {
    if (!selectedCaption) { setCaptionSrc(""); return; }
    const controller = new AbortController();
    subtitleSource(selectedCaption, controller.signal).then((text) => setCaptionSrc(URL.createObjectURL(new Blob([text], { type: "text/vtt" })))).catch((cause: unknown) => { if (import.meta.env.DEV && !controller.signal.aborted) console.warn("[MovieBox playback] subtitle error", cause); setCaptionSrc(""); });
    return () => { controller.abort(); setCaptionSrc((current) => { if (current) URL.revokeObjectURL(current); return ""; }); };
  }, [selectedCaption]);
  useEffect(() => () => { if (hideTimer.current) window.clearTimeout(hideTimer.current); }, []);
  useEffect(() => {
    function keyboard(event: KeyboardEvent) {
      const tag = (event.target as HTMLElement).tagName;
      if (["INPUT", "SELECT", "BUTTON"].includes(tag)) return;
      const video = videoRef.current;
      if (!video) return;
      if (event.key === " ") { event.preventDefault(); void togglePlay(); }
      if (event.key.toLowerCase() === "m") setMuted((current) => !current);
      if (event.key === "ArrowRight" && !compatibilityMode) video.currentTime = Math.min(video.duration || 0, video.currentTime + 10);
      if (event.key === "ArrowLeft" && !compatibilityMode) video.currentTime = Math.max(0, video.currentTime - 10);
      if (event.key.toLowerCase() === "f") void toggleFullscreen();
    }
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  });
  if (!stream) return <div className="player-shell player-empty"><Film size={25} /><p>{streams.length ? "This browser doesn't support the available video formats." : "No playable stream was returned for this title."}</p>{onRetry && <button className="button button-primary" onClick={onRetry}>Resolve again</button>}</div>;

  async function togglePlay() { const video = videoRef.current; const engine = engineRef.current; if (!video || !engine || !allowed) return; if (video.paused) { try { await engine.play(); } catch (cause: unknown) { if (import.meta.env.DEV) console.warn("[MovieBox playback] play blocked", cause); setError("Playback was blocked by the browser. Press play to try again."); } } else engine.pause(); }
  function retryCurrentSource() { failedStreamIndices.current.clear(); lastAutoFallbackFrom.current = null; compatibilityAttemptedFor.current = null; setCompatibilityMode(false); setError(""); setBuffering(true); setSourceAttempt((attempt) => attempt + 1); }
  async function toggleFullscreen() { const root = videoRef.current?.parentElement?.parentElement; if (!root) return; if (!document.fullscreenElement) { await root.requestFullscreen?.(); setFullscreen(true); } else { await document.exitFullscreen?.(); setFullscreen(false); } }
  function switchQuality(event: ChangeEvent<HTMLSelectElement>) { const next = playableStreams.findIndex((candidate) => candidate.quality === event.target.value); if (next < 0 || next === streamIndex) return; failedStreamIndices.current.clear(); lastAutoFallbackFrom.current = null; compatibilityAttemptedFor.current = null; setCompatibilityMode(false); resumeAfterLoadRef.current = Boolean(videoRef.current && !videoRef.current.paused); selectStream(next); }
  function signalActivity() { setShowControls(true); if (hideTimer.current) window.clearTimeout(hideTimer.current); if (playing) hideTimer.current = window.setTimeout(() => setShowControls(false), 2600); }
  function handleTime(event: React.SyntheticEvent<HTMLVideoElement>) { const video = event.currentTarget; const total = Number.isFinite(video.duration) ? video.duration : duration; setCurrentTime(video.currentTime); if (Math.abs(video.currentTime - saveRef.current) > 3 || video.currentTime === 0) { saveRef.current = video.currentTime; onProgress?.(video.currentTime, total); } }
  function handleMetadata(event: React.SyntheticEvent<HTMLVideoElement>) { const video = event.currentTarget; setDuration(Number.isFinite(video.duration) ? video.duration : Number(stream.duration_sec || 0)); setBuffering(false); if (!compatibilityMode && !positionApplied && initialPosition > 0 && Number.isFinite(video.duration)) { video.currentTime = Math.min(initialPosition, Math.max(0, video.duration - 4)); setPositionApplied(true); } if (allowed && resumeAfterLoadRef.current) { resumeAfterLoadRef.current = false; void engineRef.current?.play().catch(() => undefined); } }
  function handleMediaError(event: React.SyntheticEvent<HTMLVideoElement>) { const video = event.currentTarget; if (!video.currentSrc || video.currentSrc !== expectedMediaSourceRef.current) return; const mediaError = video.error; const diagnostics = { code: mediaError?.code, message: mediaError?.message, quality: stream.quality, codec: stream.codec, format: stream.format }; if (import.meta.env.DEV) console.error("[MovieBox playback] media error", diagnostics); recoverPlaybackFailure(streamIndex, mediaError?.code); }
  return <div className={`player-shell ${showControls ? "controls-visible" : "controls-hidden"}`} onMouseMove={signalActivity} onTouchStart={signalActivity}>
    <video ref={videoRef} playsInline preload="metadata" onClick={() => void togglePlay()} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onWaiting={() => setBuffering(true)} onCanPlay={() => setBuffering(false)} onLoadedMetadata={handleMetadata} onTimeUpdate={handleTime} onEnded={onEnded} onError={handleMediaError} onVolumeChange={(event) => { setVolume(event.currentTarget.volume); setMuted(event.currentTarget.muted); }} aria-label="MovieBox video player">{captionSrc && <track kind="subtitles" src={captionSrc} srcLang="en" label={captions.find((caption) => caption.url === selectedCaption)?.language || "Subtitles"} default />}</video>
    {!allowed && <div className="player-locked"><span><Play size={20} fill="currentColor" /></span><b>Playback is ready</b><small>Complete the short pre-play step to begin.</small></div>}
    {buffering && allowed && !error && <div className="player-buffer"><LoaderCircle className="spin" size={28} /></div>}
    {error && <div className="player-error"><strong>Playback paused</strong><p>{error}</p><div><button className="button button-primary" onClick={onRetry}>Resolve again</button><button className="button button-ghost" onClick={retryCurrentSource}>Reload source</button></div></div>}
    <div className="player-topline"><span>{stream.quality || "Auto"}</span><span>{compatibilityMode ? "Compatible MP4" : stream.format || "Stream"}</span></div>
    <div className="player-controls"><input className="seekbar" type="range" min="0" max={duration || 0} step="0.1" value={Math.min(currentTime, duration || 0)} disabled={compatibilityMode || !duration} onChange={(event) => { const value = Number(event.target.value); if (videoRef.current) videoRef.current.currentTime = value; setCurrentTime(value); }} aria-label="Seek video" /><div className="control-row"><button className="player-button" onClick={() => void togglePlay()} aria-label={playing ? "Pause" : "Play"}>{playing ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" />}</button><button className="player-button" disabled={compatibilityMode} onClick={() => { if (videoRef.current) videoRef.current.currentTime -= 10; }} aria-label="Rewind 10 seconds"><RotateCcw size={17} /></button><span className="time-label">{formatTime(currentTime)} / {formatTime(duration)}</span><div className="control-spacer" /><label className="volume-control"><button className="player-button" onClick={() => { const nextMuted = !muted; setMuted(nextMuted); if (videoRef.current) videoRef.current.muted = nextMuted; }} aria-label={muted ? "Unmute" : "Mute"}>{muted ? <VolumeX size={18} /> : <Volume2 size={18} />}</button><input type="range" min="0" max="1" step="0.05" value={muted ? 0 : volume} onChange={(event) => { const next = Number(event.target.value); setVolume(next); setMuted(next === 0); if (videoRef.current) { videoRef.current.volume = next; videoRef.current.muted = next === 0; } }} aria-label="Volume" /></label><label className="player-select"><Gauge size={14} /><select value={speed} onChange={(event) => { const next = Number(event.target.value); setSpeed(next); if (videoRef.current) videoRef.current.playbackRate = next; }} aria-label="Playback speed"><option value="1">1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label>{qualityOptions.length > 1 && <label className="player-select"><span>HD</span><select value={stream.quality} onChange={switchQuality} aria-label="Video quality">{qualityOptions.map((option) => <option value={option.quality} key={option.quality}>{option.quality}</option>)}</select></label>}{captions.length > 0 && <label className="player-select"><Subtitles size={14} /><select value={selectedCaption} onChange={(event) => setSelectedCaption(event.target.value)} aria-label="Subtitles"><option value="">Subtitles off</option>{captions.map((caption) => <option value={caption.url} key={caption.url}>{caption.language}</option>)}</select></label>}<button className="player-button" onClick={() => { const video = videoRef.current; if (video && "pictureInPictureEnabled" in document) void video.requestPictureInPicture?.(); }} aria-label="Picture in picture"><PictureInPicture size={18} /></button><button className="player-button" onClick={() => void toggleFullscreen()} aria-label={fullscreen ? "Exit fullscreen" : "Fullscreen"}><Expand size={19} /></button></div></div>
  </div>;
}

export function EpisodeNavigator({ current, total, onPrevious, onNext }: { current: number; total: number; onPrevious: () => void; onNext: () => void }) { return <div className="episode-nav"><button className="button button-ghost" onClick={onPrevious} disabled={current <= 1}><SkipBack size={15} /> Previous</button><span>Episode {current} of {total}</span><button className="button button-ghost" onClick={onNext} disabled={current >= total}>Next <SkipForward size={15} /></button></div>; }

export function AudioInfo({ dubs }: { dubs?: AudioTrack[] }) { const valid = (dubs || []).filter((dub) => dub.lang_name || dub.language); if (!valid.length) return null; return <div className="audio-info"><Headphones size={15} /><span>Audio tracks</span><b>{valid.map((dub) => dub.lang_name || dub.language).join(" · ")}</b></div>; }

export function Footer() { return <footer className="site-footer"><div className="footer-brand"><span className="brand-mark"><span /></span><span>moviebox</span><b>cinema</b></div><p>Stories with a little more signal.</p><span className="footer-note">Stream availability is supplied by the upstream MovieBox API.</span></footer>; }

export function PageTitle({ eyebrow, title, children }: { eyebrow: string; title: string; children?: ReactNode }) { return <div className="page-title"><span className="eyebrow">{eyebrow}</span><h1>{title}</h1>{children}</div>; }

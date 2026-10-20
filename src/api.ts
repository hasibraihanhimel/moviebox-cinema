import type {
  CaptionsResponse,
  CatalogResponse,
  DetailResponse,
  EpisodesResponse,
  MediaItem,
  SearchResponse,
  Stream,
  StreamResponse,
  Suggestion,
} from "./types";

// In local development the relative base is proxied to FastAPI. A combined
// production container serves the API and frontend from the same origin.
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? "/backend" : "")).replace(/\/$/, "");

export function resolveApiUrl(value: string): string {
  if (!value) return value;
  if (/^(?:[a-z][a-z\d+.-]*:)?\/\//i.test(value)) return value;
  const path = value.startsWith("/") ? value : `/${value}`;
  return `${API_BASE}${path}`;
}
const cache = new Map<string, { expires: number; value: unknown }>();
const inflight = new Map<string, Promise<unknown>>();

function validId(value: string): string {
  if (!/^[A-Za-z0-9_-]{2,160}$/.test(value)) throw new Error("That title could not be identified.");
  return value;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : {};
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : value == null ? fallback : String(value);
}

function asScalar(value: unknown): string | number | null {
  return typeof value === "string" || typeof value === "number" ? value : null;
}

function normalizeItem(raw: unknown): MediaItem | null {
  const value = asRecord(raw);
  const subject = asRecord(value.subject);
  const source = Object.keys(subject).length ? subject : value;
  const id = asString(source.subject_id || source.subjectId || source.id);
  const title = asString(source.title || source.name);
  if (!id || /^(?:none|null|undefined)$/i.test(id) || !title) return null;
  const coverValue = source.cover;
  const cover = typeof coverValue === "object" && coverValue !== null ? asString(asRecord(coverValue).url) : asString(coverValue, "");
  return {
    subject_id: id,
    title,
    type: source.type === "Series" || source.subject_type === 2 || source.subjectType === 2 ? "Series" : "Movie",
    subject_type: typeof source.subject_type === "number" ? source.subject_type : typeof source.subjectType === "number" ? source.subjectType : undefined,
    release_date: asString(source.release_date || source.releaseDate, "") || null,
    year: asString(source.year || source.release_date || source.releaseDate, "").slice(0, 4) || null,
    imdb_rating: asScalar(source.imdb_rating ?? source.imdbRatingValue),
    rating: asScalar(source.rating ?? source.imdbRatingValue),
    genre: (source.genre as string | string[] | null | undefined) ?? null,
    cover: cover || null,
    backdrop: asString(source.backdrop || source.background || source.coverUrl, "") || cover || null,
    detail_path: asString(source.detail_path || source.detailPath, "") || null,
    language: asString(source.language, "") || null,
    description: asString(source.description || source.plot, "") || null,
    duration: (source.duration as string | number | null | undefined) ?? null,
    country: asString(source.country || source.countryName, "") || null,
  };
}

function normalizeList(raw: unknown): MediaItem[] {
  if (!Array.isArray(raw)) return [];
  return raw.map(normalizeItem).filter((item): item is MediaItem => Boolean(item));
}

function normalizeStream(raw: unknown): Stream {
  const value = asRecord(raw);
  return {
    source: asString(value.source),
    format: asString(value.format, "unknown"),
    quality: asString(value.quality, "Unknown"),
    quality_value: Number(value.quality_value || 0),
    resolution_tag: asString(value.resolution_tag),
    url: asString(value.url),
    size_mb: Number(value.size_mb || 0),
    codec: asString(value.codec) || null,
    duration_sec: typeof value.duration_sec === "number" ? value.duration_sec : null,
    headers: asRecord(value.headers) as Record<string, string>,
    playback_url: asString(value.playback_url) || undefined,
    is_dash: Boolean(value.is_dash),
    is_browser_playable: value.is_browser_playable !== false,
    requires_proxy: Boolean(value.requires_proxy),
    requires_headers: Boolean(value.requires_headers),
  };
}

function errorMessage(status: number): string {
  if (status === 404) return "We couldn't find that title.";
  if (status >= 500) return "The cinema service is having trouble right now. Please try again.";
  return "We couldn't complete that request. Please try again.";
}

async function request<T>(path: string, options: RequestInit = {}, ttl = 0): Promise<T> {
  const key = `${options.method || "GET"}:${path}`;
  // Search requests are cancelled as the user types. Do not share a cancellable
  // fetch with another caller, since one caller's AbortController would cancel
  // the request for every other caller too.
  const canDeduplicate = !options.signal;
  const cached = cache.get(key);
  if (ttl > 0 && cached && cached.expires > Date.now()) return cached.value as T;
  const existing = canDeduplicate ? inflight.get(key) : undefined;
  if (existing) return existing as Promise<T>;
  const promise = fetch(resolveApiUrl(path), {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  }).then(async (response) => {
    if (!response.ok) throw new Error(errorMessage(response.status));
    return response.json() as Promise<T>;
  }).then((value) => {
    if (ttl > 0) cache.set(key, { value, expires: Date.now() + ttl });
    return value;
  }).catch((error: unknown) => {
    if (error instanceof TypeError) throw new Error("The cinema service is unavailable. Check your connection and try again.");
    throw error instanceof Error ? error : new Error("Something went wrong. Please try again.");
  }).finally(() => { if (canDeduplicate) inflight.delete(key); });
  if (canDeduplicate) inflight.set(key, promise);
  return promise;
}

export async function getHome(): Promise<unknown> {
  return request<unknown>("/home", {}, 45_000);
}

export async function getCatalog(kind: "movies" | "tv-series", page = 1, signal?: AbortSignal): Promise<CatalogResponse> {
  const raw = await request<Record<string, unknown>>(`/${kind}?page=${page}&per_page=24`, { signal }, 45_000);
  const type: MediaItem["type"] = kind === "tv-series" ? "Series" : "Movie";
  const expectedSubjectType = type === "Series" ? 2 : 1;
  const items = normalizeList(raw.items).filter((item) => item.subject_type === undefined || item.subject_type === expectedSubjectType).map((item) => ({ ...item, type, subject_type: expectedSubjectType }));
  return { page, per_page: Number(raw.per_page || 24), total: Number(raw.total || 0), items };
}

export async function searchTitles(query: string, page = 1, signal?: AbortSignal): Promise<SearchResponse> {
  const clean = query.trim();
  if (clean.length < 2) return { query: clean, page, results: [] };
  try {
    const raw = await request<{ query?: string; page?: number; results?: unknown[] }>(`/api/search?q=${encodeURIComponent(clean)}&page=${page}&per_page=24`, { signal });
    return { query: clean, page, results: normalizeList(raw.results) };
  } catch (error) {
    if (signal?.aborted) throw error;
    const suggestions = await getSuggestions(clean, signal);
    const results = suggestions.filter((item) => item.subject_id && !/^(?:none|null|undefined)$/i.test(item.subject_id)).map((item) => ({ subject_id: item.subject_id!, title: item.title, type: item.type || "Movie" as const }));
    if (!results.length) throw error;
    return { query: clean, page, results };
  }
}

export async function getSuggestions(query: string, signal?: AbortSignal): Promise<Suggestion[]> {
  const clean = query.trim();
  if (clean.length < 2) return [];
  const raw = await request<{ suggestions?: Suggestion[] }>(`/search/suggest?q=${encodeURIComponent(clean)}`, { signal });
  return Array.isArray(raw.suggestions) ? raw.suggestions.filter((item) => item && typeof item.title === "string").map((item) => ({ ...item, subject_id: item.subject_id && !/^(?:none|null|undefined)$/i.test(item.subject_id) ? item.subject_id : null })) : [];
}

export async function getDetail(id: string): Promise<DetailResponse> {
  const raw = await request<DetailResponse>(`/detail/${encodeURIComponent(validId(id))}`, {}, 120_000);
  return { ...raw, subject_id: asString(raw.subject_id || id), title: asString(raw.title, "Untitled"), type: raw.type === "Series" ? "Series" : "Movie" };
}

export async function getEpisodes(id: string): Promise<EpisodesResponse> {
  const raw = await request<EpisodesResponse>(`/episodes/${encodeURIComponent(validId(id))}`, {}, 120_000);
  return { subject_id: id, seasons: Array.isArray(raw.seasons) ? raw.seasons.map((season) => ({ season: Number(season.season), episode_count: Number(season.episode_count || 0), resolutions: season.resolutions })) : [], episodes: raw.episodes };
}

export async function getStreams(id: string, season = 0, episode = 0, detailPath?: string): Promise<StreamResponse> {
  const params = new URLSearchParams();
  if (season > 0) params.set("se", String(season));
  if (episode > 0) params.set("ep", String(episode));
  if (detailPath) params.set("detail_path", detailPath.slice(0, 180));
  const raw = await request<Record<string, unknown>>(`/api/stream/${encodeURIComponent(validId(id))}${params.size ? `?${params}` : ""}`, { cache: "no-store" });
  return { subject_id: asString(raw.subject_id, id), season: Number(raw.season || season), episode: Number(raw.episode || episode), count: Number(raw.count || 0), streams: Array.isArray(raw.streams) ? raw.streams.map(normalizeStream) : [] };
}

export async function getCaptions(id: string, season = 0, episode = 0): Promise<CaptionsResponse> {
  const params = new URLSearchParams();
  if (season > 0) params.set("se", String(season));
  if (episode > 0) params.set("ep", String(episode));
  return request<CaptionsResponse>(`/api/stream/${encodeURIComponent(validId(id))}/captions${params.size ? `?${params}` : ""}`, { cache: "no-store" });
}

export function extractHomeLists(raw: unknown): { hero: MediaItem | null; sections: { title: string; items: MediaItem[] }[] } {
  const root = asRecord(raw);
  const data = asRecord(root.data);
  const source = Object.keys(data).length ? data : root;
  const sections: { title: string; items: MediaItem[] }[] = [];
  let hero: MediaItem | null = null;
  const add = (title: string, value: unknown) => {
    const items = normalizeList(value);
    if (items.length) sections.push({ title, items });
  };
  const operatingList = Array.isArray(source.operatingList) ? source.operatingList : [];
  const bannerSection = operatingList.find((section) => asRecord(section).type === "BANNER");
  if (bannerSection) {
    const bannerItems = asRecord(asRecord(bannerSection).banner).items;
    hero = normalizeList(bannerItems).find((item) => item.cover) || null;
  }
  for (const section of operatingList) {
    const sectionRecord = asRecord(section);
    const sectionItems = normalizeList(sectionRecord.subjects);
    const title = asString(sectionRecord.title, "").trim();
    if (sectionItems.length && title) sections.push({ title, items: sectionItems });
  }
  const candidates: [string, string[]][] = [
    ["Trending now", ["trending", "trendingList", "trendingMovies"]],
    ["Popular this week", ["popular", "popularMovies", "hot"]],
    ["Latest releases", ["latest", "latestMovies", "newMovies"]],
    ["Series worth starting", ["series", "tvSeries", "latestSeries"]],
    ["Recommended for you", ["recommended", "recommend"]],
  ];
  for (const [title, keys] of candidates) for (const key of keys) if (source[key]) { add(title, source[key]); break; }
  const allItems = sections.flatMap((section) => section.items);
  return { hero: hero || allItems[0] || null, sections };
}

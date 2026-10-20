export type MediaKind = "Movie" | "Series";

export interface MediaItem {
  subject_id: string;
  title: string;
  type?: MediaKind;
  subject_type?: number;
  release_date?: string | null;
  year?: string | number | null;
  imdb_rating?: string | number | null;
  rating?: string | number | null;
  genre?: string | string[] | null;
  cover?: string | null;
  backdrop?: string | null;
  detail_path?: string | null;
  language?: string | null;
  description?: string | null;
  duration?: string | number | null;
  country?: string | null;
  dubs?: AudioTrack[];
}

export interface AudioTrack {
  subject_id?: string;
  lang_name?: string | null;
  language?: string | null;
}

export interface Season {
  season: number;
  episode_count: number;
  resolutions?: unknown[];
}

export interface Episode {
  season?: number;
  episode?: number;
  number?: number;
  ep?: number;
  title?: string | null;
  name?: string | null;
  description?: string | null;
  thumbnail?: string | null;
  cover?: string | null;
}

export interface Stream {
  source?: string;
  format?: string;
  quality: string;
  quality_value?: number;
  resolution_tag?: string;
  url: string;
  size_mb?: number;
  codec?: string | null;
  duration_sec?: number | null;
  headers?: Record<string, string>;
  playback_url?: string;
  is_dash?: boolean;
  is_browser_playable?: boolean;
  requires_proxy?: boolean;
  requires_headers?: boolean;
}

export interface Subtitle {
  id: string;
  language: string;
  code?: string;
  url: string;
  size?: number | string | null;
}

export interface DetailResponse extends MediaItem {
  subject_id: string;
  title: string;
  type: MediaKind;
}

export interface EpisodesResponse {
  subject_id: string;
  seasons: Season[];
  episodes?: Episode[];
}

export interface StreamResponse {
  subject_id: string;
  season: number;
  episode: number;
  count: number;
  streams: Stream[];
}

export interface CaptionsResponse {
  subject_id: string;
  season: number;
  episode: number;
  count: number;
  captions: Subtitle[];
}

export interface CatalogResponse {
  page: number;
  per_page: number;
  total: number;
  items: MediaItem[];
}

export interface SearchResponse {
  query: string;
  page: number;
  results: MediaItem[];
}

export interface Suggestion {
  title: string;
  slug?: string | null;
  subject_id?: string | null;
  type?: MediaKind;
}

export interface ProgressRecord {
  key: string;
  subjectId: string;
  title: string;
  cover?: string | null;
  type: MediaKind;
  season?: number;
  episode?: number;
  position: number;
  duration: number;
  updatedAt: number;
}

export type AdGateState = "IDLE" | "AD_REQUIRED" | "AD_OPENING" | "AD_WAITING" | "AD_COMPLETED" | "PLAYBACK_ALLOWED" | "ERROR";

export interface AdConfig {
  enabled: boolean;
  preplayEnabled: boolean;
  smartLinkUrl: string;
  countdownSeconds: number;
  bannerUrl: string;
  bannerImageUrl: string;
  bannerLabel: string;
}

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, CalendarDays, Check, ChevronRight, Clock3, Globe2, Info, Languages, Play, RefreshCw, Star } from "lucide-react";
import { extractHomeLists, getCatalog, getCaptions, getDetail, getEpisodes, getHome, getStreams, searchTitles } from "./api";
import { getProgressFor, saveProgress } from "./storage";
import type { AudioTrack, DetailResponse, Episode, EpisodesResponse, MediaItem, ProgressRecord, Season, Stream, Subtitle } from "./types";
import { AdBanner, adConfig, AudioInfo, ContinueWatching, EpisodeNavigator, ErrorState, getGenres, getMediaPath, getRating, getWatchPath, getYear, HeroBanner, LoadingGrid, MovieCard, MovieRow, Navbar, PageTitle, PosterImage, PrePlayAdGate, valueOr, VideoPlayer } from "./components";

function setMeta(title: string, description: string, image?: string | null) {
  document.title = title;
  const set = (selector: string, value: string) => { const element = document.querySelector<HTMLMetaElement>(selector); if (element) element.content = value; };
  set('meta[name="description"]', description);
  set('meta[property="og:title"]', title);
  set('meta[property="og:description"]', description);
  if (image) set('meta[property="og:image"]', image);
  const canonical = document.querySelector<HTMLLinkElement>('link[rel="canonical"]') || document.head.appendChild(Object.assign(document.createElement("link"), { rel: "canonical" }));
  canonical.href = window.location.href;
}

function Shell({ children }: { children: React.ReactNode }) { return <><Navbar /><main>{children}</main><div className="page-ad"><AdBanner placement="footer" /></div><footer className="site-footer"><div className="footer-brand"><span className="brand-mark"><span /></span><span>moviebox</span><b>cinema</b></div><p>Stories with a little more signal.</p><span className="footer-note">Stream availability is supplied by the upstream MovieBox API.</span></footer></>; }

function HomePage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [hero, setHero] = useState<MediaItem | null>(null);
  const [sections, setSections] = useState<{ title: string; items: MediaItem[] }[]>([]);
  const [catalogMovies, setCatalogMovies] = useState<MediaItem[]>([]);
  const [catalogSeries, setCatalogSeries] = useState<MediaItem[]>([]);
  const navigate = useNavigate();
  async function load() {
    setLoading(true); setError("");
    try {
      const homePromise = getHome().then((raw) => {
        const parsed = extractHomeLists(raw);
        setHero((current) => parsed.hero || current);
        const movieSections = parsed.sections.map((section) => ({ title: `Movies · ${section.title}`, items: section.items.filter((item) => item.type === "Movie") })).filter((section) => section.items.length);
        const seriesSections = parsed.sections.map((section) => ({ title: `Series · ${section.title}`, items: section.items.filter((item) => item.type === "Series") })).filter((section) => section.items.length);
        setSections([...movieSections, ...seriesSections]);
        return raw;
      });
      const moviesPromise = getCatalog("movies").then((response) => {
        setCatalogMovies(response.items);
        setHero((current) => current || response.items[0] || null);
        return response;
      });
      const seriesPromise = getCatalog("tv-series").then((response) => {
        setCatalogSeries(response.items);
        setHero((current) => current || response.items[0] || null);
        return response;
      });
      const [homeResult, moviesResult, seriesResult] = await Promise.allSettled([homePromise, moviesPromise, seriesPromise]);
      const homeRaw = homeResult.status === "fulfilled" ? homeResult.value : null;
      const movies = moviesResult.status === "fulfilled" ? moviesResult.value : { items: [] as MediaItem[] };
      const series = seriesResult.status === "fulfilled" ? seriesResult.value : { items: [] as MediaItem[] };
      const parsed = extractHomeLists(homeRaw);
      setHero(parsed.hero || movies.items[0] || series.items[0] || null);
      const movieSections = parsed.sections.map((section) => ({ title: `Movies · ${section.title}`, items: section.items.filter((item) => item.type === "Movie") })).filter((section) => section.items.length);
      const seriesSections = parsed.sections.map((section) => ({ title: `Series · ${section.title}`, items: section.items.filter((item) => item.type === "Series") })).filter((section) => section.items.length);
      setSections([...movieSections, ...seriesSections]);
      setCatalogMovies(movies.items); setCatalogSeries(series.items);
      if (homeResult.status === "rejected" && !movies.items.length && !series.items.length) throw homeResult.reason;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "We couldn't load the catalog.");
    } finally { setLoading(false); }
  }
  useEffect(() => { void load(); setMeta("MovieBox Cinema · Find your next story", "Browse real films and series from the MovieBox catalog."); }, []);
  return <Shell><div className="page-wrap home-page"><HeroBanner item={hero} /><AdBanner placement="home-top" />{error && <ErrorState message={error} onRetry={() => void load()} />}{loading && !catalogMovies.length && !catalogSeries.length && !sections.length ? <><section className="content-section"><div className="section-heading"><div><span className="eyebrow">LOADING THE SHELF</span><h2>Finding something good</h2></div></div><LoadingGrid count={6} /></section><section className="content-section"><LoadingGrid count={6} /></section></> : <><ContinueWatching onPick={(record) => { const item: MediaItem = { subject_id: record.subjectId, title: record.title, type: record.type, cover: record.cover }; navigate(getWatchPath(item, record.season, record.episode)); }} />{sections.map((section, index) => <MovieRow key={`${section.title}-${index}`} title={section.title} items={section.items} eyebrow={index === 0 ? "RIGHT NOW" : "CURATED FOR YOU"} />)}{catalogMovies.length > 0 && <MovieRow title="Movies to make time for" eyebrow="THE MOVIE SHELF" items={catalogMovies} action={<Link className="text-link" to="/search?type=movies">See all <ChevronRight size={14} /></Link>} />}{catalogSeries.length > 0 && <MovieRow title="Series with a signal" eyebrow="THE SERIES SHELF" items={catalogSeries} action={<Link className="text-link" to="/search?type=series">See all <ChevronRight size={14} /></Link>} />}{!loading && !sections.length && !catalogMovies.length && !catalogSeries.length && <div className="empty-state"><Info size={22} /><h2>The shelf is quiet.</h2><p>Try again in a moment or search for a title directly.</p></div>}<AdBanner placement="home-content" /></>}</div></Shell>;
}

function SearchPage() {
  const [params, setParams] = useSearchParams();
  const query = params.get("q") || "";
  const requestedType = params.get("type");
  const browseType = requestedType?.toLowerCase() === "movie" || requestedType?.toLowerCase() === "movies" ? "Movie" : requestedType?.toLowerCase() === "series" ? "Series" : null;
  const requestedPage = Math.max(1, Number(params.get("page") || 1) || 1);
  const [input, setInput] = useState(query);
  const [type, setType] = useState<"all" | "Movie" | "Series">(browseType === "Movie" || browseType === "Series" ? browseType : "all");
  const [page, setPage] = useState(Number(params.get("page") || 1));
  const [results, setResults] = useState<MediaItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => setInput(query), [query]);
  useEffect(() => { if (browseType === "Movie" || browseType === "Series") setType(browseType); }, [browseType]);
  useEffect(() => {
    setPage(requestedPage);
    // Do not leave the previous query's cards visible under a new query's error.
    setResults([]);
    const controller = new AbortController();
    setError("");
    if (!query.trim() && (browseType === "Movie" || browseType === "Series")) {
      setLoading(true); setError("");
      getCatalog(browseType === "Series" ? "tv-series" : "movies", requestedPage, controller.signal).then((response) => { if (!controller.signal.aborted) setResults(response.items); }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "The catalog is unavailable right now."); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
      return () => controller.abort();
    }
    if (!query.trim()) { setResults([]); setLoading(false); setError(""); return () => controller.abort(); }
    setLoading(true); setError("");
    searchTitles(query, requestedPage, controller.signal).then((response) => { if (!controller.signal.aborted) setResults(response.results); }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Search is unavailable right now."); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, browseType, requestedPage]);
  useEffect(() => setMeta(query ? `Search: ${query} · MovieBox Cinema` : "Discover · MovieBox Cinema", "Search films and series in the MovieBox catalog."), [query]);
  const filtered = results.filter((item) => type === "all" || item.type === type);
  function submit(event: React.FormEvent) { event.preventDefault(); const clean = input.trim(); if (clean) setParams({ q: clean, page: "1" }); }
  return <Shell><div className="page-wrap search-page"><PageTitle eyebrow="THE DISCOVERY DESK" title={query ? `Results for “${query}”` : browseType === "Movie" ? "Movies" : browseType === "Series" ? "Series" : "Find your next story"}><form className="large-search" onSubmit={submit}><input value={input} onChange={(event) => setInput(event.target.value)} placeholder="Try a title, genre, or feeling" aria-label="Search titles" /><button className="button button-primary" type="submit">Search <ChevronRight size={16} /></button></form></PageTitle><AdBanner placement="search" />{(query || browseType) && <div className="filter-bar"><span>{loading ? "Searching the catalog…" : `${filtered.length} result${filtered.length === 1 ? "" : "s"}`}</span><div className="filter-pills"><button className={type === "all" ? "active" : ""} onClick={() => setType("all")}>All</button><button className={type === "Movie" ? "active" : ""} onClick={() => setType("Movie")}>Movies</button><button className={type === "Series" ? "active" : ""} onClick={() => setType("Series")}>Series</button></div></div>}{error && <ErrorState message={error} onRetry={() => setParams(browseType ? { type: browseType, page: String(page) } : { q: query, page: String(page) })} />}{loading && <LoadingGrid count={8} />}{!loading && query && !error && filtered.length === 0 && <div className="empty-state"><Info size={22} /><h2>No matching signal.</h2><p>Try another spelling, a shorter phrase, or browse the shelves on home.</p></div>}{!loading && browseType && !query && !error && filtered.length === 0 && <div className="empty-state"><Info size={22} /><h2>The shelf is quiet.</h2><p>There are no titles in this catalog right now.</p></div>}{!loading && filtered.length > 0 && <div className="results-grid">{filtered.map((item) => <MovieCard key={item.subject_id} item={item} />)}</div>}{(query || browseType) && filtered.length > 0 && <div className="pagination"><button className="button button-ghost" disabled={page <= 1} onClick={() => setParams(browseType && !query ? { type: browseType, page: String(page - 1) } : { q: query, page: String(page - 1) })}>Previous</button><span>Page {page}</span><button className="button button-ghost" disabled={results.length < 24} onClick={() => setParams(browseType && !query ? { type: browseType, page: String(page + 1) } : { q: query, page: String(page + 1) })}>Next</button></div>}{!query && !browseType && <div className="search-intro"><div className="search-intro-main"><span className="eyebrow">A LITTLE INSPIRATION</span><h2>Search without<br /><em>the scroll fatigue.</em></h2><p>Find something specific, or start with a mood. Every result comes directly from the connected catalog.</p></div><div className="search-stat"><span>01</span><b>Search, then stay for the story.</b></div></div>}</div></Shell>;
}

function detailGenres(detail: DetailResponse): string[] { if (Array.isArray(detail.genre)) return detail.genre; return detail.genre ? String(detail.genre).split(/[,·]/).map((item) => item.trim()).filter(Boolean) : []; }
function AudioLanguagePicker({ dubs, value, onChange }: { dubs?: AudioTrack[]; value: string; onChange: (subjectId: string) => void }) {
  const choices = (dubs || []).filter((dub) => dub.subject_id && dub.lang_name && !/\bsub(?:title)?\b/i.test(dub.lang_name));
  if (choices.length < 2) return null;
  return <label className="season-select compact audio-language-picker"><span>Audio language</span><select value={value} onChange={(event) => onChange(event.target.value)} aria-label="Audio language">{choices.map((dub) => <option value={dub.subject_id} key={dub.subject_id}>{dub.lang_name}</option>)}</select></label>;
}
function episodeNumber(episode: Episode, fallback: number): number { return Number(episode.episode || episode.number || episode.ep || fallback); }
function episodeTitle(episode: Episode, number: number): string { return String(episode.title || episode.name || `Episode ${number}`); }
function episodeFor(seasons: Season[], season: number, episode: number): Episode { return { season, episode, number: episode, title: `Episode ${episode}` }; }

function DetailsPage({ expectedType }: { expectedType: "Movie" | "Series" }) {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const detailPath = searchParams.get("detail_path") || undefined;
  const navigate = useNavigate();
  const [detail, setDetail] = useState<DetailResponse | null>(null);
  const [episodes, setEpisodes] = useState<EpisodesResponse | null>(null);
  const [related, setRelated] = useState<MediaItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [season, setSeason] = useState(1);
  useEffect(() => {
    let active = true; setLoading(true); setError("");
    Promise.all([getDetail(id, detailPath), expectedType === "Series" ? getEpisodes(id).catch(() => null) : Promise.resolve(null), getCatalog(expectedType === "Series" ? "tv-series" : "movies").catch(() => ({ items: [] as MediaItem[], page: 1, per_page: 24, total: 0 }))]).then(([nextDetail, nextEpisodes, catalog]) => { if (!active) return; setDetail(nextDetail); setEpisodes(nextEpisodes); if (nextEpisodes?.seasons?.length) setSeason(nextEpisodes.seasons[0].season); setRelated(catalog.items.filter((item) => item.subject_id !== id).slice(0, 8)); }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "We couldn't load this title."); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id, expectedType, detailPath]);
  useEffect(() => { if (detail) setMeta(`${detail.title} · MovieBox Cinema`, valueOr(detail.description, `Watch ${detail.title} on MovieBox Cinema.`), detail.cover); }, [detail]);
  if (loading) return <Shell><div className="page-wrap detail-page"><div className="detail-loading shimmer" /><LoadingGrid count={5} /></div></Shell>;
  if (error || !detail) return <Shell><div className="page-wrap"><ErrorState message={error || "This title is not available."} onRetry={() => window.location.reload()} /></div></Shell>;
  const genres = detailGenres(detail);
  const isSeries = detail.type === "Series";
  const currentSeason = episodes?.seasons.find((item) => item.season === season) || episodes?.seasons[0];
  const count = currentSeason?.episode_count || 0;
  const episodeMeta = episodes?.episodes?.filter((episode) => Number(episode.season || season) === season) || [];
  return <Shell><div className="detail-page"><section className="detail-hero" style={detail.cover ? { backgroundImage: `linear-gradient(90deg, #090a0c 10%, rgba(9,10,12,.84) 42%, rgba(9,10,12,.5) 70%, #090a0c 100%), url(${detail.cover})` } : undefined}><div className="detail-backdrop-glow" /><div className="detail-content"><PosterImage item={detail} className="detail-poster" priority /><div className="detail-copy"><span className="eyebrow">{isSeries ? "SERIES" : "FEATURE FILM"} <span className="eyebrow-slash">/</span> MOVIEBOX</span><h1>{detail.title}</h1><div className="detail-meta">{getYear(detail) && <span><CalendarDays size={14} /> {getYear(detail)}</span>}{getRating(detail) && <span><Star size={14} fill="currentColor" /> {getRating(detail)} rating</span>}{detail.duration && <span><Clock3 size={14} /> {valueOr(detail.duration)}</span>}{detail.country && <span><Globe2 size={14} /> {detail.country}</span>}</div><p className="detail-description">{valueOr(detail.description, "A title from the MovieBox catalog, ready when you are.")}</p>{genres.length > 0 && <div className="genre-list">{genres.map((genre) => <span key={genre}>{genre}</span>)}</div>}<div className="detail-actions"><button className="button button-primary" onClick={() => navigate(getWatchPath(detail, isSeries ? season : undefined, isSeries ? 1 : undefined))}><Play size={17} fill="currentColor" /> {isSeries ? "Start series" : "Watch now"}</button><Link className="button button-ghost" to="/search"><ArrowLeft size={16} /> Back to discover</Link></div><AudioInfo dubs={detail.dubs} /></div></div></section><div className="page-wrap detail-lower"><AdBanner placement="details" />{isSeries && episodes && <section className="episode-section"><div className="section-heading"><div><span className="eyebrow">EPISODE GUIDE</span><h2>Choose your chapter</h2></div><label className="season-select"><span>Season</span><select value={season} onChange={(event) => setSeason(Number(event.target.value))}>{episodes.seasons.map((item) => <option value={item.season} key={item.season}>Season {item.season}</option>)}</select></label></div>{count === 0 ? <div className="empty-state small"><Info size={20} /><p>The upstream API didn't return episodes for this series yet.</p></div> : <div className="episode-grid">{Array.from({ length: count }, (_, index) => { const number = index + 1; const real = episodeMeta.find((episode, realIndex) => episodeNumber(episode, realIndex + 1) === number); const episode = real || episodeFor(episodes.seasons, season, number); return <Link className="episode-card" key={`${season}-${number}`} to={getWatchPath(detail, season, number)}><div className="episode-art">{(episode.thumbnail || episode.cover) ? <img src={episode.thumbnail || episode.cover || ""} alt="" loading="lazy" /> : <span>{String(number).padStart(2, "0")}</span>}<span className="episode-play"><Play size={16} fill="currentColor" /></span></div><div><span className="episode-number">S{season} · E{number}</span><strong>{episodeTitle(episode, number)}</strong>{episode.description && <p>{episode.description}</p>}</div></Link>; })}</div>}</section>}<MovieRow title="More to explore" eyebrow="KEEP THE LIGHTS LOW" items={related} /></div></div></Shell>;
}

function WatchPage() {
  const { id = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const detailPath = searchParams.get("detail_path") || undefined;
  const navigate = useNavigate();
  const requestedSeason = Number(searchParams.get("season") || 0);
  const requestedEpisode = Number(searchParams.get("episode") || 0);
  const [detail, setDetail] = useState<DetailResponse | null>(null);
  const [episodes, setEpisodes] = useState<EpisodesResponse | null>(null);
  const [audioSubjectId, setAudioSubjectId] = useState(id);
  const [season, setSeason] = useState(requestedSeason);
  const [episode, setEpisode] = useState(requestedEpisode);
  const [streams, setStreams] = useState<Stream[]>([]);
  const [captions, setCaptions] = useState<Subtitle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [allowed, setAllowed] = useState(!adConfig.preplayEnabled);
  const [retryKey, setRetryKey] = useState(0);
  const audioSwitchRef = useRef(false);
  const isSeries = detail?.type === "Series";
  const progressKey = `${id}:${isSeries ? `${season}:${episode}` : "movie"}`;
  const saved = getProgressFor(progressKey);
  useEffect(() => { setAudioSubjectId(id); }, [id]);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setStreams([]); setCaptions([]);
    if (audioSwitchRef.current) audioSwitchRef.current = false;
    else setAllowed(!adConfig.preplayEnabled);
    async function resolvePlayback() {
      const nextDetail = await getDetail(id, detailPath);
      const series = nextDetail.type === "Series";
      const nextEpisodes = series ? await getEpisodes(id).catch(() => null) : null;
      const availableSeasons = nextEpisodes?.seasons || [];
      const selectedSeason = series ? (availableSeasons.find((item) => item.season === requestedSeason) || availableSeasons[0]) : null;
      const nextSeason = series ? (selectedSeason?.season || Math.max(1, requestedSeason || 1)) : 0;
      const nextEpisode = series ? Math.min(Math.max(1, requestedEpisode || 1), selectedSeason?.episode_count || Math.max(1, requestedEpisode || 1)) : 0;
      const audioDetail = audioSubjectId === id ? nextDetail : await getDetail(audioSubjectId, detailPath);
      const [streamResponse, captionResponse] = await Promise.all([
        getStreams(audioSubjectId, nextSeason, nextEpisode, audioDetail.detail_path || undefined),
        getCaptions(audioSubjectId, nextSeason, nextEpisode).catch(() => ({ captions: [] as Subtitle[] })),
      ]);
      if (!active) return;
      if (!streamResponse.streams.length) throw new Error(`No ${audioDetail.language || "selected language"} audio stream is available for this episode.`);
      setDetail(nextDetail); setStreams(streamResponse.streams || []); setCaptions(captionResponse.captions || []); setEpisodes(nextEpisodes); setSeason(series ? nextSeason : 1); setEpisode(series ? nextEpisode : 1);
    }
    resolvePlayback().catch((cause: unknown) => { if (!active) return; if (audioSubjectId !== id) { audioSwitchRef.current = true; setAudioSubjectId(id); setError(""); return; } setError(cause instanceof Error ? cause.message : "We couldn't start this title."); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id, requestedSeason, requestedEpisode, detailPath, retryKey, audioSubjectId]);
  useEffect(() => { if (detail) setMeta(`Watch ${detail.title} · MovieBox Cinema`, valueOr(detail.description, `Now playing: ${detail.title}.`), detail.cover); }, [detail]);
  const currentSeason = episodes?.seasons.find((item) => item.season === season);
  const episodeCount = currentSeason?.episode_count || 0;
  const audioChoices = (detail?.dubs || []).filter((dub) => dub.subject_id && dub.lang_name && !/\bsub(?:title)?\b/i.test(dub.lang_name));
  function switchAudioLanguage(subjectId: string) { audioSwitchRef.current = true; setAudioSubjectId(subjectId); }
  function goEpisode(nextSeason: number, nextEpisode: number) { setSearchParams({ season: String(nextSeason), episode: String(nextEpisode), ...(detailPath ? { detail_path: detailPath } : {}) }); setSeason(nextSeason); setEpisode(nextEpisode); }
  function onEnded() { if (!isSeries || !episodes) return; if (episode < episodeCount) { goEpisode(season, episode + 1); return; } const currentIndex = episodes.seasons.findIndex((item) => item.season === season); const nextSeason = currentIndex >= 0 ? episodes.seasons[currentIndex + 1] : undefined; if (nextSeason) goEpisode(nextSeason.season, 1); }
  function save(position: number, duration: number) { if (!detail || position < 2) return; const record: ProgressRecord = { key: progressKey, subjectId: id, title: detail.title, cover: detail.cover, type: detail.type, season: isSeries ? season : undefined, episode: isSeries ? episode : undefined, position, duration, updatedAt: Date.now() }; saveProgress(record); }
  if (loading) return <Shell><div className="watch-page page-wrap"><div className="player-loading shimmer" /><div className="watch-skeleton-lines"><span className="shimmer" /><span className="shimmer short" /></div></div></Shell>;
  if (error || !detail) return <Shell><div className="watch-page page-wrap"><ErrorState message={error || "This title isn't available right now."} onRetry={() => setRetryKey((value) => value + 1)} /></div></Shell>;
  return <Shell><div className="watch-page page-wrap"><div className="watch-header"><Link to={getMediaPath(detail)} className="back-link"><ArrowLeft size={15} /> Back to {isSeries ? "series" : "details"}</Link><span className="watch-kicker">NOW PLAYING</span></div><AdBanner placement="watch" /><section className="watch-stage"><div className="watch-player-wrap"><VideoPlayer streams={streams} captions={captions} allowed={allowed} initialPosition={saved?.position || 0} onProgress={save} onRetry={() => setRetryKey((value) => value + 1)} onEnded={onEnded} />{streams.length > 0 && !allowed && <PrePlayAdGate onUnlocked={() => setAllowed(true)} />}</div><div className="watch-info"><div><span className="eyebrow">{isSeries ? `S${season} · E${episode}` : "FEATURE FILM"}</span><h1>{detail.title}</h1><p>{valueOr(detail.description, "Press play when you are ready.")}</p></div><div className="watch-actions">{isSeries && episodes && <><label className="season-select compact"><span>Season</span><select value={season} onChange={(event) => goEpisode(Number(event.target.value), 1)}>{episodes.seasons.map((item) => <option value={item.season} key={item.season}>Season {item.season}</option>)}</select></label><label className="season-select compact"><span>Episode</span><select value={episode} onChange={(event) => goEpisode(season, Number(event.target.value))}>{Array.from({ length: episodeCount }, (_, index) => <option value={index + 1} key={index + 1}>Episode {index + 1}</option>)}</select></label></>}{audioChoices.length > 0 && <AudioLanguagePicker dubs={detail.dubs} value={audioSubjectId} onChange={switchAudioLanguage} />}</div></div>{isSeries && episodes && <EpisodeNavigator current={episode} total={episodeCount} onPrevious={() => episode > 1 && goEpisode(season, episode - 1)} onNext={() => episode < episodeCount && goEpisode(season, episode + 1)} />}</section><section className="watch-details"><div><span className="eyebrow">A FEW MORE DETAILS</span><div className="watch-detail-meta">{getYear(detail) && <span><CalendarDays size={14} /> {getYear(detail)}</span>}{getRating(detail) && <span><Star size={14} fill="currentColor" /> {getRating(detail)}</span>}{detail.language && <span><Languages size={14} /> {detail.language}</span>}{detail.country && <span><Globe2 size={14} /> {detail.country}</span>}</div></div><div className="watch-note"><Check size={16} /><span>{captions.length ? `${captions.length} subtitle track${captions.length > 1 ? "s" : ""} available` : "No subtitles returned for this title"}</span></div></section></div></Shell>;
}

export { HomePage, SearchPage, DetailsPage, WatchPage, Shell };

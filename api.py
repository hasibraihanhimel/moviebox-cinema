import base64
import atexit
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import ssl
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse

try:
    from curl_cffi import requests as cr
except ImportError:
    cr = None

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger("moviebox.playback")


def _system_ca_bundle() -> Optional[str]:
    """Add Windows' trusted roots to certifi for curl-cffi TLS verification."""
    configured_bundle = os.environ.get("CURL_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if configured_bundle or not hasattr(ssl, "enum_certificates"):
        return configured_bundle
    try:
        import certifi

        roots = [
            certificate
            for certificate, encoding, _trust in ssl.enum_certificates("ROOT")
            if encoding == "x509_asn"
        ]
        if not roots:
            return None
        handle = tempfile.NamedTemporaryFile(prefix="moviebox-ca-", suffix=".pem", delete=False)
        try:
            with open(certifi.where(), "rb") as defaults:
                handle.write(defaults.read())
            for certificate in roots:
                encoded = base64.b64encode(certificate).decode("ascii")
                handle.write(b"\n-----BEGIN CERTIFICATE-----\n")
                for offset in range(0, len(encoded), 64):
                    handle.write(encoded[offset:offset + 64].encode("ascii") + b"\n")
                handle.write(b"-----END CERTIFICATE-----\n")
        finally:
            handle.close()
        atexit.register(lambda: os.path.exists(handle.name) and os.remove(handle.name))
        return handle.name
    except (OSError, ImportError, ValueError) as exc:
        logger.warning("Could not add Windows trusted roots to the upstream CA bundle: %s", exc)
        return None


SYSTEM_CA_BUNDLE = _system_ca_bundle()

app = FastAPI(
    title="MovieBox 1080p API",
    description="REST API for MovieBox with 1080p Stream & Subtitle Extraction",
    version="2.5.0"
)

configured_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["Accept", "Content-Type", "Range"],
    expose_headers=["Accept-Ranges", "Content-Length", "Content-Range", "Content-Type", "ETag", "Last-Modified"],
)


@app.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok"}

SECRET_KEY_DEFAULT_B64 = "NzZpUmwwN3MweFNOOWpxbUVXQXQ3OUVCSlp1bElRSXNWNjRGWnIyTw=="
SECRET_KEY_ALT_B64 = "WHFuMm5uTzQxL0w5Mm8xaXVYaFNMSFRiWHZZNFo1Wlo2Mm04bVNMQQ=="
MODERN_USER_AGENT = "com.community.mbox.in/50020130 (Linux; U; Android 14; en_IN; Pixel 8; Build/UD1A.230803.041; Cronet/145.0.7582.0)"
WEB_USER_AGENT = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Mobile Safari/537.36"
API_BASE = "https://h5-api.aoneroom.com/wefeed-h5api-bff"
NATIVE_API_BASE = "https://api3.aoneroom.com"
BOOTSTRAP_HOST = "https://apig.inmoviebox.com"
BOOTSTRAP_URL = f"{BOOTSTRAP_HOST}/wefeed-mobile-bff/tab/ranking-list?tabId=0&categoryType=4516404531735022304&page=1&perPage=1"

WEB_DOMAINS = [
    "https://123moviesfree.club",
    "https://movie-box.co",
    "https://movieboxonline.net",
    "https://sflix.film",
    "https://h5-api.aoneroom.com",
    "https://netnaija.film",
    "https://movieboxhd.net",
]

PROXY_SIGNING_SECRET = os.getenv("PROXY_SIGNING_SECRET") or secrets.token_urlsafe(32)
PROXY_TOKEN_TTL_SECONDS = max(60, int(os.getenv("PROXY_TOKEN_TTL_SECONDS", "900")))
ALLOWED_MEDIA_HOSTS = {
    "aoneroom.com",
    "hakunaymatata.com",
}

def _b64decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)

def get_secret_bytes(use_alt_key: bool = False) -> bytes:
    outer = SECRET_KEY_ALT_B64 if use_alt_key else SECRET_KEY_DEFAULT_B64
    inner = _b64decode(outer).decode("utf-8")
    return _b64decode(inner)

def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()

def generate_device_id() -> str:
    return secrets.token_hex(16)

def generate_x_client_token(timestamp: int) -> str:
    ts_str = str(timestamp)
    reversed_ts = ts_str[::-1].encode("utf-8")
    return f"{ts_str},{md5_hex(reversed_ts)}"

def generate_client_info(device_id: str) -> str:
    return json.dumps(
        {
            "package_name": "com.community.mbox.in",
            "version_name": "4.0.03.0920.03",
            "version_code": 50020130,
            "os": "android",
            "os_version": "14",
            "device_id": device_id,
            "install_store": "official",
            "gaid": "1b2212c1-dadf-43c3-a0c8-bd6ce48ae22d",
            "brand": "Google",
            "model": "Pixel 8",
            "system_language": "en",
            "net": "NETWORK_WIFI",
            "region": "IN",
            "timezone": "Asia/Calcutta",
            "sp_code": "",
        },
        separators=(",", ":"),
    )

def build_canonical_string(method: str, accept: str | None, content_type: str | None, url: str, body: str | None, timestamp: int) -> str:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        pairs = []
        for segment in parsed.query.split("&"):
            if not segment:
                continue
            pieces = segment.split("=", 1)
            pairs.append((pieces[0], pieces[1] if len(pieces) > 1 else ""))
        pairs.sort(key=lambda kv: kv[0])
        canonical_query = "&".join(f"{k}={v}" for k, v in pairs)
        canonical_url = f"{path}?{canonical_query}"
    else:
        canonical_url = path

    body_bytes = body.encode("utf-8") if body is not None else None
    if body_bytes:
        body_length = str(len(body_bytes))
        body_hash = md5_hex(body_bytes[:102400])
    else:
        body_length = ""
        body_hash = ""

    return "\n".join(
        [
            method.upper(),
            accept or "",
            content_type or "",
            body_length,
            str(timestamp),
            body_hash,
            canonical_url,
        ]
    )

def generate_x_tr_signature(method: str, accept: str | None, content_type: str | None, url: str, body: str | None, timestamp: int, use_alt_key: bool = False) -> str:
    canonical = build_canonical_string(method, accept, content_type, url, body, timestamp)
    key = get_secret_bytes(use_alt_key)
    digest = hmac.new(key, canonical.encode("utf-8"), hashlib.md5).digest()
    sig_b64 = base64.b64encode(digest).decode("ascii")
    return f"{timestamp}|2|{sig_b64}"

def build_auth_headers(method: str, url: str, body: str | None = None, token: str | None = None, device_id: str | None = None, use_alt_key: bool = False, content_type: str = "application/json", accept: str = "application/json") -> Dict[str, str]:
    ts = int(time.time() * 1000)
    dev_id = device_id or generate_device_id()
    headers = {
        "user-agent": MODERN_USER_AGENT,
        "accept": accept,
        "content-type": content_type,
        "connection": "keep-alive",
        "x-client-token": generate_x_client_token(ts),
        "x-tr-signature": generate_x_tr_signature(method, accept, content_type, url, body, ts, use_alt_key),
        "x-client-info": generate_client_info(dev_id),
        "x-client-status": "0",
    }
    if token:
        headers["authorization"] = f"Bearer {token}"
    return headers

def extract_policy_resource(sign_cookie: str | None) -> str | None:
    if not sign_cookie:
        return None
    m_edge = re.search(r"Edge-Cache-Cookie=urlprefix=([^:;\s]+)", sign_cookie)
    if m_edge:
        try:
            decoded = _b64decode(m_edge.group(1)).decode("utf-8").rstrip("/")
            return decoded + "/index.mpd"
        except Exception:
            pass
    m_cf = re.search(r"CloudFront-Policy=([^;]+)", sign_cookie)
    if m_cf:
        raw = m_cf.group(1)
        for t in ("cf", "std"):
            try:
                b = raw.replace("-", "+").replace("_", "=") if t == "cf" else raw
                b += "=" * (-len(b) % 4)
                data = json.loads(base64.b64decode(b).decode("utf-8"))
                for s in data.get("Statement", []):
                    res = s.get("Resource")
                    if res:
                        return res.rstrip("*").rstrip("/") + "/index.mpd"
            except Exception:
                pass
    return None

def _media_host_allowed(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and any(host == suffix or host.endswith(f".{suffix}") for suffix in ALLOWED_MEDIA_HOSTS)

def _validated_redirect_url(url: str) -> str:
    if not _media_host_allowed(url):
        raise HTTPException(status_code=502, detail="Playback source redirected to a disallowed host.")
    return url

def _encode_playback_token(payload: Dict[str, Any]) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + PROXY_TOKEN_TTL_SECONDS
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(PROXY_SIGNING_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"

def _decode_playback_token(token: str) -> Dict[str, Any]:
    try:
        encoded, signature = token.rsplit(".", 1)
        expected = hmac.new(PROXY_SIGNING_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()) or not _media_host_allowed(str(payload.get("url", ""))):
            raise ValueError("expired or disallowed target")
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=404, detail="Playback source expired. Resolve a fresh stream.") from exc

def _normalize_stream(stream: Dict[str, Any]) -> Dict[str, Any]:
    url = str(stream.get("url") or "")
    raw_format = str(stream.get("format") or "").lower()
    raw_codec = str(stream.get("codec") or "").lower()
    is_dash = raw_format in {"dash", "mpd"} or ".mpd" in url.lower()
    is_mp4 = raw_format == "mp4" or ".mp4" in url.lower()
    has_h264 = any(value in raw_codec for value in ("h264", "avc"))
    has_hevc = any(value in raw_codec for value in ("hevc", "h265", "x265"))
    codec = "unknown" if has_h264 and has_hevc else "h264" if has_h264 else "hevc" if has_hevc else (raw_codec or "unknown")
    requires_headers = bool(stream.get("headers") or stream.get("cookie"))
    stream["format"] = "DASH" if is_dash else "MP4" if is_mp4 else (stream.get("format") or "unknown")
    stream["codec"] = codec
    stream["is_dash"] = is_dash
    stream["is_browser_playable"] = bool(is_mp4 or is_dash)
    stream["requires_headers"] = requires_headers
    stream["requires_proxy"] = True
    stream["playback_url"] = f"/api/playback/{_encode_playback_token({'url': url, 'headers': stream.get('headers') or {}, 'cookie': stream.get('cookie') or '', 'kind': 'dash' if is_dash else 'media'})}"
    return stream

class MovieBoxEngine:
    _instance = None

    def __init__(self):
        self.device_id = generate_device_id()
        self.bearer_token: Optional[str] = None
        if cr:
            self.session = cr.Session(impersonate="chrome120", verify=SYSTEM_CA_BUNDLE or True)
        else:
            self.session = httpx.Client(timeout=25, follow_redirects=True, verify=SYSTEM_CA_BUNDLE or True)

    @classmethod
    def get_instance(cls) -> "MovieBoxEngine":
        if cls._instance is None:
            cls._instance = MovieBoxEngine()
        return cls._instance

    def bootstrap_token(self, force: bool = False) -> str:
        if self.bearer_token and not force:
            return self.bearer_token
        headers = build_auth_headers("GET", BOOTSTRAP_URL, device_id=self.device_id)
        resp = self.session.get(BOOTSTRAP_URL, headers=headers, timeout=20)
        x_user = resp.headers.get("x-user")
        if not x_user:
            raise HTTPException(status_code=502, detail="Failed to bootstrap MovieBox token")
        token_data = json.loads(x_user)
        token = token_data.get("token")
        if not token:
            raise HTTPException(status_code=502, detail="Empty token in upstream header")
        self.bearer_token = token
        return token

    def api_request(self, method: str, path_or_url: str, body_dict: Optional[Dict[str, Any]] = None, use_token: bool = True, retry_auth: bool = True) -> Dict[str, Any]:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            full_url = path_or_url
        else:
            full_url = f"{NATIVE_API_BASE}{path_or_url}"
        if use_token and not self.bearer_token:
            self.bootstrap_token()
        body_str = json.dumps(body_dict, separators=(",", ":")) if body_dict is not None else None
        headers = build_auth_headers(
            method=method,
            url=full_url,
            body=body_str,
            token=self.bearer_token if use_token else None,
            device_id=self.device_id,
        )
        data_bytes = body_str.encode("utf-8") if body_str is not None else None
        if method.upper() == "POST":
            resp = self.session.post(full_url, headers=headers, data=data_bytes, timeout=25)
        else:
            resp = self.session.get(full_url, headers=headers, timeout=25)
        if resp.status_code in (401, 441) and retry_auth:
            self.bootstrap_token(force=True)
            return self.api_request(method, path_or_url, body_dict, use_token, retry_auth=False)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"API returned HTTP {resp.status_code}")
        return resp.json()

    def search(self, query: str, page: int = 1, per_page: int = 20) -> List[Dict[str, Any]]:
        # The upstream recently changed the accepted pagination fields. Try
        # the current contract first and keep the prior contract as fallback.
        variants = [
            ("/wefeed-mobile-bff/subject-api/search", {"keyword": query, "type": 0, "page": page, "pageSize": per_page}),
            ("/wefeed-mobile-bff/subject-api/search/v2", {"keyword": query, "page": page, "perPage": per_page}),
            ("/wefeed-mobile-bff/subject-api/search", {"keyword": query, "page": page, "perPage": per_page}),
        ]
        data = None
        last_error: Optional[HTTPException] = None
        for url, payload in variants:
            try:
                response = self.api_request("POST", url, body_dict=payload)
                data = response.get("data", {}) if isinstance(response, dict) else {}
                break
            except HTTPException as exc:
                last_error = exc
        if data is None:
            raise last_error or HTTPException(status_code=502, detail="Search is unavailable")
        raw_items = data.get("items", []) or data.get("list", [])
        results = []
        for item in raw_items:
            sub = item.get("subject", item)
            results.append({
                "subject_id": str(sub.get("subjectId")),
                "title": sub.get("title"),
                "type": "Movie" if sub.get("subjectType") == 1 else "Series",
                "subject_type": sub.get("subjectType"),
                "release_date": sub.get("releaseDate"),
                "imdb_rating": sub.get("imdbRatingValue"),
                "genre": sub.get("genre"),
                "cover": sub.get("cover", {}).get("url") if isinstance(sub.get("cover"), dict) else None,
                "detail_path": sub.get("detailPath"),
                "language": sub.get("language")
            })
        return results

    def get_details(self, subject_id: str) -> Dict[str, Any]:
        url = f"/wefeed-mobile-bff/subject-api/get?subjectId={subject_id}"
        data = self.api_request("GET", url).get("data", {})
        detail_url_str = data.get("detailUrl", "")
        detail_path = detail_url_str.split("/")[-1] if "/" in detail_url_str else detail_url_str
        return {
            "subject_id": str(data.get("subjectId")),
            "title": data.get("title"),
            "subject_type": data.get("subjectType"),
            "type": "Movie" if data.get("subjectType") == 1 else "Series",
            "description": data.get("description"),
            "release_date": data.get("releaseDate"),
            "duration": data.get("duration"),
            "imdb_rating": data.get("imdbRatingValue"),
            "genre": data.get("genre"),
            "country": data.get("countryName"),
            "language": data.get("language"),
            "cover": data.get("cover", {}).get("url") if isinstance(data.get("cover"), dict) else None,
            "detail_path": detail_path,
            "dubs": [
                {
                    "subject_id": str(d.get("subjectId")),
                    "lang_name": d.get("lanName"),
                    "language": d.get("language"),
                }
                for d in data.get("dubs", [])
                if isinstance(d, dict)
            ]
        }

    def get_episodes(self, subject_id: str) -> List[Dict[str, Any]]:
        url = f"/wefeed-mobile-bff/subject-api/season-info?subjectId={subject_id}"
        res = self.api_request("GET", url)
        seasons = res.get("data", {}).get("seasons", [])
        season_list = []
        for s in seasons:
            season_list.append({
                "season": s.get("se", 1),
                "episode_count": s.get("maxEp", 1),
                "resolutions": s.get("resolutions", [])
            })
        return season_list

    def get_streams(self, subject_id: str, season: int = 0, episode: int = 0, detail_path: Optional[str] = None) -> List[Dict[str, Any]]:
        if not detail_path:
            try:
                details = self.get_details(subject_id)
                detail_path = details.get("detail_path", "")
            except Exception:
                detail_path = ""
        token = self.bootstrap_token()
        streams: List[Dict[str, Any]] = []
        seen_urls = set()

        for domain in WEB_DOMAINS:
            play_url = (
                f"{domain}/wefeed-h5api-bff/subject/play?"
                f"subjectId={subject_id}&se={season}&ep={episode}&detailPath={detail_path}&"
                f"streamSignType=1&supportCodecs%5Bhevc%5D=0&supportCodecs%5Bh264%5D=1"
            )
            referer = f"{domain}/movies/{detail_path}?id={subject_id}&type=/movie/detail&detailSe=&detailEp=&lang=en"
            headers = {
                "User-Agent": WEB_USER_AGENT,
                "Referer": referer,
                "Accept": "application/json",
                "x-client-info": '{"timezone":"Asia/Calcutta"}',
                "x-request-lang": "en",
                "x-vip-restrict": "0",
                "x-no-high-risk-restrict": "0",
                "x-source": "moviebox",
                "Authorization": f"Bearer {token}",
            }
            try:
                resp = self.session.get(play_url, headers=headers, timeout=12)
                if resp.status_code != 200:
                    continue
                data = resp.json().get("data", {})
                raw_streams = data.get("streams", [])
                dash_items = data.get("dash", [])

                if raw_streams or dash_items:
                    for st in raw_streams:
                        url = st.get("url")
                        if not url or url in seen_urls:
                            continue
                        seen_urls.add(url)
                        res_str = str(st.get("resolutions", "unknown"))
                        size_bytes = int(st.get("size") or 0)
                        size_mb = size_bytes / (1024 * 1024) if size_bytes else 0
                        quality_val = 0
                        for q in (2160, 1440, 1080, 720, 480, 360, 240):
                            if str(q) in res_str:
                                quality_val = q
                                break
                        streams.append({
                            "source": f"Web CDN ({domain})",
                            "format": st.get("format", "MP4"),
                            "quality": f"{quality_val}p" if quality_val else res_str,
                            "quality_value": quality_val,
                            "resolution_tag": res_str,
                            "url": url,
                            "size_mb": round(size_mb, 1),
                            "codec": st.get("codecName"),
                            "duration_sec": st.get("duration"),
                            "headers": {
                                "User-Agent": "Mozilla/5.0",
                                "Referer": f"{domain}/",
                                "Origin": domain,
                            }
                        })

                    for d in dash_items:
                        url = d.get("url")
                        cookie = d.get("signCookie")
                        extracted_mpd = extract_policy_resource(cookie) if cookie else None
                        final_url = extracted_mpd or url
                        if final_url and final_url not in seen_urls:
                            seen_urls.add(final_url)
                            res_tag = str(d.get("resolutions", "adaptive"))
                            streams.append({
                                "source": f"Web DASH ({domain})",
                                "format": "DASH",
                                "quality": "1080p (Adaptive)" if "1080" in res_tag else "Adaptive",
                                "quality_value": 1080 if "1080" in res_tag else 999,
                                "resolution_tag": res_tag,
                                "url": final_url,
                                "size_mb": 0,
                                "codec": "unknown",
                                "duration_sec": None,
                                "cookie": cookie,
                                "headers": {
                                    "User-Agent": "Mozilla/5.0",
                                    "Referer": f"{domain}/",
                                    "Origin": domain,
                                }
                            })
            except Exception:
                logger.warning("web provider stream probe failed", extra={"provider": domain, "subject_id": subject_id}, exc_info=True)

        try:
            native_url = f"/wefeed-mobile-bff/subject-api/play-info?subjectId={subject_id}&se={season}&ep={episode}"
            native_data = self.api_request("GET", native_url).get("data", {})
            for st in native_data.get("streams", []):
                raw_url = st.get("url")
                if not raw_url or "b164fbfb4347792950bdfbfb563d39d9" in raw_url:
                    continue
                cookie = st.get("signCookie")
                policy_url = extract_policy_resource(cookie) if cookie else None
                final_url = policy_url or raw_url
                if final_url in seen_urls:
                    continue
                seen_urls.add(final_url)
                res_str = str(st.get("resolutions", ""))
                quality_val = 0
                for q in (2160, 1440, 1080, 720, 480, 360, 240):
                    if str(q) in res_str:
                        quality_val = q
                        break
                size_bytes = int(st.get("size") or 0)
                size_mb = size_bytes / (1024 * 1024) if size_bytes else 0
                streams.append({
                    "source": "Mobile Native CDN",
                    "format": "DASH" if (policy_url or ".mpd" in final_url) else st.get("format", "MP4"),
                    "quality": f"{quality_val}p" if quality_val else (res_str or "Default"),
                    "quality_value": quality_val,
                    "resolution_tag": res_str,
                    "url": final_url,
                    "size_mb": round(size_mb, 1),
                    "codec": st.get("codecName"),
                    "duration_sec": st.get("duration"),
                    "headers": {
                        "User-Agent": MODERN_USER_AGENT,
                        "Referer": "https://api3.aoneroom.com/",
                    }
                })
        except Exception:
            logger.warning("native provider stream probe failed", extra={"provider": "native", "subject_id": subject_id}, exc_info=True)

        streams = [stream for stream in streams if _media_host_allowed(str(stream.get("url") or ""))]
        for stream in streams:
            _normalize_stream(stream)
        def compatibility(stream: Dict[str, Any]) -> int:
            if stream.get("format") == "MP4" and stream.get("codec") == "h264":
                return 4
            if stream.get("format") == "DASH" and stream.get("codec") == "h264":
                return 3
            if stream.get("format") == "MP4" and stream.get("codec") == "hevc":
                return 2
            if stream.get("format") == "DASH":
                return 1
            return 0
        streams.sort(key=lambda s: (compatibility(s), int(s.get("quality_value") or 0)), reverse=True)
        return streams

    def get_subtitles(self, subject_id: str, stream_id: Optional[str] = None, season: int = 0, episode: int = 0) -> List[Dict[str, Any]]:
        subs: List[Dict[str, Any]] = []
        seen_urls = set()

        if not stream_id:
            try:
                native_url = f"/wefeed-mobile-bff/subject-api/play-info?subjectId={subject_id}&se={season}&ep={episode}"
                raw_streams = self.api_request("GET", native_url).get("data", {}).get("streams", [])
                if raw_streams and raw_streams[0].get("id"):
                    stream_id = str(raw_streams[0]["id"])
            except Exception:
                pass

        def _add_sub(item: Dict[str, Any]):
            u = item.get("url")
            if u and u not in seen_urls:
                seen_urls.add(u)
                caption_url = u
                if _media_host_allowed(str(u)):
                    caption_url = f"/api/playback/{_encode_playback_token({'url': str(u), 'headers': {}, 'cookie': '', 'kind': 'caption'})}"
                subs.append({
                    "id": str(item.get("id", "")),
                    "language": item.get("lanName") or item.get("language") or "English",
                    "code": item.get("lan", ""),
                    "url": caption_url,
                    "size": item.get("size")
                })

        if stream_id:
            try:
                url = f"/wefeed-mobile-bff/subject-api/get-stream-captions?subjectId={subject_id}&streamId={stream_id}"
                data = self.api_request("GET", url).get("data", {})
                captions = data if isinstance(data, list) else data.get("extCaptions") or data.get("captions") or []
                for s in captions:
                    if isinstance(s, dict):
                        _add_sub(s)
            except Exception:
                pass

        try:
            res_id = stream_id or "0"
            url = f"/wefeed-mobile-bff/subject-api/get-ext-captions?subjectId={subject_id}&resourceId={res_id}&episode={episode}"
            data = self.api_request("GET", url).get("data", {})
            ext = data if isinstance(data, list) else data.get("extCaptions") or data.get("captions") or []
            for s in ext:
                if isinstance(s, dict):
                    _add_sub(s)
        except Exception:
            pass

        return subs

engine = MovieBoxEngine.get_instance()

def validate_subject_id(subject_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,160}", subject_id):
        raise HTTPException(status_code=400, detail="Invalid subject ID")
    return subject_id

def _gateway_headers(payload: Dict[str, Any], request: Request) -> Dict[str, str]:
    headers = {str(key): str(value) for key, value in (payload.get("headers") or {}).items() if key in {"User-Agent", "Referer", "Origin", "Accept"}}
    if payload.get("cookie"):
        headers["Cookie"] = str(payload["cookie"])
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    return headers

def _open_gateway_response(client: httpx.Client, method: str, target: str, headers: Dict[str, str]) -> tuple[httpx.Response, str]:
    current = _validated_redirect_url(target)
    for _ in range(5):
        response = client.send(client.build_request(method, current, headers=headers), stream=method != "HEAD", follow_redirects=False)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response, current
        location = response.headers.get("location")
        response.close()
        if not location:
            break
        current = _validated_redirect_url(urljoin(current, location))
    client.close()
    raise HTTPException(status_code=502, detail="Playback source redirected too many times.")

def _stream_gateway_response(payload: Dict[str, Any], request: Request) -> Response | StreamingResponse:
    target = str(payload.get("url") or "")
    if not _media_host_allowed(target):
        raise HTTPException(status_code=502, detail="Playback source is not an approved media host.")
    client = httpx.Client(timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0), verify=SYSTEM_CA_BUNDLE or True)
    upstream, final_url = _open_gateway_response(client, request.method, target, _gateway_headers(payload, request))
    if upstream.status_code not in (200, 206):
        status = upstream.status_code
        upstream.close(); client.close()
        logger.warning("media gateway upstream failure", extra={"status": status, "host": urlsplit(target).hostname})
        raise HTTPException(status_code=502, detail=f"Media source returned HTTP {status}")

    passthrough = {key: upstream.headers[key] for key in ("content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified") if key in upstream.headers}
    passthrough["Cache-Control"] = "no-store"
    if request.method == "HEAD":
        upstream.close(); client.close()
        return Response(status_code=upstream.status_code, headers=passthrough, media_type=upstream.headers.get("content-type"))

    def body():
        try:
            for chunk in upstream.iter_bytes(1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close(); client.close()

    return StreamingResponse(body(), status_code=upstream.status_code, headers=passthrough, media_type=upstream.headers.get("content-type", "application/octet-stream"))

def _rewrite_dash_manifest(manifest: str, gateway_base: str) -> str:
    base_pattern = r"(<BaseURL(?:\s[^>]*)?>).*?(</BaseURL>)"
    if re.search(r"<BaseURL(?:\s[^>]*)?>", manifest, flags=re.IGNORECASE):
        return re.sub(base_pattern, rf"\1{gateway_base}\2", manifest, flags=re.IGNORECASE | re.DOTALL)
    return re.sub(r"(<MPD\b[^>]*>)", rf"\1<BaseURL>{gateway_base}</BaseURL>", manifest, count=1, flags=re.IGNORECASE)

@app.api_route("/api/playback/{token}", methods=["GET", "HEAD"])
def playback_manifest_or_media(token: str, request: Request):
    payload = _decode_playback_token(token)
    if payload.get("kind") != "dash":
        return _stream_gateway_response(payload, request)
    target = str(payload.get("url") or "")
    client = httpx.Client(timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0), verify=SYSTEM_CA_BUNDLE or True)
    upstream, final_url = _open_gateway_response(client, request.method, target, _gateway_headers(payload, request))
    if upstream.status_code != 200:
        upstream.close(); client.close()
        logger.warning("DASH manifest upstream failure", extra={"status": upstream.status_code, "host": urlsplit(target).hostname})
        raise HTTPException(status_code=502, detail=f"DASH manifest returned HTTP {upstream.status_code}")
    if request.method == "HEAD":
        headers = {key: upstream.headers[key] for key in ("content-type", "content-length", "etag", "last-modified") if key in upstream.headers}
        upstream.close(); client.close()
        return Response(status_code=200, headers=headers, media_type="application/dash+xml")
    manifest = upstream.text
    upstream.close(); client.close()
    gateway_base = f"/api/playback/{token}/"
    manifest = _rewrite_dash_manifest(manifest, gateway_base)
    return Response(content=manifest, media_type="application/dash+xml", headers={"Cache-Control": "no-store"})

@app.api_route("/api/playback/{token}/{asset:path}", methods=["GET", "HEAD"])
def playback_dash_asset(token: str, asset: str, request: Request):
    payload = _decode_playback_token(token)
    if payload.get("kind") != "dash":
        raise HTTPException(status_code=404, detail="Playback asset not found")
    target = urljoin(str(payload.get("url") or ""), asset)
    if not _media_host_allowed(target):
        raise HTTPException(status_code=502, detail="Playback asset resolved to a disallowed host.")
    child = dict(payload); child["url"] = target
    return _stream_gateway_response(child, request)

@app.get("/", include_in_schema=False)
def frontend_root():
    """Serve the built React app at the public site root."""
    index = os.path.join(os.path.dirname(__file__), "dist", "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend build not found")

@app.get("/search")
def search(q: str = Query(..., min_length=1, max_length=120), page: int = Query(1, ge=1, le=100), per_page: int = Query(20, ge=1, le=100)):
    try:
        results = engine.search(q, page=page, per_page=per_page)
    except HTTPException:
        # The provider's full search endpoint occasionally fails while its
        # autocomplete endpoint remains available. Keep the search page useful
        # in that case, while preserving the original error if both fail.
        if page != 1:
            raise
        try:
            suggestions = search_suggest(q).get("suggestions", [])
            results = [
                {
                    "subject_id": item.get("subject_id"),
                    "title": item.get("title"),
                    "type": item.get("type", "Movie"),
                    "subject_type": 2 if item.get("type") == "Series" else 1,
                    "detail_path": item.get("slug"),
                }
                for item in suggestions
                if item.get("title") and item.get("subject_id")
                and str(item.get("subject_id")).lower() not in {"none", "null", "undefined"}
            ]
            if not results:
                raise
        except Exception:
            raise
    return {"query": q, "page": page, "results": results}

@app.get("/search/suggest")
def search_suggest(q: str = Query(..., min_length=1, max_length=120)):
    url = f"{API_BASE}/subject/search-suggest"
    headers = {"User-Agent": WEB_USER_AGENT, "Referer": "https://moviebox.ph/"}
    try:
        resp = engine.session.post(url, headers=headers, json={"keyword": q, "perPage": 10}, timeout=15)
        raw = resp.json().get("data", {}).get("items", [])
    except Exception:
        raw = []
    suggestions = []
    for item in raw:
        sub = item.get("subject") or {}
        suggestions.append({
            "title": sub.get("title") or item.get("word") or item.get("title"),
            "slug": sub.get("detailPath") or item.get("detailPath"),
            "subject_id": str(sub.get("subjectId") or item.get("subjectId")),
            "type": "Series" if sub.get("subjectType") == 2 else "Movie"
        })
    return {"suggestions": suggestions}

@app.get("/detail/{subject_id}")
def get_detail(subject_id: str):
    return engine.get_details(validate_subject_id(subject_id))

@app.get("/episodes/{subject_id}")
def get_episodes(subject_id: str):
    subject_id = validate_subject_id(subject_id)
    return {"subject_id": subject_id, "seasons": engine.get_episodes(subject_id)}

@app.get("/api/stream/{subject_id}")
def get_streams(subject_id: str, se: int = Query(0, ge=0, le=100), ep: int = Query(0, ge=0, le=1000), detail_path: Optional[str] = Query(None, max_length=180)):
    subject_id = validate_subject_id(subject_id)
    streams = engine.get_streams(subject_id, season=se, episode=ep, detail_path=detail_path)
    return {
        "subject_id": subject_id,
        "season": se,
        "episode": ep,
        "count": len(streams),
        "streams": streams
    }

@app.get("/api/stream/{subject_id}/captions")
def get_captions(subject_id: str, se: int = Query(0, ge=0, le=100), ep: int = Query(0, ge=0, le=1000), stream_id: Optional[str] = Query(None, max_length=180)):
    subject_id = validate_subject_id(subject_id)
    subs = engine.get_subtitles(subject_id, stream_id=stream_id, season=se, episode=ep)
    return {
        "subject_id": subject_id,
        "season": se,
        "episode": ep,
        "count": len(subs),
        "captions": subs
    }

def _get_ranked_catalog(tab_id: int, page: int, per_page: int, expected_type: int) -> Dict[str, Any]:
    """Read the correctly categorized MovieBox tab instead of its stale generic filter feed."""
    query = urlencode({"tabId": tab_id, "categoryType": tab_id, "page": page, "perPage": per_page})
    response = engine.api_request("GET", f"/wefeed-mobile-bff/tab/ranking-list?{query}", use_token=False)
    data = response.get("data", {}) if isinstance(response, dict) else {}
    pager = data.get("pager", {}) if isinstance(data, dict) else {}
    raw_items = data.get("subjects") or data.get("items") or data.get("list") or []
    items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        subject = item.get("subject") if isinstance(item.get("subject"), dict) else item
        if subject.get("subjectType") != expected_type:
            continue
        subject_id = subject.get("subjectId") or subject.get("subject_id") or subject.get("id")
        title = subject.get("title") or subject.get("name")
        if not subject_id or not title:
            continue
        cover = subject.get("cover")
        items.append({
            "title": title,
            "subject_id": str(subject_id),
            "type": "Series" if expected_type == 2 else "Movie",
            "subject_type": expected_type,
            "detail_path": subject.get("detailPath") or subject.get("detail_path"),
            "cover": cover.get("url") if isinstance(cover, dict) else cover,
            "rating": subject.get("imdbRatingValue") or subject.get("rating"),
            "year": str(subject.get("releaseDate") or subject.get("year") or "")[:4] or None,
            "genre": subject.get("genre"),
        })
    has_more = bool(pager.get("hasMore")) if isinstance(pager, dict) else False
    reported_total = int(pager.get("totalCount") or 0) if isinstance(pager, dict) else 0
    total = reported_total or (page * per_page + 1 if has_more else (page - 1) * per_page + len(items))
    return {"page": page, "per_page": per_page, "total": total, "items": items}

@app.get("/movies")
def get_movies(page: int = Query(1, ge=1, le=100), per_page: int = Query(24, ge=1, le=100), sort: str = Query("RECOMMEND", max_length=30)):
    return _get_ranked_catalog(tab_id=2, page=page, per_page=per_page, expected_type=1)

@app.get("/tv-series")
def get_tv_series(page: int = Query(1, ge=1, le=100), per_page: int = Query(24, ge=1, le=100), sort: str = Query("RECOMMEND", max_length=30)):
    return _get_ranked_catalog(tab_id=5, page=page, per_page=per_page, expected_type=2)

@app.get("/home")
def get_home():
    url = f"{API_BASE}/home?host=moviebox.ph"
    resp = engine.session.get(url, headers={"User-Agent": WEB_USER_AGENT, "Referer": "https://moviebox.ph/"}, timeout=20)
    return resp.json()

@app.get("/{frontend_path:path}", include_in_schema=False)
def frontend_fallback(frontend_path: str):
    if frontend_path == "api" or frontend_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found")
    dist = os.path.join(os.path.dirname(__file__), "dist")
    requested = os.path.abspath(os.path.join(dist, frontend_path))
    if os.path.commonpath([dist, requested]) == dist and os.path.isfile(requested):
        return FileResponse(requested)
    index = os.path.join(dist, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend build not found")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)

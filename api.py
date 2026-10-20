import base64
import atexit
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode, urljoin, urlsplit

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
WEB_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
API_BASE = "https://h5-api.aoneroom.com/wefeed-h5api-bff"
NATIVE_API_BASE = "https://api3.aoneroom.com"
BOOTSTRAP_HOST = "https://apig.inmoviebox.com"
BOOTSTRAP_URL = f"{BOOTSTRAP_HOST}/wefeed-mobile-bff/tab/ranking-list?tabId=0&categoryType=4516404531735022304&page=1&perPage=1"

WEB_DOMAINS = [
    "https://h5-api.aoneroom.com",
    "https://movie-box.co",
    "https://movieboxonline.net",
    "https://sflix.film",
]
H5_PROVIDER_HOSTS = [
    "moviebox.ph",
    "movie-box.co",
    "movieboxonline.net",
    "sflix.film",
]

# A per-process random fallback breaks signed playback URLs when Render routes
# manifest and segment requests to different workers or restarts the process.
# Honor an explicit deployment secret; otherwise use a stable app-specific key.
PROXY_SIGNING_SECRET = os.getenv("PROXY_SIGNING_SECRET") or hashlib.sha256(
    f"moviebox-playback-proxy-v1:{SECRET_KEY_DEFAULT_B64}".encode("utf-8")
).hexdigest()
# Playback URLs are also used by the browser for every DASH segment request.
# Keep their signatures alive for a full feature film, including pauses.
PROXY_TOKEN_TTL_SECONDS = max(60, int(os.getenv("PROXY_TOKEN_TTL_SECONDS", "21600")))
COMPAT_TRANSCODE_LIMIT = max(1, int(os.getenv("COMPAT_TRANSCODE_LIMIT", "1")))
_COMPAT_TRANSCODE_SLOTS = threading.BoundedSemaphore(COMPAT_TRANSCODE_LIMIT)
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
    raw_format = str(stream.get("format") or "").split(";", 1)[0].strip().lower()
    raw_codec = str(stream.get("codec") or "").lower()
    if not raw_codec or raw_codec == "unknown":
        path = url.lower()
        raw_codec = "hevc" if any(value in path for value in ("h265", "h.265", "hevc", "hvc1", "hev1", "x265")) else "h264" if any(value in path for value in ("h264", "avc")) else ""
    is_dash = raw_format in {"dash", "mpd", "application/dash+xml"} or ".mpd" in url.lower()
    is_mp4 = raw_format in {"mp4", "video/mp4", "application/mp4"} or ".mp4" in url.lower()
    has_h264 = any(value in raw_codec for value in ("h264", "avc"))
    has_hevc = any(value in raw_codec for value in ("hevc", "h265", "h.265", "x265", "hvc1", "hev1"))
    codec = "unknown" if has_h264 and has_hevc else "h264" if has_h264 else "hevc" if has_hevc else (raw_codec or "unknown")
    requires_headers = bool(stream.get("headers") or stream.get("cookie"))
    stream["format"] = "DASH" if is_dash else "MP4" if is_mp4 else (stream.get("format") or "unknown")
    stream["codec"] = codec
    stream["is_dash"] = is_dash
    stream["is_browser_playable"] = bool(is_mp4 or is_dash)
    stream["requires_headers"] = requires_headers
    stream["requires_proxy"] = True
    playback_token = _encode_playback_token({"url": url, "headers": stream.get("headers") or {}, "cookie": stream.get("cookie") or "", "kind": "dash" if is_dash else "media", "mime_type": "application/dash+xml" if is_dash else "video/mp4" if is_mp4 else ""})
    stream["playback_url"] = f"/api/playback/{playback_token}"
    if is_mp4 or is_dash:
        stream["compatibility_url"] = f"/api/playback/{playback_token}/compat"
    return stream


def _search_items(data: Any) -> List[Dict[str, Any]]:
    """Accept the list and pager shapes used by the upstream search endpoints."""
    current = data
    for _ in range(3):
        if isinstance(current, list):
            return [item for item in current if isinstance(item, dict)]
        if not isinstance(current, dict):
            return []
        empty_list = None
        for key in ("items", "subjects", "list", "results", "subjectList", "contentList", "movie"):
            value = current.get(key)
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, dict)]
                if items:
                    return items
                empty_list = items
        nested = current.get("data")
        if not isinstance(nested, (dict, list)):
            return empty_list or []
        current = nested
    return []


def _upstream_data(response: Any) -> Any:
    """Unwrap the common H5 BFF response envelope without assuming a fixed depth."""
    current = response
    for _ in range(4):
        if not isinstance(current, dict) or "data" not in current:
            break
        current = current["data"]
    return current


def _cover_url(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("url") or value.get("coverUrl") or value.get("path")
    return value if isinstance(value, str) else None


def _subject_type(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

class MovieBoxEngine:
    _instance = None

    def __init__(self):
        self.device_id = generate_device_id()
        self.bearer_token: Optional[str] = None
        self.h5_tokens: Dict[str, str] = {}
        self.active_h5_host = "moviebox.ph"
        self.player_domain: Optional[str] = None
        self.player_domain_host: Optional[str] = None
        self.player_domain_expires = 0.0
        if cr:
            self.session = cr.Session(impersonate="chrome120", verify=SYSTEM_CA_BUNDLE or True)
        else:
            self.session = httpx.Client(timeout=25, follow_redirects=True, verify=SYSTEM_CA_BUNDLE or True)

    @classmethod
    def get_instance(cls) -> "MovieBoxEngine":
        if cls._instance is None:
            cls._instance = MovieBoxEngine()
        return cls._instance

    def bootstrap_token(self, force: bool = False, timeout: float = 20) -> str:
        if self.bearer_token and not force:
            return self.bearer_token
        headers = build_auth_headers("GET", BOOTSTRAP_URL, device_id=self.device_id)
        resp = self.session.get(BOOTSTRAP_URL, headers=headers, timeout=timeout)
        x_user = resp.headers.get("x-user")
        if not x_user:
            # Render regions can be blocked by the native bootstrap host even
            # while the public H5 service remains available.
            try:
                self.h5_request("GET", "/home", params={"host": self.active_h5_host}, timeout=min(timeout, 3))
            except Exception:
                pass
            if self.bearer_token:
                return self.bearer_token
            raise HTTPException(status_code=502, detail="Failed to bootstrap MovieBox token")
        token_data = json.loads(x_user)
        token = token_data.get("token")
        if not token:
            raise HTTPException(status_code=502, detail="Empty token in upstream header")
        self.bearer_token = token
        return token

    def api_request(self, method: str, path_or_url: str, body_dict: Optional[Dict[str, Any]] = None, use_token: bool = True, retry_auth: bool = True, timeout: float = 25) -> Dict[str, Any]:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            full_url = path_or_url
        else:
            full_url = f"{NATIVE_API_BASE}{path_or_url}"
        if use_token and not self.bearer_token:
            self.bootstrap_token(timeout=timeout)
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
            resp = self.session.post(full_url, headers=headers, data=data_bytes, timeout=timeout)
        else:
            resp = self.session.get(full_url, headers=headers, timeout=timeout)
        if resp.status_code in (401, 441) and retry_auth:
            self.bootstrap_token(force=True, timeout=timeout)
            return self.api_request(method, path_or_url, body_dict, use_token, retry_auth=False, timeout=timeout)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"API returned HTTP {resp.status_code}")
        return resp.json()

    def h5_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        *,
        host: Optional[str] = None,
        referer: Optional[str] = None,
        origin: Optional[str] = None,
        timeout: float = 20,
        retry_auth: bool = True,
    ) -> Any:
        """Call the H5 BFF with its per-host guest session and provider context."""
        provider_host = host or str((params or {}).get("host") or self.active_h5_host)
        query = dict(params or {})
        query.setdefault("host", provider_host)
        clean_path = path.split("?", 1)[0].rstrip("/") or "/"

        guest_token = self.h5_tokens.get(provider_host)
        if clean_path != "/home" and not guest_token:
            try:
                self.h5_request(
                    "GET", "/home", params={"host": provider_host}, host=provider_host,
                    timeout=min(timeout, 3), retry_auth=False,
                )
                guest_token = self.h5_tokens.get(provider_host)
            except Exception as exc:
                logger.info("H5 guest token bootstrap unavailable for %s: %s", provider_host, exc)

        url = f"{API_BASE}/{path.lstrip('/')}"
        site_origin = f"https://{provider_host}"
        headers = {
            "User-Agent": WEB_USER_AGENT,
            "Referer": referer or f"{site_origin}/",
            "Origin": origin or site_origin,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "x-client-info": json.dumps({"timezone": "Asia/Dhaka"}, separators=(",", ":")),
            "x-request-lang": "en",
            "x-vip-restrict": "0",
            "x-no-high-risk-restrict": "0",
            "sec-ch-ua": '"Chromium";v="120", "Google Chrome";v="120", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        }
        if guest_token:
            headers["Authorization"] = f"Bearer {guest_token}"
        if method.upper() == "POST":
            response = self.session.post(url, params=query, headers=headers, json=body or {}, timeout=timeout)
        else:
            response = self.session.get(url, params=query, headers=headers, timeout=timeout)
        if response.status_code in (401, 403, 441) and guest_token and retry_auth:
            self.h5_tokens.pop(provider_host, None)
            try:
                self.h5_request(
                    "GET", "/home", params={"host": provider_host}, host=provider_host,
                    timeout=min(timeout, 3), retry_auth=False,
                )
                return self.h5_request(
                    method, path, params=query, body=body, host=provider_host,
                    referer=referer, origin=origin, timeout=timeout, retry_auth=False,
                )
            except Exception as exc:
                logger.info("H5 guest token refresh unavailable for %s: %s", provider_host, exc)
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Web catalog returned HTTP {response.status_code}")
        self.active_h5_host = provider_host
        x_user = response.headers.get("x-user")
        token = None
        if x_user:
            try:
                token_data = json.loads(x_user)
                token = token_data.get("token") if isinstance(token_data, dict) else None
            except (TypeError, json.JSONDecodeError):
                logger.debug("Ignoring malformed H5 guest token header")
        if not token:
            cookie = response.headers.get("set-cookie", "")
            match = re.search(r'(?:^|[,;\s])token=([^;,"\s]+)', cookie)
            token = match.group(1) if match else None
        if token:
            self.h5_tokens[provider_host] = str(token)
            if not self.bearer_token:
                self.bearer_token = str(token)
        return response.json()

    def search(self, query: str, page: int = 1, per_page: int = 20) -> List[Dict[str, Any]]:
        # The H5 API requires a guest session and a mirror host. Fail over across
        # its known hosts because availability and search policy vary by region.
        payload = {"keyword": query, "page": page, "perPage": per_page}
        last_h5_error: Optional[Exception] = None
        for provider_host in H5_PROVIDER_HOSTS:
            try:
                response = self.h5_request(
                    "POST", "/subject/search", params={"host": provider_host},
                    body=payload, host=provider_host, timeout=5,
                )
                data = _search_items(_upstream_data(response))
                if data:
                    return self._map_subjects(data)
            except Exception as exc:
                last_h5_error = exc
                logger.info("H5 search unavailable on %s: %s", provider_host, exc)
        if last_h5_error:
            logger.info("All H5 search providers were exhausted; trying the signed mobile API")
        # Do not start a second token bootstrap when Render could not establish
        # the web session; it only adds a long wait before the same 502.
        if not self.bearer_token:
            raise last_h5_error or HTTPException(status_code=502, detail="Search is unavailable")
        # The upstream recently changed the accepted pagination fields. Try
        # the current contract first and keep the prior contract as fallback.
        variants = [
            ("/wefeed-mobile-bff/subject-api/search", {"keyword": query, "type": 0, "page": page, "pageSize": per_page}),
            ("/wefeed-mobile-bff/subject-api/search/v2", {"keyword": query, "page": page, "perPage": per_page}),
            ("/wefeed-mobile-bff/subject-api/search", {"keyword": query, "page": page, "perPage": per_page}),
        ]
        data = None
        first_empty_data = None
        last_error: Optional[HTTPException] = None
        for url, payload in variants:
            try:
                response = self.api_request("POST", url, body_dict=payload, timeout=6)
                candidate = response.get("data", response) if isinstance(response, dict) else response
                items = _search_items(candidate)
                if items:
                    data = items
                    break
                if first_empty_data is None:
                    first_empty_data = items
            except HTTPException as exc:
                last_error = exc
            except Exception as exc:
                logger.warning("search provider request failed", extra={"endpoint": url}, exc_info=True)
                last_error = HTTPException(status_code=502, detail="Search is unavailable")
        if data is None and first_empty_data is not None:
            data = first_empty_data
        if data is None:
            raise last_error or HTTPException(status_code=502, detail="Search is unavailable")
        return self._map_subjects(data)

    @staticmethod
    def _map_subjects(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []
        for item in data:
            if not isinstance(item, dict):
                continue
            sub = item.get("subject") if isinstance(item.get("subject"), dict) else item
            subject_id = sub.get("subjectId") or sub.get("subject_id") or sub.get("id")
            title = sub.get("title") or sub.get("name")
            if not subject_id or not title:
                continue
            results.append({
                "subject_id": str(subject_id),
                "title": title,
                "type": "Series" if _subject_type(sub.get("subjectType", sub.get("subject_type"))) == 2 else "Movie",
                "subject_type": _subject_type(sub.get("subjectType", sub.get("subject_type"))),
                "release_date": sub.get("releaseDate") or sub.get("release_date"),
                "imdb_rating": sub.get("imdbRatingValue") or sub.get("imdb_rating"),
                "genre": sub.get("genre"),
                "cover": _cover_url(sub.get("cover") or sub.get("poster")),
                "backdrop": _cover_url(sub.get("backdrop") or sub.get("background")),
                "detail_path": sub.get("detailPath") or sub.get("detail_path") or sub.get("slug"),
                "description": sub.get("description") or sub.get("plot"),
                "language": sub.get("language")
            })
        return results

    def get_details(self, subject_id: str, detail_path: Optional[str] = None) -> Dict[str, Any]:
        if detail_path:
            try:
                response = self.h5_request("GET", "/detail", params={"detailPath": detail_path, "host": self.active_h5_host})
                data = _upstream_data(response)
                subject = data.get("subject") if isinstance(data, dict) and isinstance(data.get("subject"), dict) else data
                if isinstance(subject, dict) and (subject.get("subjectId") or subject.get("subject_id") or subject.get("id")):
                    return self._map_detail(subject, subject_id, detail_path)
            except Exception as exc:
                logger.info("H5 detail by path failed for %s: %s", subject_id, exc)
        # Resolve the slug from the working home feed when older direct links do
        # not carry it, then fetch the H5 detail instead of relying on mobile auth.
        try:
            home = _upstream_data(self.h5_request("GET", "/home", params={"host": self.active_h5_host}))
            for section in home.get("operatingList", []) if isinstance(home, dict) else []:
                candidates = (section.get("subjects") or []) if isinstance(section, dict) else []
                banner = section.get("banner", {}) if isinstance(section, dict) else {}
                candidates = list(candidates) + (banner.get("items", []) if isinstance(banner, dict) and isinstance(banner.get("items"), list) else [])
                for candidate in candidates:
                    sub = candidate.get("subject") if isinstance(candidate.get("subject"), dict) else candidate
                    if str(sub.get("subjectId") or sub.get("subject_id") or sub.get("id") or "") == subject_id:
                        slug = sub.get("detailPath") or sub.get("detail_path") or sub.get("slug")
                        if slug:
                            response = self.h5_request("GET", "/detail", params={"detailPath": slug, "host": self.active_h5_host})
                            data = _upstream_data(response)
                            detail = data.get("subject") if isinstance(data, dict) and isinstance(data.get("subject"), dict) else data
                            if isinstance(detail, dict) and (detail.get("title") or detail.get("name")):
                                return self._map_detail(detail, subject_id, slug)
        except Exception as exc:
            logger.info("H5 detail lookup failed for %s: %s", subject_id, exc)
        url = f"/wefeed-mobile-bff/subject-api/get?subjectId={subject_id}"
        data = self.api_request("GET", url, timeout=8).get("data", {})
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

    @staticmethod
    def _map_detail(data: Dict[str, Any], subject_id: str, detail_path: Optional[str] = None) -> Dict[str, Any]:
        subject_id = str(data.get("subjectId") or data.get("subject_id") or data.get("id") or subject_id)
        subject_type = _subject_type(data.get("subjectType", data.get("subject_type")))
        dubs = data.get("dubs") or data.get("resourceDetectors") or []
        return {
            "subject_id": subject_id,
            "title": data.get("title") or data.get("name") or "Untitled",
            "subject_type": subject_type,
            "type": "Series" if subject_type == 2 else "Movie",
            "description": data.get("description") or data.get("plot"),
            "release_date": data.get("releaseDate") or data.get("release_date"),
            "duration": data.get("duration"),
            "imdb_rating": data.get("imdbRatingValue") or data.get("imdb_rating"),
            "genre": data.get("genre"),
            "country": data.get("countryName") or data.get("country"),
            "language": data.get("language"),
            "cover": _cover_url(data.get("cover") or data.get("poster")),
            "backdrop": _cover_url(data.get("backdrop") or data.get("background")),
            "detail_path": data.get("detailPath") or data.get("detail_path") or detail_path,
            "dubs": [
                {"subject_id": str(d.get("subjectId") or d.get("subject_id") or ""),
                 "lang_name": d.get("lanName") or d.get("name"),
                 "language": d.get("language")}
                for d in dubs if isinstance(d, dict)
            ],
        }

    def get_episodes(self, subject_id: str) -> List[Dict[str, Any]]:
        url = f"/wefeed-mobile-bff/subject-api/season-info?subjectId={subject_id}"
        res = self.api_request("GET", url, timeout=8)
        seasons = res.get("data", {}).get("seasons", [])
        season_list = []
        for s in seasons:
            season_list.append({
                "season": s.get("se", 1),
                "episode_count": s.get("maxEp", 1),
                "resolutions": s.get("resolutions", [])
            })
        return season_list

    def get_player_domain(self) -> str:
        """Resolve the H5 playback API domain for the active provider host."""
        provider_host = self.active_h5_host
        if self.player_domain and self.player_domain_host == provider_host and self.player_domain_expires > time.time():
            return self.player_domain
        try:
            response = self.h5_request(
                "GET", "/media-player/get-domain", host=provider_host, timeout=5,
            )
            value = _upstream_data(response)
            if isinstance(value, dict):
                value = value.get("domain") or value.get("url") or value.get("host")
            domain = str(value or "").strip().rstrip("/")
            if domain and not urlsplit(domain).scheme:
                domain = f"https://{domain}"
            parsed = urlsplit(domain)
            if parsed.scheme == "https" and parsed.netloc:
                self.player_domain = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
                self.player_domain_host = provider_host
                self.player_domain_expires = time.time() + 300
                return self.player_domain
        except Exception as exc:
            logger.info("Could not resolve MovieBox player domain: %s", exc)
        # The API host is not a media-page origin; fall back to the public site.
        self.player_domain = f"https://{provider_host}"
        self.player_domain_host = provider_host
        self.player_domain_expires = time.time() + 60
        return self.player_domain

    @staticmethod
    def _player_referer(domain: str, subject_id: str, detail_path: str, season: int, episode: int) -> str:
        page_path = quote(detail_path or subject_id, safe="/")
        query = urlencode({
            "id": subject_id,
            "type": "/movie/detail",
            "detailSe": season,
            "detailEp": episode,
            "lang": "en",
        })
        return f"{domain.rstrip('/')}/spa/videoPlayPage/movies/{page_path}?{query}"

    def get_streams(self, subject_id: str, season: int = 0, episode: int = 0, detail_path: Optional[str] = None) -> List[Dict[str, Any]]:
        if not detail_path:
            try:
                details = self.get_details(subject_id)
                detail_path = details.get("detail_path", "")
            except Exception:
                detail_path = ""
        player_domain = self.get_player_domain()
        # Web playback requires the guest token issued for the selected web
        # host. The native API token is a separate credential and can be rejected.
        provider_host = self.active_h5_host
        token = self.h5_tokens.get(provider_host) or self.bearer_token
        streams: List[Dict[str, Any]] = []
        seen_urls = set()
        player_origin = f"https://{provider_host}"
        player_referer = self._player_referer(player_origin, subject_id, detail_path or "", season, episode)

        known_api_hosts = {urlsplit(domain).hostname for domain in WEB_DOMAINS}
        candidate_domains = list(WEB_DOMAINS)
        if urlsplit(player_domain).hostname in known_api_hosts:
            candidate_domains.insert(0, player_domain)
        candidate_domains = list(dict.fromkeys(candidate_domains))
        refreshed_web_token = False
        for domain in candidate_domains:
            play_query = urlencode({
                "subjectId": subject_id,
                "se": season,
                "ep": episode,
                "detailPath": detail_path or "",
                "streamSignType": 1,
                "supportCodecs[hevc]": 0,
                "supportCodecs[h264]": 1,
                "host": provider_host,
            })
            play_url = f"{domain}/wefeed-h5api-bff/subject/play?{play_query}"
            headers = {
                "User-Agent": WEB_USER_AGENT,
                "Referer": player_referer,
                "Origin": player_origin,
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "x-client-info": json.dumps({"timezone": "Asia/Dhaka"}, separators=(",", ":")),
                "x-request-lang": "en",
                "x-vip-restrict": "0",
                "x-no-high-risk-restrict": "0",
                "sec-ch-ua": '"Chromium";v="120", "Google Chrome";v="120", "Not/A)Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "cross-site",
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                resp = self.session.get(play_url, headers=headers, timeout=6)
                if resp.status_code in (401, 403, 441) and not refreshed_web_token:
                    refreshed_web_token = True
                    self.h5_tokens.pop(provider_host, None)
                    try:
                        self.h5_request("GET", "/home", params={"host": provider_host}, timeout=3, retry_auth=False)
                        token = self.h5_tokens.get(provider_host) or token
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                            resp = self.session.get(play_url, headers=headers, timeout=6)
                    except Exception as exc:
                        logger.info("H5 playback guest-token refresh failed: %s", exc)
                if resp.status_code != 200:
                    logger.info("H5 playback provider returned HTTP %s", resp.status_code, extra={"provider": urlsplit(domain).hostname})
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
                                "User-Agent": WEB_USER_AGENT,
                                "Referer": player_referer,
                                "Origin": player_origin,
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
                                    "User-Agent": WEB_USER_AGENT,
                                    "Referer": player_referer,
                                    "Origin": player_origin,
                                }
                            })
                    if streams:
                        break
            except Exception:
                logger.warning("web provider stream probe failed", extra={"provider": domain, "subject_id": subject_id}, exc_info=True)

        try:
            native_url = f"/wefeed-mobile-bff/subject-api/play-info?subjectId={subject_id}&se={season}&ep={episode}"
            native_data = self.api_request("GET", native_url, timeout=8).get("data", {})
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
                        "Referer": player_referer,
                        "Origin": player_origin,
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
    headers.setdefault("Accept", "*/*")
    # Video CDNs may otherwise gzip/br-compress a ranged MP4 response. That
    # changes byte offsets and causes browsers to reject or stall seeking.
    headers["Accept-Encoding"] = "identity"
    if payload.get("cookie"):
        headers["Cookie"] = str(payload["cookie"])
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    return headers

def _new_gateway_client():
    """Use the same browser TLS/HTTP fingerprint for CDN requests as the H5 API."""
    if cr:
        return cr.Session(impersonate="chrome120", verify=SYSTEM_CA_BUNDLE or True)
    try:
        return httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
            verify=SYSTEM_CA_BUNDLE or True,
            http2=True,
        )
    except ImportError:
        return httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
            verify=SYSTEM_CA_BUNDLE or True,
        )

def _gateway_request(client, method: str, target: str, headers: Dict[str, str]):
    if cr and isinstance(client, cr.Session):
        return client.request(
            method,
            target,
            headers=headers,
            timeout=60,
            allow_redirects=False,
            stream=True,
            http_version="v2",
        )
    request = client.build_request(method, target, headers=headers)
    return client.send(request, stream=True, follow_redirects=False)

def _iter_gateway_bytes(upstream):
    if hasattr(upstream, "iter_content"):
        yield from upstream.iter_content()
    else:
        yield from upstream.iter_bytes(chunk_size=1024 * 1024)

def _open_gateway_response(client, method: str, target: str, headers: Dict[str, str]):
    current = _validated_redirect_url(target)
    for _ in range(5):
        response = _gateway_request(client, method, current, headers)
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
    client = _new_gateway_client()
    upstream, final_url = _open_gateway_response(client, request.method, target, _gateway_headers(payload, request))
    if upstream.status_code not in (200, 206):
        status = upstream.status_code
        upstream.close(); client.close()
        logger.warning("media gateway upstream failure", extra={"status": status, "host": urlsplit(target).hostname})
        raise HTTPException(status_code=502, detail=f"Media source returned HTTP {status}")

    media_type = upstream.headers.get("content-type", "")
    if media_type.split(";", 1)[0].strip().lower() in {"", "application/octet-stream", "binary/octet-stream"}:
        media_type = str(payload.get("mime_type") or ("video/mp4" if urlsplit(final_url).path.lower().endswith(".mp4") else media_type))
    passthrough = {key: upstream.headers[key] for key in ("content-length", "content-range", "accept-ranges", "etag", "last-modified") if key in upstream.headers}
    if media_type:
        passthrough["Content-Type"] = media_type
    passthrough["Cache-Control"] = "no-store"
    if request.method == "HEAD":
        upstream.close(); client.close()
        return Response(status_code=upstream.status_code, headers=passthrough, media_type=media_type or None)

    def body():
        try:
            for chunk in _iter_gateway_bytes(upstream):
                if chunk:
                    yield chunk
        finally:
            upstream.close(); client.close()

    return StreamingResponse(body(), status_code=upstream.status_code, headers=passthrough, media_type=media_type or "application/octet-stream")

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
    client = _new_gateway_client()
    upstream, final_url = _open_gateway_response(client, request.method, target, _gateway_headers(payload, request))
    if upstream.status_code != 200:
        upstream.close(); client.close()
        logger.warning("DASH manifest upstream failure", extra={"status": upstream.status_code, "host": urlsplit(target).hostname})
        raise HTTPException(status_code=502, detail=f"DASH manifest returned HTTP {upstream.status_code}")
    if request.method == "HEAD":
        headers = {key: upstream.headers[key] for key in ("content-type", "content-length", "etag", "last-modified") if key in upstream.headers}
        upstream.close(); client.close()
        return Response(status_code=200, headers=headers, media_type="application/dash+xml")
    manifest = b"".join(_iter_gateway_bytes(upstream)).decode("utf-8-sig", errors="replace")
    upstream.close(); client.close()
    gateway_base = f"/api/playback/{token}/"
    manifest = _rewrite_dash_manifest(manifest, gateway_base)
    return Response(content=manifest, media_type="application/dash+xml", headers={"Cache-Control": "no-store"})

@app.api_route("/api/playback/{token}/compat", methods=["GET", "HEAD"])
def playback_compatibility_transcode(token: str, request: Request):
    """Convert a resolved stream to broadly supported H.264/AAC fragmented MP4."""
    payload = _decode_playback_token(token)
    if payload.get("kind") not in {"media", "dash"}:
        raise HTTPException(status_code=404, detail="Compatibility playback is unavailable for this stream.")
    headers = {
        "Cache-Control": "no-store",
        "Accept-Ranges": "none",
        "X-Accel-Buffering": "no",
    }
    if request.method == "HEAD":
        return Response(status_code=200, media_type="video/mp4", headers=headers)

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="Compatibility playback is unavailable on this server.")
    if not _COMPAT_TRANSCODE_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="The compatibility player is busy. Please retry shortly.")

    port = int(os.getenv("PORT", "8000"))
    local_source = f"http://127.0.0.1:{port}/api/playback/{token}"
    command = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error", "-nostdin", "-rw_timeout", "30000000",
        "-protocol_whitelist", "http,tcp,https,tls,crypto,data",
        "-i", local_source,
        "-map", "0:v:0", "-map", "0:a:0?", "-sn",
        "-vf", "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p",
        "-r", "30", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-profile:v", "baseline", "-level:v", "3.1", "-crf", "22", "-bf", "0", "-threads", "2",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-frag_duration", "2000000",
        "-f", "mp4", "pipe:1",
    ]
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    except OSError as exc:
        _COMPAT_TRANSCODE_SLOTS.release()
        logger.warning("Could not start compatibility transcode: %s", exc)
        raise HTTPException(status_code=503, detail="Compatibility playback could not start.") from exc

    def body():
        try:
            if process.stdout is None:
                return
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
            return_code = process.wait()
            if return_code:
                logger.warning("Compatibility transcode exited with status %s", return_code)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if process.stdout:
                process.stdout.close()
            _COMPAT_TRANSCODE_SLOTS.release()

    return StreamingResponse(body(), media_type="video/mp4", headers=headers)

@app.api_route("/api/playback/{token}/{asset:path}", methods=["GET", "HEAD"])
def playback_dash_asset(token: str, asset: str, request: Request):
    payload = _decode_playback_token(token)
    if payload.get("kind") != "dash":
        raise HTTPException(status_code=404, detail="Playback asset not found")
    target = urljoin(str(payload.get("url") or ""), asset)
    if not _media_host_allowed(target):
        raise HTTPException(status_code=502, detail="Playback asset resolved to a disallowed host.")
    child = dict(payload); child["url"] = target; child.pop("mime_type", None)
    return _stream_gateway_response(child, request)

@app.get("/", include_in_schema=False)
def frontend_root():
    """Serve the built React app at the public site root."""
    index = os.path.join(os.path.dirname(__file__), "dist", "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend build not found")

@app.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=120), page: int = Query(1, ge=1, le=100), per_page: int = Query(20, ge=1, le=100)):
    results = engine.search(q, page=page, per_page=per_page)
    return {"query": q, "page": page, "results": results}

@app.get("/search/suggest")
def search_suggest(q: str = Query(..., min_length=1, max_length=120)):
    try:
        response = engine.h5_request("POST", "/subject/search-suggest", body={"keyword": q, "perPage": 10})
        raw = _search_items(_upstream_data(response))
    except Exception:
        raw = []
    suggestions = []
    for item in raw:
        sub = item.get("subject") if isinstance(item.get("subject"), dict) else item
        subject_id = sub.get("subjectId") or sub.get("subject_id") or sub.get("id") or item.get("subjectId")
        title = sub.get("title") or sub.get("name") or item.get("word") or item.get("title")
        suggestions.append({
            "title": title,
            "slug": sub.get("detailPath") or sub.get("detail_path") or item.get("detailPath"),
            "subject_id": str(subject_id) if subject_id else None,
            "type": "Series" if sub.get("subjectType", sub.get("subject_type")) == 2 else "Movie"
        })
    return {"suggestions": [item for item in suggestions if item.get("title")]}

@app.get("/detail/{subject_id}")
def get_detail(subject_id: str, detail_path: Optional[str] = Query(None, max_length=180)):
    if detail_path and (".." in detail_path or "\x00" in detail_path):
        raise HTTPException(status_code=400, detail="Invalid title path")
    return engine.get_details(validate_subject_id(subject_id), detail_path=detail_path)

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
    """Read the mobile ranking feed, falling back to reachable H5 home shelves."""
    pager: Dict[str, Any] = {}
    raw_items: List[Dict[str, Any]] = []
    try:
        query = urlencode({"tabId": tab_id, "categoryType": tab_id, "page": page, "perPage": per_page})
        response = engine.api_request("GET", f"/wefeed-mobile-bff/tab/ranking-list?{query}", use_token=False)
        data = response.get("data", {}) if isinstance(response, dict) else {}
        pager = data.get("pager", {}) if isinstance(data, dict) else {}
        raw_items = data.get("subjects") or data.get("items") or data.get("list") or []
    except Exception as exc:
        logger.info("Mobile catalog unavailable; using H5 home shelves: %s", exc)
    if not raw_items:
        home = _upstream_data(engine.h5_request("GET", "/home", params={"host": engine.active_h5_host}))
        for section in home.get("operatingList", []) if isinstance(home, dict) else []:
            if not isinstance(section, dict):
                continue
            candidates = section.get("subjects") or []
            banner = section.get("banner")
            if isinstance(banner, dict) and isinstance(banner.get("items"), list):
                candidates = list(candidates) + banner["items"]
            if isinstance(candidates, list):
                section_type = 2 if any(word in str(section.get("title") or "").lower() for word in ("series", "tv show", "drama")) else 1
                for candidate in candidates:
                    if isinstance(candidate, dict):
                        subject = candidate.get("subject") if isinstance(candidate.get("subject"), dict) else candidate
                        if not subject.get("subjectType") and not subject.get("subject_type"):
                            subject["subjectType"] = section_type
                    raw_items.append(candidate)
    items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        subject = item.get("subject") if isinstance(item.get("subject"), dict) else item
        subject_type = _subject_type(subject.get("subjectType", subject.get("subject_type")))
        if subject_type != expected_type:
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
            "cover": _cover_url(cover or subject.get("poster")),
            "backdrop": _cover_url(subject.get("backdrop") or subject.get("background")),
            "rating": subject.get("imdbRatingValue") or subject.get("rating"),
            "year": str(subject.get("releaseDate") or subject.get("year") or "")[:4] or None,
            "genre": subject.get("genre"),
        })
    # Home shelves are bounded and often overlap; deduplicate before paginating.
    deduped = []
    seen_ids = set()
    for item in items:
        if item["subject_id"] not in seen_ids:
            seen_ids.add(item["subject_id"])
            deduped.append(item)
    items = deduped
    if not pager:
        start = (page - 1) * per_page
        total = len(items)
        return {"page": page, "per_page": per_page, "total": total, "items": items[start:start + per_page]}
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
    return engine.h5_request("GET", "/home", params={"host": engine.active_h5_host})

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

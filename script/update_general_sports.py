#!/usr/bin/env python3
"""
SAKIRULs IPTV Sports Auto Updater (In-Place Edition, hardened)
"""

import json
import re
import sys
import threading
import time
import unicodedata
import difflib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import List, Dict, Set, Tuple, Optional
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------
PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")

SPORTS_GROUP_TITLE = "sports"
SPORTS_SECTION_TITLE = "SPORTS"
SEPARATOR_PATTERN = re.compile(r'^#\s*=+\s*$')

CACHE_DIR = Path(".cache")
SOURCE_CACHE_FILE = CACHE_DIR / "source_cache.json"
RESOLVED_URL_CACHE_FILE = CACHE_DIR / "resolved_urls.json"
RESOLVED_URL_TTL_SECONDS = 6 * 3600  # 6 hours

REPORTS_DIR = Path("reports")

# HTTP & Concurrency Tuning
REQUEST_TIMEOUT = 6
DOWNLOAD_TIMEOUT = 30
MAX_CANDIDATES_TO_TEST = 8
MAX_TEST_WORKERS = 30
MAX_DOWNLOAD_WORKERS = 16
VALIDATION_READ_BYTES = 8192

# ---------------------------------------------------------------------------
# Source Definitions & Priorities
# ---------------------------------------------------------------------------
SKY_SPORTS_PREFERRED_SOURCE = [
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s1.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s2.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/wc5.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/wc8.m3u",
]
TSPORTS_SOURCES = [
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/combine_playlist.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/ns_player.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/ott_navigator.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/universal_player.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/refs/heads/main/combined-playlist.m3u",
]
_SOURCE_URLS_RAW = [
    # IPTVFlixBD - OopsTv
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/2.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/Sports-s7.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/alfa-wc.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/all-sports.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/asia.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/bd-spo.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/bd-test.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/bear.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/chspo.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/new-sp-s4.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/new-sports-fast.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s1.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s2.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/wc5.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/wc8.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/world-1.m3u",
    # IPTVFlixBD - BDIX
    "https://raw.githubusercontent.com/IPTVFlixBD/BDIX-IPTV-playlist/main/A1x.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/BDIX-IPTV-playlist/main/BDIX.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/BDIX-IPTV-playlist/main/IPTV-mix.m3u",
    # IPTVFlixBD
    "https://raw.githubusercontent.com/IPTVFlixBD/iptv-playlist/main/PlexTV.m3u8",
    # CricHD
    "https://raw.githubusercontent.com/abusaeeidx/CricHd-playlists-Auto-Update-permanent/main/ALL.m3u",
    # IPTV Scraper Zilla
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/main/CricHD.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/main/BD.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/main/SamsungTVPlus-All.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/refs/heads/main/combined-playlist.m3u",
    # Toffee
    "https://raw.githubusercontent.com/abusaeeidx/Toffee-playlist/main/ott_navigator.m3u",
    # T Sports
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/combine_playlist.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/ns_player.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/ott_navigator.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/universal_player.m3u",
    # KB TV
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/FIFA%20Live%20Playlist%20Server%20v1.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/FIFA%20Special%20KB%20Live%20Tv%20Playlist%20v1.4.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/Github%20Auto%20Update%20Channel.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/KB%20Live%20Tv%20121%20Channel%20v1.2.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/KB%20Live%20Tv%20Playlist%20v1.3.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/KB%20Live%20Tv%20Playlist%20v1.6.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/KB%20TV%20Playlist%2047%20Channel%20v1.0.m3u"
]
SOURCE_URLS = list(dict.fromkeys(_SOURCE_URLS_RAW))

SOURCE_PRIORITIES = {
    "sky sports": {"lock": SKY_SPORTS_PREFERRED_SOURCE},
    "t sports": {"prefer": TSPORTS_SOURCES},
    "sony sports": {"prefer": SKY_SPORTS_PREFERRED_SOURCE},
    "star sports": {"prefer": SKY_SPORTS_PREFERRED_SOURCE},
    "bein sports": {"prefer": SKY_SPORTS_PREFERRED_SOURCE},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
}

CHANNELS = [
    "beIN SPORTS 1", "beIN SPORTS 2", "beIN SPORTS 3", "beIN SPORTS 4",
    "beIN SPORTS 5", "beIN SPORTS 6", "BTV World", "DAZN 1", "Das Erste HD",
    "Eurosport 1", "Eurosport 2", "FIFA+", "LaLiga TV", "LFC TV",
    "Motorsport.tv", "MUTV", "NPO 1 HD", "NPO 2 HD", "NPO 3 HD",
    "Premier Sports 1", "Premier Sports 2", "PTV", "Racing TV", "Racing.com",
    "Real Madrid TV", "Red Bull TV", "Servus TV Motorsport", "Sky Sports Action",
    "Sky Sports Cricket", "Sky Sports F1", "Sky Sports Football", "Sky Sports Golf",
    "Sky Sports Main Event", "Sky Sports Mix", "Sky Sports News",
    "Sky Sports Plus", "Sky Sports Premier League", "Sky Sports Racing",
    "Sky Sports Tennis", "Sony Sports Ten 1", "Sony Sports Ten 2",
    "Sony Sports Ten 3", "Sport1 Germany", "Sportdigital Fußball",
    "Sports18 1 HD", "Star Sports 1", "Star Sports 1 Hindi",
    "Star Sports Select 1", "Star Sports Select 2", "SuperSport Cricket",
    "SuperSport Football", "SuperSport Golf", "SuperSport La Liga",
    "SuperSport Motorsport", "SuperSport Premier League", "SuperSport Rugby",
    "SuperSport Tennis", "SuperSport Variety 1", "SuperSport Variety 2",
    "SuperSport Variety 3", "SuperSport Variety 4", "T Sports HD",
    "TNT Sports 1", "TNT Sports 2", "TNT Sports 3", "UFC TV", "UNITE8 SPORTS 1",
    "UNITE8 SPORTS 2", "Viaplay TV", "Willow Cricket HD", "WWE Network",
]

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass
class ChannelData:
    name: str
    url: str
    group: str
    source_url: str
    strict: str
    tokens: Tuple[str, ...]
    tokens_q: Tuple[str, ...]
    variants: Set[Tuple[str, ...]]


@dataclass
class StreamCheckResult:
    is_valid: bool
    latency: float
    content_type: str
    final_url: str
    details: str


@dataclass
class ScoredCandidate:
    channel: ChannelData
    check: StreamCheckResult
    is_preferred: bool
    score: float


@dataclass
class RunStats:
    total_sources: int = 0
    downloaded_sources: int = 0
    failed_sources: int = 0
    parsed_channels: int = 0
    duplicate_urls: List[str] = field(default_factory=list)
    invalid_streams: List[Tuple[str, str, str]] = field(default_factory=list)
    matched: List[Tuple[str, str, str]] = field(default_factory=list)
    unmatched: List[str] = field(default_factory=list)
    source_channel_counts: Dict[str, int] = field(default_factory=dict)
    start_time: float = field(default_factory=time.monotonic)


GLOBAL_ASSIGNED_URLS: Set[str] = set()
GLOBAL_ASSIGNED_LOCK = threading.Lock()

STREAM_CACHE: Dict[str, StreamCheckResult] = {}
STREAM_CACHE_LOCK = threading.Lock()

RESOLVED_URL_CACHE: Dict[str, str] = {}
RESOLVED_URL_CACHE_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Normalization & Matching Logic
# ---------------------------------------------------------------------------
QUALITY_WORDS = {"hd", "fhd", "uhd", "shd", "sd", "4k", "8k", "2k", "hq", "sq", "lq", "fullhd"}
REGION_WORDS = {"uk", "usa", "us", "fr", "de", "es", "it", "ca", "au", "eu", "in", "bd", "nl", "be"}
GENERIC_FILLER = QUALITY_WORDS | REGION_WORDS | {"sports", "sport", "channel", "tv", "the", "live", "plus"}
MIN_MATCH_TOKENS = 2
MIN_SINGLE_TOKEN_LEN = 4
MAX_MERGE_WINDOW = 3
CHAR_TRANSLITERATIONS = {"ß": "ss", "+": " plus "}
GROUP_TITLE_RE = re.compile(r'group-title="([^"]*)"', re.IGNORECASE)


def transliterate(name: str) -> str:
    for src, dst in CHAR_TRANSLITERATIONS.items():
        name = name.replace(src, dst)
    return name


def clean_channel_name(name: str) -> str:
    name = transliterate(name)
    name = ''.join(c for c in unicodedata.normalize('NFKD', str(name)) if unicodedata.category(c) != 'Mn')
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    name = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', name)
    name = re.sub(r'(\d)([a-zA-Z])', r'\1 \2', name)
    cleaned = re.sub(r"[┃\|│║\[\]\(\)\{\}#\-_\*]+", " ", name)

    tokens = cleaned.split()
    if tokens and tokens[0].lower() in REGION_WORDS:
        tokens.pop(0)
    if tokens and tokens[-1].lower() in REGION_WORDS:
        tokens.pop()
    return " ".join(tokens)


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", clean_channel_name(name).lower())


def tokenize(name: str) -> Tuple[str, ...]:
    cleaned = clean_channel_name(name).lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    cleaned = re.sub(r'\bbtv\b', 'bangladesh television', cleaned)
    return tuple(w for w in cleaned.split() if w and w not in REGION_WORDS)


def strip_quality(tokens: Tuple[str, ...]) -> Tuple[str, ...]:
    return tuple(t for t in tokens if t not in QUALITY_WORDS)


def contiguous_subseq(short: Tuple[str, ...], long_: Tuple[str, ...]) -> bool:
    ls, ll = len(short), len(long_)
    if ls == 0 or ls > ll:
        return False
    for i in range(ll - ls + 1):
        if long_[i:i + ls] == short:
            return True
    return False


def tokens_containment_match(a_tokens: Tuple[str, ...], b_tokens: Tuple[str, ...]) -> bool:
    if not a_tokens or not b_tokens:
        return False
    shorter, longer = (a_tokens, b_tokens) if len(a_tokens) <= len(b_tokens) else (b_tokens, a_tokens)
    if len(shorter) < MIN_MATCH_TOKENS:
        if len(shorter) != 1 or len(shorter[0]) < MIN_SINGLE_TOKEN_LEN:
            return False
    if not contiguous_subseq(shorter, longer):
        return False
    return any(t not in GENERIC_FILLER for t in shorter)


def merge_variants(tokens: Tuple[str, ...], max_window: int = MAX_MERGE_WINDOW) -> Set[Tuple[str, ...]]:
    n = len(tokens)
    variants = {tokens}
    for size in range(2, min(max_window, n) + 1):
        for start in range(0, n - size + 1):
            merged_token = "".join(tokens[start:start + size])
            variant = tokens[:start] + (merged_token,) + tokens[start + size:]
            variants.add(variant)
    return variants


def get_source_rules(channel_name: str) -> Dict[str, List[str]]:
    name_lower = channel_name.lower()
    for keyword, rules in SOURCE_PRIORITIES.items():
        if keyword in name_lower:
            return rules
    return {}


def get_all_matches(
    channel_name: str, all_channels: List[ChannelData]
) -> Tuple[List[ChannelData], Optional[str], List[Tuple[float, str, str]], List[ChannelData]]:
    target_strict = normalize(channel_name)
    target_tokens = tokenize(channel_name)
    target_tokens_q = strip_quality(target_tokens)
    target_variants = merge_variants(target_tokens_q)

    rules = get_source_rules(channel_name)
    is_locked = "lock" in rules
    allowed_sources = rules.get("lock") or rules.get("prefer") or []

    def search_pass(channel_list: List[ChannelData]):
        exact, tokens_match, tokens_q_match, merged = [], [], [], []
        closest = []

        for ch in channel_list:
            sim_score = difflib.SequenceMatcher(None, target_strict, ch.strict).ratio()
            if sim_score > 0.5:
                closest.append((sim_score, ch.name, ch.source_url))

            if ch.strict == target_strict:
                exact.append(ch)
                continue
            if tokens_containment_match(target_tokens, ch.tokens):
                tokens_match.append(ch)
                continue
            if tokens_containment_match(target_tokens_q, ch.tokens_q):
                tokens_q_match.append(ch)
                continue

            matched_variant = False
            for t_var in target_variants:
                for c_var in ch.variants:
                    if tokens_containment_match(t_var, c_var):
                        matched_variant = True
                        break
                if matched_variant:
                    break

            if matched_variant:
                merged.append(ch)
        closest = sorted(closest, key=lambda x: x[0], reverse=True)[:5]

        if exact:
            return exact, "exact", closest
        if tokens_match:
            return tokens_match, "token", closest
        if tokens_q_match:
            return tokens_q_match, "token-quality-stripped", closest
        if merged:
            return merged, "merged-token", closest
        return [], None, closest

    preferred_channels = [c for c in all_channels if c.source_url in allowed_sources] if allowed_sources else all_channels
    hits, tier, closest = search_pass(preferred_channels)

    if hits:
        return hits, tier, closest, preferred_channels
    if not is_locked and allowed_sources:
        non_preferred = [c for c in all_channels if c.source_url not in allowed_sources]
        hits2, tier2, closest2 = search_pass(non_preferred)
        if hits2:
            return hits2, tier2, closest2, non_preferred

        all_closest = sorted(closest + closest2, key=lambda x: x[0], reverse=True)[:5]
        return [], None, all_closest, preferred_channels + non_preferred
    return [], None, closest, preferred_channels


# ---------------------------------------------------------------------------
# Persistent Cache Helpers
# ---------------------------------------------------------------------------
def load_json_cache(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[CACHE] Could not read {path}: {e} (starting fresh)")
    return {}


def save_json_cache(path: Path, data: dict) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[CACHE] Could not write {path}: {e}")


def load_resolved_url_cache() -> Dict[str, str]:
    raw = load_json_cache(RESOLVED_URL_CACHE_FILE)
    now = time.time()
    fresh = {}
    for k, v in raw.items():
        try:
            if now - v.get("ts", 0) < RESOLVED_URL_TTL_SECONDS:
                fresh[k] = v["url"]
        except Exception:
            continue
    return fresh


def save_resolved_url_cache(cache: Dict[str, str]) -> None:
    now = time.time()
    payload = {k: {"url": v, "ts": now} for k, v in cache.items()}
    save_json_cache(RESOLVED_URL_CACHE_FILE, payload)


# ---------------------------------------------------------------------------
# HTTP Helpers
# ---------------------------------------------------------------------------
def get_http_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=MAX_TEST_WORKERS, pool_maxsize=MAX_TEST_WORKERS)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_base_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _download_one_source(url: str, session: requests.Session, http_cache: dict) -> Tuple[str, str]:
    cached_entry = http_cache.get(url, {})
    headers = dict(HEADERS)
    if cached_entry.get("etag"):
        headers["If-None-Match"] = cached_entry["etag"]
    if cached_entry.get("last_modified"):
        headers["If-Modified-Since"] = cached_entry["last_modified"]

    try:
        resp = session.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT)
        if resp.status_code == 304 and cached_entry.get("content"):
            print(f"[CACHED] Unchanged: {url}")
            return url, cached_entry["content"]
        if resp.status_code == 200:
            print(f"[OK] Downloaded: {url}")
            http_cache[url] = {
                "etag": resp.headers.get("ETag", ""),
                "last_modified": resp.headers.get("Last-Modified", ""),
                "content": resp.text,
            }
            return url, resp.text
        print(f"[FAILED] {url} (HTTP {resp.status_code})")
    except Exception as e:
        print(f"[ERROR] {url} -> {e}")
    return url, ""


def download_sources(session: requests.Session, http_cache: dict) -> Dict[str, str]:
    raw_sources: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as executor:
        futures = {executor.submit(_download_one_source, url, session, http_cache): url for url in SOURCE_URLS}
        for future in as_completed(futures):
            url = futures[future]
            try:
                _, text = future.result()
            except Exception as e:
                print(f"[ERROR] Unexpected failure downloading {url}: {e}")
                text = ""
            if text:
                raw_sources[url] = text
    return raw_sources


def _parse_m3u_text(content: str, source_url: str = "") -> List[ChannelData]:
    channels = []
    current_name, current_group = None, ""

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            gt_match = GROUP_TITLE_RE.search(line)
            current_group = gt_match.group(1) if gt_match else ""
            current_name = line.rsplit(",", 1)[-1].strip() if "," in line else None
        elif not line.startswith("#") and current_name:
            t_tokens = tokenize(current_name)
            t_tokens_q = strip_quality(t_tokens)
            channels.append(ChannelData(
                name=current_name,
                url=line,
                group=current_group,
                source_url=source_url,
                strict=normalize(current_name),
                tokens=t_tokens,
                tokens_q=t_tokens_q,
                variants=merge_variants(t_tokens_q),
            ))
            current_name = None
            current_group = ""
    return channels


def parse_sources(raw_sources: Dict[str, str]) -> List[ChannelData]:
    all_channels: List[ChannelData] = []
    for source_url, content in raw_sources.items():
        try:
            channels = _parse_m3u_text(content, source_url=source_url)
            all_channels.extend(channels)
            print(f"Parsed {len(channels)} channels from {source_url}")
        except Exception as e:
            print(f"[ERROR] Failed to parse {source_url}: {e} (skipping this source)")
    return all_channels


def _resolve_stream_url_uncached(url: str, session: requests.Session) -> str:
    if "raw.githubusercontent.com" in url or url.endswith((".m3u8", ".m3u", ".txt")):
        try:
            resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                if resp.url and resp.url != url and resp.url.lower().endswith((".m3u8", ".ts")):
                    return resp.url
                urls = re.findall(r'(https?://[^\s"\'<>]+)', resp.text)
                if urls:
                    m3u8_urls = [u for u in urls if ".m3u8" in u.lower()]
                    return m3u8_urls[0] if m3u8_urls else urls[0]
        except Exception as e:
            print(f"[WRAP] Could not resolve wrapper {url}: {e} (using it as-is)")
    return url


def resolve_stream_url(url: str, session: requests.Session) -> str:
    with RESOLVED_URL_CACHE_LOCK:
        cached = RESOLVED_URL_CACHE.get(url)
    if cached is not None:
        return cached
    resolved = _resolve_stream_url_uncached(url, session)
    with RESOLVED_URL_CACHE_LOCK:
        RESOLVED_URL_CACHE.setdefault(url, resolved)
    return resolved


# ---------------------------------------------------------------------------
# Stream Validation & Scoring
# ---------------------------------------------------------------------------
CHALLENGE_MARKERS = (
    "checking your browser", "cf-browser-verification", "just a moment",
    "attention required", "cloudflare", "ddos protection by", "jschl_answer",
    "captcha",
)
LOGIN_MARKERS = (
    'type="password"', 'name="password"', 'id="password"',
    "sign in to continue", "please log in", "please login", "session expired",
)
EXPIRY_MARKERS = ("token expired", "link expired", "expired token", "url expired", "access denied")


def _rejection_reason(text_lower: str) -> Optional[str]:
    for marker in CHALLENGE_MARKERS:
        if marker in text_lower:
            return "Cloudflare/challenge page"
    for marker in LOGIN_MARKERS:
        if marker in text_lower:
            return "Login page"
    for marker in EXPIRY_MARKERS:
        if marker in text_lower:
            return "Token-expired page"
    return None


def validate_stream(url: str, session: requests.Session) -> StreamCheckResult:
    try:
        start = time.monotonic()
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True, allow_redirects=True)
        latency = time.monotonic() - start
        final_url = resp.url or url

        if resp.status_code >= 400:
            resp.close()
            return StreamCheckResult(False, latency, "", final_url, f"Rejected: HTTP {resp.status_code}")

        c_type = resp.headers.get("Content-Type", "").lower()

        chunk = b""
        for piece in resp.iter_content(chunk_size=2048):
            chunk += piece
            if len(chunk) >= VALIDATION_READ_BYTES:
                break
        resp.close()

        text_content = chunk.decode(errors="ignore")
        text_lower = text_content.lower()

        if not text_content.strip():
            return StreamCheckResult(False, latency, c_type, final_url, "Rejected: Empty response")

        if "json" in c_type or text_lower.lstrip().startswith(("{", "[")):
            return StreamCheckResult(False, latency, c_type, final_url, "Rejected: JSON response")

        if "xml" in c_type or text_lower.lstrip().startswith("<?xml"):
            return StreamCheckResult(False, latency, c_type, final_url, "Rejected: XML response")

        reason = _rejection_reason(text_lower)
        if reason:
            return StreamCheckResult(False, latency, c_type, final_url, f"Rejected: {reason}")

        if "text/html" in c_type or text_lower.lstrip().startswith(("<html", "<!doctype")):
            return StreamCheckResult(False, latency, c_type, final_url, "Rejected: HTML page")

        has_extinf = "#EXTINF" in text_content
        has_stream_inf = "#EXT-X-STREAM-INF" in text_content
        has_ts_or_m3u8 = ".m3u8" in text_content or ".ts" in text_content or "#EXTM3U" in text_content

        if any(kw in c_type for kw in ("mpegurl", "video", "mpeg")) or has_extinf or has_stream_inf or has_ts_or_m3u8:
            return StreamCheckResult(True, latency, c_type, final_url, "Valid stream")

        return StreamCheckResult(False, latency, c_type, final_url, f"Rejected: Unsupported Content-Type '{c_type}'")
    except Exception as e:
        return StreamCheckResult(False, 99.0, "", url, f"Rejected: Exception {type(e).__name__}: {e}")


def get_cached_validation(url: str, session: requests.Session) -> StreamCheckResult:
    with STREAM_CACHE_LOCK:
        if url in STREAM_CACHE:
            return STREAM_CACHE[url]

    res = validate_stream(url, session)

    with STREAM_CACHE_LOCK:
        STREAM_CACHE[url] = res
    return res


def score_candidate(candidate: ChannelData, check: StreamCheckResult, target_name: str) -> ScoredCandidate:
    rules = get_source_rules(target_name)
    pref_sources = rules.get("prefer") or rules.get("lock") or []
    is_pref = candidate.source_url in pref_sources

    score = 0.0
    if check.is_valid:
        score += 100.0
    if is_pref:
        score += 50.0

    score += max(0.0, 20.0 - (check.latency * 5.0))

    if any(st in check.content_type for st in ("mpegurl", "video/")):
        score += 10.0

    return ScoredCandidate(candidate=candidate, check=check, is_preferred=is_pref, score=score)


# ---------------------------------------------------------------------------
# In-Place File Operations
# ---------------------------------------------------------------------------
def read_master_playlist(path: Path) -> Tuple[List[str], str, str]:
    if not path.exists():
        print(f"[FATAL] Master playlist {path} not found.")
        sys.exit(1)

    raw_bytes = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw_bytes else "\n"

    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            lines = text.splitlines()
            return lines, newline, enc
        except UnicodeDecodeError:
            continue

    print(f"[FATAL] Failed to decode {path}")
    sys.exit(1)


def build_sports_channel_map(lines: List[str]) -> Dict[str, int]:
    mapping = {}

    has_group_title = any("group-title=" in line.lower() for line in lines if line.startswith("#EXTINF"))

    if has_group_title:
        for idx, line in enumerate(lines):
            if line.startswith("#EXTINF"):
                gt_match = GROUP_TITLE_RE.search(line)
                if gt_match and gt_match.group(1).lower() == SPORTS_GROUP_TITLE:
                    c_name = line.rsplit(",", 1)[-1].strip() if "," in line else ""
                    if c_name and (idx + 1) < len(lines):
                        mapping[normalize(c_name)] = idx + 1
    else:
        in_sports_section = False
        for idx, line in enumerate(lines):
            line_str = line.strip()
            if line_str.startswith("#") and SPORTS_SECTION_TITLE in line_str.upper():
                in_sports_section = True
                continue
            if in_sports_section and SEPARATOR_PATTERN.match(line_str):
                in_sports_section = False
                continue

            if in_sports_section and line_str.startswith("#EXTINF"):
                c_name = line_str.rsplit(",", 1)[-1].strip() if "," in line_str else ""
                if c_name and (idx + 1) < len(lines):
                    mapping[normalize(c_name)] = idx + 1

    return mapping


# ---------------------------------------------------------------------------
# Main Execution Pipeline
# ---------------------------------------------------------------------------
def main():
    stats = RunStats()
    session = get_http_session()

    print("Loading master playlist...")
    lines, newline, encoding = read_master_playlist(PLAYLIST_FILE)
    sports_map = build_sports_channel_map(lines)
    print(f"Located {len(sports_map)} Sports target entries in playlist.")

    http_cache = load_json_cache(SOURCE_CACHE_FILE)
    global RESOLVED_URL_CACHE
    RESOLVED_URL_CACHE = load_resolved_url_cache()

    print("Downloading candidate sources concurrently...")
    raw_sources = download_sources(session, http_cache)
    save_json_cache(SOURCE_CACHE_FILE, http_cache)

    stats.total_sources = len(SOURCE_URLS)
    stats.downloaded_sources = len(raw_sources)
    stats.failed_sources = stats.total_sources - stats.downloaded_sources

    print("Parsing sources...")
    all_parsed_channels = parse_sources(raw_sources)
    stats.parsed_channels = len(all_parsed_channels)

    updates_made = 0

    def process_channel(target_name: str) -> Optional[Tuple[str, int, str, Optional[ScoredCandidate]]]:
        target_norm = normalize(target_name)
        line_idx = sports_map.get(target_norm)
        if line_idx is None:
            return None

        candidates, tier, closest, searched_pool = get_all_matches(target_name, all_parsed_channels)
        if not candidates:
            return (target_name, line_idx, "unmatched", None)

        candidates_to_check = candidates[:MAX_CANDIDATES_TO_TEST]
        scored_candidates: List[ScoredCandidate] = []

        for cand in candidates_to_check:
            res_url = resolve_stream_url(cand.url, session)
            check = get_cached_validation(res_url, session)

            if not check.is_valid:
                stats.invalid_streams.append((target_name, cand.url, check.details))
                continue

            final_cand = replace(cand, url=check.final_url)
            scored = score_candidate(final_cand, check, target_name)
            scored_candidates.append(scored)

        if not scored_candidates:
            return (target_name, line_idx, "no_valid_candidates", None)

        scored_candidates.sort(key=lambda sc: (-sc.score, -int(sc.is_preferred), sc.check.latency, sc.channel.url))

        best = None
        with GLOBAL_ASSIGNED_LOCK:
            for sc in scored_candidates:
                c_url = sc.channel.url
                if c_url not in GLOBAL_ASSIGNED_URLS:
                    GLOBAL_ASSIGNED_URLS.add(c_url)
                    best = sc
                    break
                else:
                    stats.duplicate_urls.append(c_url)

        if best:
            return (target_name, line_idx, "matched", best)
        return (target_name, line_idx, "all_duplicates", None)

    print("Matching and validating streams...")
    with ThreadPoolExecutor(max_workers=MAX_TEST_WORKERS) as executor:
        futures = {executor.submit(process_channel, ch): ch for ch in CHANNELS}
        for future in as_completed(futures):
            res = future.result()
            if not res:
                continue

            target_name, line_idx, status, best_scored = res
            if status == "matched" and best_scored:
                old_url = lines[line_idx]
                new_url = best_scored.channel.url
                if old_url != new_url:
                    lines[line_idx] = new_url
                    updates_made += 1

                stats.matched.append((target_name, new_url, best_scored.channel.source_url))
            else:
                stats.unmatched.append(target_name)

    # Save playlist in-place
    print(f"Saving updated playlist ({updates_made} URLs changed)...")
    PLAYLIST_FILE.write_text(newline.join(lines) + newline, encoding=encoding)

    # Save resolved stream URL cache
    save_resolved_url_cache(RESOLVED_URL_CACHE)

    # Output Run Summary
    elapsed = time.monotonic() - stats.start_time
    print("\n" + "=" * 50)
    print(f" Execution Completed in {elapsed:.2f}s")
    print("=" * 50)
    print(f" Sources Processed : {stats.downloaded_sources}/{stats.total_sources}")
    print(f" Parsed Channels   : {stats.parsed_channels}")
    print(f" Matched Streams   : {len(stats.matched)}")
    print(f" Unmatched/Failed  : {len(stats.unmatched)}")
    print(f" In-Place Updates  : {updates_made}")
    print("=" * 50)


if __name__ == "__main__":
    main()

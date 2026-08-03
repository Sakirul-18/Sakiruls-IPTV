#!/usr/bin/env python3
"""
SAKIRULs IPTV Sports Auto Updater (In-Place Edition, hardened)

This script treats "SAKIRULs IPTV.m3u" as a database, not a document to
regenerate. It NEVER rebuilds the playlist. It loads the file exactly as it
is, locates the existing Sports entries, and swaps only the stream URL for
each channel in the CHANNELS master list. Everything else in the file -
comments, separators, blank lines, category order, alphabetical order,
non-sports categories, EXTINF lines themselves - is left byte-for-byte
untouched. Channels are never inserted, deleted, or reordered.

Matching engine (normalization, exact/token/quality-stripped/merged-token
matching, source-priority routing) is carried over UNCHANGED from the
previous version, per the "preserve architecture" requirements. What changed
in this revision is entirely about *how* the surrounding work is done:

  1. Sports-section detection no longer depends on the "# SPORTS #" comment
     separators. It reads group-title="Sports" directly off each EXTINF
     line, so editing/removing comments can never break it. A legacy
     comment-based fallback only kicks in if the file has zero group-title
     attributes at all.
  2. URL replacement is O(1) per channel via a normalized-name -> line-index
     map built once, instead of rescanning the section for every channel.
  3. Every unique stream URL is HTTP-validated at most once per run
     (STREAM_CACHE), no matter how many channels happen to share it.
  4. Every unique "wrapper" playlist URL is resolved to its real stream URL
     at most once per run (RESOLVED_URL_CACHE).
  5. Duplicate/mirror streams are detected by resolved URL, final redirect
     URL, AND base URL (query stripped), not just resolved URL.
  6. Source-priority scoring is explicit and additive (validation, preferred
     source, latency, content-type stability, redirect success) and the
     highest-scoring candidate wins - not just the first preferred one.
  7. Stream validation now rejects HTML/login/Cloudflare-challenge pages,
     JSON/XML bodies, empty responses, and playlists with no channel
     entries, in addition to the previous checks.
  8. The final (post-redirect) response URL is tracked and used instead of
     the original URL wherever a wrapper redirects straight to the stream.
  9. All sources are downloaded concurrently (ThreadPoolExecutor) instead of
     one-by-one.
  10. Each unique source URL is downloaded once and parsed once, guaranteed
      by construction (dict keyed by URL).
  11. Ties in scoring are broken deterministically (preferred source ->
      lowest latency -> alphabetically first source URL), independent of
      thread completion order.
  12. A best-effort persistent cache under .cache/ stores source ETags /
      Last-Modified headers (for conditional GETs) and resolved wrapper
      URLs (with a TTL) between runs. NOTE: live stream-validation results
      are deliberately NOT persisted across runs - a stream's aliveness is
      exactly the thing this script exists to re-check, so caching that
      across runs would risk re-publishing dead links as "already
      validated." Only the two safe-to-reuse caches above are persisted.
  13. reports/ now gets matched.txt, unmatched.txt, duplicate_urls.txt,
      invalid_streams.txt, source_statistics.txt and performance.txt.
  14. Every source download, parse, wrapper-resolution, and per-channel
      match is individually isolated in try/except - one failure never
      aborts the run. Only a missing/unreadable master playlist file does.
  15. Candidate objects are cloned via dataclasses.replace() instead of
      manual field-by-field reconstruction when only the URL changes.
  16. Saving reproduces the file's original encoding, line-ending style, and
      trailing-newline exactly. Only URL lines are ever modified.
  17. Everything the previous version already did well (normalization,
      token/quality/merged-token matching, source priority definitions,
      parallel validation, in-place editing, CHANNELS as the master list,
      FanCode staying in its own separate script) is unchanged.

FanCode is intentionally out of scope. It is not in CHANNELS, so it is never
searched for, matched, or overwritten by this script. It is updated
separately by update_sports_Fancode.py.
"""

import json
import re
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

# Primary Sports-section detection key (item 1): the group-title attribute
# value we look for on each EXTINF line, matched case-insensitively.
SPORTS_GROUP_TITLE = "sports"

# Legacy fallback only - used solely if the playlist has NO group-title
# attributes anywhere in it. Not relied on as the primary mechanism.
SPORTS_SECTION_TITLE = "SPORTS"
SEPARATOR_PATTERN = re.compile(r'^#\s*=+\s*$')

# Persistent cache (item 12) - see module docstring point 12 for what is and
# isn't cached across runs, and why.
CACHE_DIR = Path(".cache")
SOURCE_CACHE_FILE = CACHE_DIR / "source_cache.json"
RESOLVED_URL_CACHE_FILE = CACHE_DIR / "resolved_urls.json"
RESOLVED_URL_TTL_SECONDS = 6 * 3600  # wrapper->stream mappings are re-checked every 6h at most

REPORTS_DIR = Path("reports")

# HTTP & Concurrency Tuning
REQUEST_TIMEOUT = 6
DOWNLOAD_TIMEOUT = 30
MAX_CANDIDATES_TO_TEST = 8
MAX_TEST_WORKERS = 30
MAX_DOWNLOAD_WORKERS = 16
VALIDATION_READ_BYTES = 8192

# ---------------------------------------------------------------------------
# Source Definitions & Priorities (UNCHANGED - item 17)
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
      'https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/refs/heads/main/combined-playlist.m3u',
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
# item 10 guard: guarantee no accidental duplicate entries so nothing is ever
# downloaded/parsed twice even if this list is edited carelessly in future.
SOURCE_URLS = list(dict.fromkeys(_SOURCE_URLS_RAW))

# Source Priority Engine (UNCHANGED - item 17)
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
    """Raw technical validation result for a URL - independent of which
    channel asked for it. This is what STREAM_CACHE stores (item 3), so the
    same URL is validated over HTTP at most once per run no matter how many
    channels share it."""
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
    """Everything needed to write reports/ at the end of the run (item 13)."""
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
# Name Normalization & Matching Engine
# UNCHANGED (item 17) - normalization, tokenizing, quality stripping, token/
# merged-token containment matching, and source-priority routing are exactly
# as before. Only the code around this engine (I/O, caching, scoring,
# validation, concurrency) has been redesigned.
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
# Persistent cache helpers (item 12)
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
# HTTP helpers
# ---------------------------------------------------------------------------
def get_http_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=MAX_TEST_WORKERS, pool_maxsize=MAX_TEST_WORKERS)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_base_url(url: str) -> str:
    """Strips query string and fragment (item 5) so mirrors that differ only
    by a token/query param are still recognized as the same underlying
    stream for duplicate detection."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _download_one_source(url: str, session: requests.Session, http_cache: dict) -> Tuple[str, str]:
    """Downloads a single source using conditional GET against the
    persistent ETag/Last-Modified cache (item 12), so unchanged playlists
    aren't re-transferred. Isolated in its own try/except (item 14) - one
    bad source never aborts the run."""
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
    """Downloads every candidate source in SOURCE_URLS *simultaneously*
    (item 9). Each unique URL is fetched exactly once (item 10, guaranteed by
    the dict key). A single source failing never blocks the others
    (item 14)."""
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
    """Parses a downloaded m3u blob into ChannelData records. (unchanged)"""
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
    """Parses every downloaded source exactly once (item 10). A malformed
    source is isolated and skipped rather than aborting the run (item 14)."""
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
                # A wrapper can redirect straight to the real stream instead
                # of just linking to it in its body (item 8).
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
    """Resolves 'wrapper' playlists down to the real stream URL. Cached
    globally (item 4) so the same wrapper is never downloaded twice in a
    run, however many channels reference it."""
    with RESOLVED_URL_CACHE_LOCK:
        cached = RESOLVED_URL_CACHE.get(url)
    if cached is not None:
        return cached
    resolved = _resolve_stream_url_uncached(url, session)
    with RESOLVED_URL_CACHE_LOCK:
        RESOLVED_URL_CACHE.setdefault(url, resolved)
    return resolved


# ---------------------------------------------------------------------------
# Stream validation (item 7) & scoring (item 6)
# ---------------------------------------------------------------------------
CHALLENGE_MARKERS = (
    "checking your browser", "cf-browser-verification", "just a moment",
    "attention required", "cloudflare", "ddos protection by", "jschl_answer",
    "captcha",
)
LOGIN_MARKERS = (
    'type="password"', "name=\"password\"", "id=\"password\"",
    "sign in to continue", "please log in", "please login", "session expired",
)
EXPIRY_MARKERS = ("token expired", "link expired", "expired token", "url expired", "access denied")
STABLE_CONTENT_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl", "video/", "mpeg")


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
    """Performs a single live HTTP check on a candidate stream URL. Returns
    only the raw technical result (validity/latency/content-type/final
    redirect URL) - scoring against a channel's preferences happens
    separately in score_candidate(), so this result can be safely shared
    across every channel pointing at the same URL (item 3)."""
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
        # Note: deliberately NOT treating a ".m3u8" URL extension alone as
        # "media" - a .m3u8 URL can still resolve to an empty/comment-only
        # playlist, which must be caught by the has_extinf/has_stream_inf
        # checks above rather than waved through on extension alone.
        is_media = "video/" in c_type or "mpeg" in c_type or url.lower().endswith((".ts", ".mp4", ".mkv"))

        if not (has_extinf or has_stream_inf or is_media):
            non_comment_lines = [ln for ln in text_content.splitlines() if ln.strip() and not ln.strip().startswith("#")]
            if not non_comment_lines:
                return StreamCheckResult(False, latency, c_type, final_url, "Rejected: Playlist with no channel entries")
            return StreamCheckResult(False, latency, c_type, final_url, "Rejected: Unrecognized stream signature")

        return StreamCheckResult(True, latency, c_type, final_url, "Valid stream")
    except Exception as e:
        return StreamCheckResult(False, 0.0, "", url, f"Rejected: Exception ({e})")


def validate_stream_cached(url: str, session: requests.Session) -> StreamCheckResult:
    """Wraps validate_stream() with the global STREAM_CACHE (item 3) so
    every unique URL is only ever hit over HTTP once per run."""
    with STREAM_CACHE_LOCK:
        cached = STREAM_CACHE.get(url)
    if cached is not None:
        return cached
    result = validate_stream(url, session)
    with STREAM_CACHE_LOCK:
        STREAM_CACHE.setdefault(url, result)
    return result


def score_candidate(check: StreamCheckResult, is_preferred: bool, had_redirect: bool) -> float:
    """Additive scoring (item 6): successful validation, preferred source,
    latency, content-type stability, and redirect success all contribute.
    The highest total wins - not just the first preferred source seen."""
    if not check.is_valid:
        return -1.0
    score = 1000.0  # successful validation
    if is_preferred:
        score += 500.0
    speed_bonus = max(0.0, 200.0 - check.latency * 40.0)
    score += speed_bonus
    if any(marker in check.content_type for marker in STABLE_CONTENT_TYPES):
        score += 100.0
    if had_redirect:
        score += 50.0
    return round(score, 2)


def _pick_best_stream(
    candidates: List[ChannelData], preferred_sources: List[str], session: requests.Session, stats: RunStats
) -> Optional[ChannelData]:
    """Resolves, deduplicates, validates (in parallel, each URL at most
    once), scores, and deterministically picks the best live stream for one
    channel's candidate pool."""
    if not candidates:
        return None

    # --- Resolve wrappers + dedupe by resolved URL and base URL (item 5) ---
    seen_resolved: Set[str] = set()
    seen_base: Set[str] = set()
    unique_candidates: List[ChannelData] = []
    for cand in candidates:
        with GLOBAL_ASSIGNED_LOCK:
            already_assigned = cand.url in GLOBAL_ASSIGNED_URLS
        resolved_url = resolve_stream_url(cand.url, session)
        with GLOBAL_ASSIGNED_LOCK:
            already_assigned = already_assigned or resolved_url in GLOBAL_ASSIGNED_URLS
        base = get_base_url(resolved_url)

        if already_assigned:
            continue
        if resolved_url in seen_resolved or base in seen_base:
            stats.duplicate_urls.append(resolved_url)
            continue

        seen_resolved.add(resolved_url)
        seen_base.add(base)
        # item 15: clone via dataclasses.replace instead of manual field copy
        unique_candidates.append(replace(cand, url=resolved_url))
        if len(unique_candidates) >= MAX_CANDIDATES_TO_TEST:
            break

    if not unique_candidates:
        return None

    # --- Validate every unique URL exactly once, in parallel (item 3) ---
    scored: List[ScoredCandidate] = []
    with ThreadPoolExecutor(max_workers=MAX_TEST_WORKERS) as executor:
        future_to_cand = {executor.submit(validate_stream_cached, c.url, session): c for c in unique_candidates}
        for future in as_completed(future_to_cand):
            c = future_to_cand[future]
            try:
                check = future.result()
            except Exception as e:
                check = StreamCheckResult(False, 0.0, "", c.url, f"Rejected: Exception ({e})")

            if not check.is_valid:
                stats.invalid_streams.append((c.name, c.url, check.details))
                continue

            # dedupe again by final redirect URL's base, in case two
            # different wrapper/base URLs land on the identical CDN stream
            final_base = get_base_url(check.final_url)
            if final_base in seen_base and check.final_url != c.url:
                stats.duplicate_urls.append(check.final_url)
            seen_base.add(final_base)

            is_preferred = c.source_url in preferred_sources
            had_redirect = check.final_url != c.url
            score = score_candidate(check, is_preferred, had_redirect)
            scored.append(ScoredCandidate(c, check, is_preferred, score))

    if not scored:
        return None

    # item 11: deterministic tie-break, independent of thread completion order
    scored.sort(key=lambda s: (-s.score, s.check.latency, s.channel.source_url))
    best = scored[0]

    with GLOBAL_ASSIGNED_LOCK:
        GLOBAL_ASSIGNED_URLS.add(best.channel.url)
    return best.channel


def find_best_match(
    channel_name: str, all_channels: List[ChannelData], session: requests.Session, stats: RunStats
) -> Tuple[Optional[ChannelData], Optional[str]]:
    """Runs the (unchanged) matching engine for one CHANNELS entry and
    validates the surviving candidates, returning the best live stream
    found (or None) plus which match tier it came from."""
    rules = get_source_rules(channel_name)
    preferred_sources = rules.get("lock") or rules.get("prefer") or []

    candidates, tier, _closest, _pool = get_all_matches(channel_name, all_channels)
    best_stream = _pick_best_stream(candidates, preferred_sources, session, stats)

    if best_stream:
        print(f" -> [MATCH] {channel_name} <- {best_stream.name} (tier: {tier})")
    else:
        print(f" -> [FAILED] No valid stream found for {channel_name}")
    return best_stream, tier


# ---------------------------------------------------------------------------
# load_playlist()
# ---------------------------------------------------------------------------
def load_playlist() -> Tuple[List[str], str, bool]:
    """Reads the master playlist file exactly as it is on disk. This is the
    only step in the whole run allowed to abort it (item 14) - everything
    downstream is isolated and failure-tolerant."""
    if not PLAYLIST_FILE.exists():
        raise FileNotFoundError(f"Master playlist not found: {PLAYLIST_FILE}")

    raw = PLAYLIST_FILE.read_bytes()
    line_ending = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8")
    trailing_newline = text.endswith("\n")
    lines = text.splitlines()
    return lines, line_ending, trailing_newline


# ---------------------------------------------------------------------------
# Sports-section detection (item 1) & O(1) index (item 2)
# ---------------------------------------------------------------------------
def find_sports_entries(lines: List[str]) -> List[int]:
    """Returns the line indices of every #EXTINF entry belonging to the
    Sports section.

    Primary method: read group-title="..." straight off each EXTINF line.
    This does not care where entries live in the file and keeps working no
    matter how surrounding comments/separators are edited or removed.

    Fallback: only used if the file has zero group-title attributes
    anywhere. In that case entries are located via the legacy "# SPORTS #"
    comment block, purely for backward compatibility with older playlists.
    """
    primary: List[int] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#EXTINF"):
            continue
        gt_match = GROUP_TITLE_RE.search(stripped)
        if gt_match and gt_match.group(1).strip().lower() == SPORTS_GROUP_TITLE:
            primary.append(i)

    if primary:
        return primary

    print("[WARN] No group-title=\"Sports\" entries found; "
          "falling back to legacy comment-header detection.")
    return _find_sports_entries_legacy(lines)


def _find_sports_entries_legacy(lines: List[str]) -> List[int]:
    """Old comment-separator-based detection, kept only as a fallback for
    playlists with no group-title attributes at all."""
    n = len(lines)
    content_start = None
    i = 0
    while i < n:
        if SEPARATOR_PATTERN.match(lines[i].strip()):
            if i + 1 < n:
                header_text = lines[i + 1].strip().lstrip("#").strip()
                if header_text.upper() == SPORTS_SECTION_TITLE:
                    if i + 2 < n and SEPARATOR_PATTERN.match(lines[i + 2].strip()):
                        content_start = i + 3
                        break
        i += 1

    if content_start is None:
        raise ValueError(
            "Could not find the Sports section (neither group-title=\"Sports\" "
            f"entries nor a '# SPORTS #' comment header) in {PLAYLIST_FILE}. "
            "Aborting without touching the file."
        )

    content_end = content_start
    while content_end < n and not SEPARATOR_PATTERN.match(lines[content_end].strip()):
        content_end += 1

    return [i for i in range(content_start, content_end) if lines[i].strip().startswith("#EXTINF")]


def build_sports_index(lines: List[str], sports_entries: List[int]) -> Dict[str, int]:
    """Maps normalized channel display name -> its #EXTINF line index,
    built once. Turns every one of the CHANNELS lookups into an O(1) dict
    access instead of an O(n) rescan of the section (item 2)."""
    index: Dict[str, int] = {}
    for i in sports_entries:
        stripped = lines[i].strip()
        display_name = stripped.rsplit(",", 1)[-1].strip() if "," in stripped else ""
        norm = normalize(display_name)
        if norm and norm not in index:
            index[norm] = i
    return index


def replace_channel_url(lines: List[str], sports_index: Dict[str, int], channel_name: str, new_url: str) -> bool:
    """O(1) lookup + in-place URL swap (item 2). Only the URL line is ever
    touched - the EXTINF line, its formatting, and everything else in the
    file is left completely alone (item 16)."""
    idx = sports_index.get(normalize(channel_name))
    if idx is None:
        return False
    url_idx = idx + 1
    if url_idx < len(lines) and lines[url_idx].strip() and not lines[url_idx].strip().startswith("#"):
        lines[url_idx] = new_url
        return True
    return False


def save_playlist(lines: List[str], line_ending: str, trailing_newline: bool) -> None:
    """Writes the (in-place modified) lines list back to PLAYLIST_FILE,
    reproducing the original line-ending style and trailing newline exactly
    (item 16)."""
    content = line_ending.join(lines)
    if trailing_newline:
        content += line_ending
    PLAYLIST_FILE.write_bytes(content.encode("utf-8"))


# ---------------------------------------------------------------------------
# Reports (item 13)
# ---------------------------------------------------------------------------
def write_reports(stats: RunStats) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    runtime = time.monotonic() - stats.start_time

    (REPORTS_DIR / "matched.txt").write_text(
        "\n".join(f"{ch} <- {name} (tier: {tier})" for ch, name, tier in stats.matched) + "\n",
        encoding="utf-8",
    )
    (REPORTS_DIR / "unmatched.txt").write_text(
        "\n".join(stats.unmatched) + "\n", encoding="utf-8",
    )
    (REPORTS_DIR / "duplicate_urls.txt").write_text(
        "\n".join(sorted(set(stats.duplicate_urls))) + "\n", encoding="utf-8",
    )
    (REPORTS_DIR / "invalid_streams.txt").write_text(
        "\n".join(f"{ch}\t{url}\t{reason}" for ch, url, reason in stats.invalid_streams) + "\n",
        encoding="utf-8",
    )
    (REPORTS_DIR / "source_statistics.txt").write_text(
        "\n".join(f"{src}\t{count} channels" for src, count in sorted(stats.source_channel_counts.items())) + "\n",
        encoding="utf-8",
    )
    (REPORTS_DIR / "performance.txt").write_text(
        "\n".join([
            f"total_sources={stats.total_sources}",
            f"downloaded={stats.downloaded_sources}",
            f"failed={stats.failed_sources}",
            f"parsed_channels={stats.parsed_channels}",
            f"validated_urls={len(STREAM_CACHE)}",
            f"duplicate_urls_removed={len(set(stats.duplicate_urls))}",
            f"runtime_seconds={runtime:.2f}",
        ]) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main Execution
# ---------------------------------------------------------------------------
def main():
    print("Starting SAKIRULs IPTV Sports Auto Updater (in-place)...")
    stats = RunStats(total_sources=len(SOURCE_URLS))

    # 1. Load the master playlist exactly as-is. Nothing is rebuilt. This is
    #    the only step allowed to abort the whole run (item 14).
    lines, line_ending, trailing_newline = load_playlist()

    # 2. Locate every Sports entry (item 1) and index it once (item 2).
    sports_entries = find_sports_entries(lines)
    sports_index = build_sports_index(lines, sports_entries)
    print(f"Found {len(sports_entries)} Sports entries in {PLAYLIST_FILE}.")

    # 3. Load persistent caches from previous runs (item 12).
    CACHE_DIR.mkdir(exist_ok=True)
    http_cache = load_json_cache(SOURCE_CACHE_FILE)
    global RESOLVED_URL_CACHE
    RESOLVED_URL_CACHE = load_resolved_url_cache()

    # 4. Pull fresh candidate streams from every online source in parallel
    #    (item 9), each isolated from the others' failures (item 14).
    session = get_http_session()
    raw_sources = download_sources(session, http_cache)
    stats.downloaded_sources = len(raw_sources)
    stats.failed_sources = stats.total_sources - len(raw_sources)
    all_channels = parse_sources(raw_sources)
    stats.parsed_channels = len(all_channels)
    for ch in all_channels:
        stats.source_channel_counts[ch.source_url] = stats.source_channel_counts.get(ch.source_url, 0) + 1

    # 5. For every tracked sports channel, find + validate the best live
    #    stream, then swap only its URL via the O(1) index from step 2.
    updated, skipped, failed = 0, 0, 0
    for channel_name in CHANNELS:
        print(f"Processing: {channel_name}")
        try:
            best_stream, tier = find_best_match(channel_name, all_channels, session, stats)
        except Exception as e:
            # item 14: one channel's failure never aborts the run
            print(f" -> [ERROR] Unexpected failure matching '{channel_name}': {e}")
            best_stream, tier = None, None

        if not best_stream:
            failed += 1
            stats.unmatched.append(channel_name)
            continue

        try:
            replaced = replace_channel_url(lines, sports_index, channel_name, best_stream.url)
        except Exception as e:
            print(f" -> [ERROR] Unexpected failure updating '{channel_name}': {e}")
            replaced = False

        if replaced:
            updated += 1
            stats.matched.append((channel_name, best_stream.name, tier or ""))
        else:
            skipped += 1
            stats.unmatched.append(channel_name)
            print(f" -> [SKIPPED] '{channel_name}' has no existing entry in the "
                  f"Sports section of {PLAYLIST_FILE}; not inserting a new one.")

    # 6. Write the same list back. Only the touched URL lines differ.
    save_playlist(lines, line_ending, trailing_newline)

    # 7. Persist caches for the next run and write reports.
    save_json_cache(SOURCE_CACHE_FILE, http_cache)
    save_resolved_url_cache(RESOLVED_URL_CACHE)
    write_reports(stats)

    print("\nDone.")
    print(f"  URLs updated:                   {updated}")
    print(f"  Matched online but not in file: {skipped}")
    print(f"  No valid stream found:          {failed}")
    print(f"Playlist saved in place: {PLAYLIST_FILE}")
    print(f"Reports written to: {REPORTS_DIR}/")


if __name__ == "__main__":
    main()

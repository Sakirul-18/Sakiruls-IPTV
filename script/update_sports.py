#!/usr/bin/env python3
"""
Production-Ready IPTV Sports Auto Updater (update_sports.py)

Updates non-FanCode "Sports" channels in a master M3U playlist by pulling
from a fixed list of directly-linked raw source playlists, matching
channels intelligently (alias table + phrase synonyms + decorative-tag
stripping + a fuzzy token-overlap fallback), validating and scoring
candidate HLS streams, and replacing ONLY the stream URL for a match.
Channel names, EXTINF metadata, groups, and ordering are never touched,
and FanCode channels are always left alone (handled by a separate
fancode.py).

The master playlist is the single source of truth for everything except
the URL of a matched, validated, better-scoring Sports stream.

------------------------------------------------------------------------
Changes in the previous revision (GitHub API discovery removed in favor
of a fixed SOURCE_PLAYLISTS list; one shared ThreadPoolExecutor reused
across all parallel phases; wrapper/stream/normalize caching; source
dedup; retry-enabled session; HEAD-first validation; source-priority
scoring; precompiled regexes):

 1-8. Unchanged from the prior revision -- see git history for details.

Changes in THIS revision:

 9. Source-channel index is now filtered to sports-relevant entries only
    before matching: group-title contains "sport", OR the raw name hits
    a sports keyword, OR normalization already folds it onto a known
    canonical alias (covers names like "Willow TV" that carry no
    sport-related word of their own). Non-sports channels mixed into
    multi-category sources (movies/kids/news) can no longer accidentally
    collide with a sports channel's normalized name.
10. Per-source download health (consecutive failures, last success/
    attempt) is persisted to SOURCE_HEALTH_PATH and reloaded every run --
    this process has no other memory between separate GitHub Actions
    invocations. A source failing DEAD_SOURCE_FAILURE_THRESHOLD runs in a
    row is flagged in the execution report instead of failing silently
    forever with no visibility.
11. Optional per-channel source locking (LOCKED_SOURCES_RAW): pin a
    specific channel to a specific source, bypassing priority/latency
    scoring for that channel only. Falls back to the normal candidate
    pool for that channel if the locked source has no candidate this run.
12. The execution report now includes a per-source download/channel-count
    breakdown and a before/after URL log for every channel actually
    updated this run.
------------------------------------------------------------------------
"""

import concurrent.futures
import html
import json
import logging
import re
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover - very old requests/urllib3
    from requests.packages.urllib3.util.retry import Retry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SportsUpdater")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MASTER_PLAYLIST_URL = "https://raw.githubusercontent.com/Sakirul-18/Sakiruls-IPTV/main/SAKIRULs%20IPTV.m3u"
OUTPUT_PLAYLIST_PATH = "SAKIRULs IPTV.m3u"  # Overwrites master locally / acts as final output

# Fixed list of direct raw playlist URLs. No repository scanning, no
# GitHub API calls -- these are downloaded exactly as given.
SOURCE_PLAYLISTS = [
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
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/KB%20TV%20Playlist%2047%20Channel%20v1.0.m3u",
]

HTTP_TIMEOUT = 15
MAX_WORKERS = 20
MAX_WRAPPER_HOPS = 8
VALIDATION_PEEK_BYTES = 2048

# Per-source health persisted across separate runs -- this process has no
# other memory of previous executions, so consecutive-failure counts have
# to be written out and reloaded to mean anything across time.
SOURCE_HEALTH_PATH = "source_health.json"
DEAD_SOURCE_FAILURE_THRESHOLD = 10

# Pin a specific channel to a specific source, bypassing priority/latency
# scoring for that channel only. Keys are the channel's *display* name as
# it appears in the master playlist's EXTINF line (matched after the same
# normalization used everywhere else, so name variants still match);
# values are the source filename as it appears in SOURCE_PLAYLISTS.
# Empty by default -- nothing is locked unless you add entries here.
LOCKED_SOURCES_RAW = {
    # "Sky Sports F1": "sports-s1.m3u",
    # "T Sports HD": "combine_playlist.m3u",
}


def _build_session():
    """Builds the single shared requests.Session, with an HTTPAdapter
    that automatically retries connection errors, read timeouts, and
    HTTP 500/502/503/504 up to 3 times (small exponential backoff)
    before giving up. Mounted on both http:// and https:// so every
    call made through SESSION -- master/source playlist downloads,
    wrapper hops, and stream validation alike -- benefits from it."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    retry_kwargs = dict(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 503, 504),
        raise_on_status=False,
    )
    try:
        # urllib3 >= 1.26
        retry_strategy = Retry(allowed_methods=frozenset(["GET", "HEAD"]), **retry_kwargs)
    except TypeError:
        # urllib3 < 1.26
        retry_strategy = Retry(method_whitelist=frozenset(["GET", "HEAD"]), **retry_kwargs)

    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=MAX_WORKERS,
        pool_maxsize=MAX_WORKERS * 2,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# Single Session, reused for every HTTP call made anywhere in the program.
SESSION = _build_session()


class StatsTracker:
    """Statistics collector for the end-of-run report.

    Fields updated only from the single main thread -- the master-playlist
    walk itself, and the as_completed() loops that drain worker results
    back on the calling thread -- are plain attributes (channels_scanned,
    channels_updated, channels_unchanged, no_match, broken_streams_rejected,
    source_channels_non_sports_filtered, per_source_counts,
    per_source_downloaded, dead_sources, updates_log).

    Fields written to directly from *inside* functions that are submitted
    into the shared ThreadPoolExecutor and therefore genuinely run on a
    worker thread (playlists_downloaded, wrapper_resolved, wrapper_cache_hits,
    candidates_tested, stream_cache_hits) go through incr() under a lock,
    since "+= 1" is not atomic and concurrent workers could otherwise
    silently lose updates.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self.channels_scanned = 0
        self.channels_updated = 0
        self.channels_unchanged = 0
        self.no_match = 0
        self.wrapper_resolved = 0
        self.wrapper_cache_hits = 0
        self.broken_streams_rejected = 0
        self.source_playlists_configured = 0
        self.playlists_downloaded = 0
        self.source_channels_collected = 0
        self.source_channels_deduplicated = 0
        self.source_channels_non_sports_filtered = 0
        self.candidates_tested = 0
        self.stream_cache_hits = 0
        self.per_source_counts = {}
        self.per_source_downloaded = {}
        self.dead_sources = []
        self.updates_log = []
        self.start_time = time.time()

    def incr(self, field, amount=1):
        with self._lock:
            setattr(self, field, getattr(self, field) + amount)


STATS = StatsTracker()

# ---------------------------------------------------------------------------
# Source health tracking (persisted across runs)
# ---------------------------------------------------------------------------

# Read by the main thread at startup and written back at the end of main();
# updated during the run from inside download_and_parse(), which runs on
# worker threads, so both SOURCE_HEALTH and RUN_SOURCE_STATUS go through
# _source_health_lock -- same reasoning as STATS.incr() above.
SOURCE_HEALTH = {}
RUN_SOURCE_STATUS = {}
_source_health_lock = threading.Lock()


def load_source_health(path):
    """Loads per-source consecutive-failure counts from the previous run.
    Returns {} if the file doesn't exist yet or can't be parsed -- a
    missing/corrupt health file just means every source starts this run
    with a clean record, it never blocks execution."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_source_health(path, health):
    """Persists per-source health so consecutive-failure counts survive
    across separate GitHub Actions runs (this process has no other
    memory of previous runs otherwise)."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(health, f, indent=2, sort_keys=True)
    except Exception as e:
        logger.warning(f"Could not write source health file {path}: {e}")


def record_source_result(url, success):
    """Updates SOURCE_HEALTH and RUN_SOURCE_STATUS for one source
    playlist. Called from inside download_and_parse(), which runs on
    worker threads."""
    now = datetime.now(timezone.utc).isoformat()
    with _source_health_lock:
        RUN_SOURCE_STATUS[url] = success
        record = SOURCE_HEALTH.setdefault(
            url, {"consecutive_failures": 0, "last_success": None, "last_attempt": None}
        )
        record["last_attempt"] = now
        if success:
            record["consecutive_failures"] = 0
            record["last_success"] = now
        else:
            record["consecutive_failures"] = record.get("consecutive_failures", 0) + 1


# ---------------------------------------------------------------------------
# Playlist fetching / parsing
# ---------------------------------------------------------------------------

# Shared by both the source-playlist parser and the master-playlist walker
# so the pattern is compiled once and the extraction logic lives in one place.
EXTINF_NAME_RE = re.compile(r',([^,\n]+)$')
GROUP_TITLE_RE = re.compile(r'group-title="([^"]+)"', re.IGNORECASE)


def extract_name_and_group(extinf_line, default_name=""):
    """Pulls the display name and group-title out of an #EXTINF line."""
    name_match = EXTINF_NAME_RE.search(extinf_line)
    name = name_match.group(1).strip() if name_match else default_name
    group_match = GROUP_TITLE_RE.search(extinf_line)
    group = group_match.group(1).strip() if group_match else ""
    return name, group


def fetch_playlist_content(url):
    """Downloads playlist text content safely."""
    try:
        response = SESSION.get(url, timeout=HTTP_TIMEOUT)
        if response.status_code == 200 and response.text.strip():
            return response.text
    except Exception as e:
        logger.debug(f"Failed downloading playlist {url}: {e}")
    return None


def parse_m3u_content(content, source_url):
    """Parses M3U playlist text into structured channel dictionaries."""
    channels = []
    lines = content.splitlines()
    current_extinf = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            current_extinf = line
        elif not line.startswith("#"):
            if current_extinf:
                name, group = extract_name_and_group(current_extinf, default_name="Unknown")
                channels.append({
                    "name": name,
                    "url": line,
                    "group": group,
                    "source": source_url,
                    "extinf": current_extinf,
                })
                current_extinf = None
    return channels


def download_and_parse(pl_url):
    """Runs inside a worker thread: download one source playlist and parse
    it. Counted into STATS.playlists_downloaded on success, and recorded
    into SOURCE_HEALTH / RUN_SOURCE_STATUS either way so failures are
    visible in this run's report and persist into future runs."""
    content = fetch_playlist_content(pl_url)
    if content:
        STATS.incr("playlists_downloaded")
        record_source_result(pl_url, success=True)
        return parse_m3u_content(content, pl_url)
    record_source_result(pl_url, success=False)
    return []


def download_source_playlists(executor):
    """Downloads every configured source playlist in parallel on the
    shared executor and returns the combined, un-deduplicated channel list."""
    all_channels = []
    futures = {executor.submit(download_and_parse, url): url for url in SOURCE_PLAYLISTS}
    for future in concurrent.futures.as_completed(futures):
        url = futures[future]
        try:
            all_channels.extend(future.result())
        except Exception as e:
            logger.debug(f"Error processing source playlist {url}: {e}")
    return all_channels


# ---------------------------------------------------------------------------
# Channel name normalization (aliases, fuzzy matching, decorative tags)
# ---------------------------------------------------------------------------

# Bracketed 2-4 letter decorative region/language tags -- e.g. "|UK|",
# "[PK]", "┃BD┃" -- carry no channel identity of their own, so the whole
# bracketed span is dropped, not just the bracket characters.
DECORATIVE_BRACKET_RE = re.compile(r'[\|┃\[\(]\s*[A-Za-z]{2,4}\s*[\|┃\]\)]')

# Precompiled once, applied on every normalization instead of being
# recompiled from a raw pattern string each call.
NOISE_RE_LIST = [
    re.compile(r'\b(hd|fhd|uhd|hevc|4k|720p|1080p|2160p|hdr|50fps|60fps)\b', re.IGNORECASE),
    re.compile(r'[\|┃\[\]\(\)\-_/:]'),
]

PHRASE_SYNONYMS = {
    "formula 1": "f1",
    "formula one": "f1",
}

# Known name variants that must fold onto the same canonical key. Add
# entries here (or extend CANONICAL_CHANNELS below for the fuzzy
# fallback) when a new naming variant shows up, rather than loosening
# the noise-stripping regex.
MANUAL_ALIASES = {
    "t sports": "tsports",
    "tsports": "tsports",
    "bangla t sports": "tsports",
    "sony sports ten 1": "sonyten1",
    "sony ten 1": "sonyten1",
    "sonyten1": "sonyten1",
    "sky sports f1": "skysportsf1",
    "sky f1": "skysportsf1",
    "sky sports formula 1": "skysportsf1",
    "skysportsf1": "skysportsf1",
    "willow tv": "willowtv",
    "willowtv": "willowtv",
}

CANONICAL_CHANNELS = {
    "skysportsf1": {"sky", "sports", "f1"},
    "sonyten1": {"sony", "sports", "ten", "1"},
    "tsports": {"t", "sports"},
    "willowtv": {"willow", "tv"},
}
FUZZY_MATCH_THRESHOLD = 0.66


def _fuzzy_match_canonical(tokens):
    """Overlap-coefficient match: an abbreviation ("Sky F1") is a subset
    of the fuller canonical name's token set, which plain Jaccard
    similarity penalizes too heavily for short names."""
    candidate = set(tokens)
    if not candidate:
        return None
    best_key, best_score = None, 0.0
    for key, ref in CANONICAL_CHANNELS.items():
        if not ref:
            continue
        score = len(candidate & ref) / min(len(candidate), len(ref))
        if score > best_score:
            best_key, best_score = key, score
    return best_key if best_score >= FUZZY_MATCH_THRESHOLD else None


def _normalize_channel_name_uncached(name):
    """Normalizes channel names by stripping quality tags, decorative
    bracketed region tags, and punctuation/formatting noise, then folds
    known name variants onto one canonical key via an exact alias match
    (checked before AND after phrase substitution) and, failing that, a
    fuzzy token-overlap match."""
    if not name:
        return ""
    name = html.unescape(name)
    name = DECORATIVE_BRACKET_RE.sub(' ', name)

    for pattern in NOISE_RE_LIST:
        name = pattern.sub(' ', name)

    name = re.sub(r'\s+', ' ', name).strip().lower()

    if name in MANUAL_ALIASES:
        return MANUAL_ALIASES[name]

    for phrase, short in PHRASE_SYNONYMS.items():
        name = name.replace(phrase, short)

    if name in MANUAL_ALIASES:
        return MANUAL_ALIASES[name]

    tokens = name.split()
    concatenated = "".join(tokens)
    if concatenated in MANUAL_ALIASES:
        return MANUAL_ALIASES[concatenated]

    fuzzy = _fuzzy_match_canonical(tokens)
    if fuzzy:
        return fuzzy

    return name


# Normalization only ever runs on the main thread (deduplicate_source_channels
# and the master-playlist walk are both sequential), so this cache needs no
# lock -- unlike WRAPPER_CACHE/STREAM_CACHE below, which worker threads write to.
NORMALIZE_CACHE = {}


def normalize_channel_name(name):
    """Cached wrapper around _normalize_channel_name_uncached: a raw name
    string that repeats across many source playlists (or matches a master
    channel's raw name) is only ever actually normalized once."""
    cached = NORMALIZE_CACHE.get(name)
    if cached is not None:
        return cached
    result = _normalize_channel_name_uncached(name)
    NORMALIZE_CACHE[name] = result
    return result


# Computed once at import time: raw display names from LOCKED_SOURCES_RAW,
# normalized the same way every other channel name is, so the match-time
# lookup is a plain dict get on norm_master_name.
LOCKED_SOURCES = {
    normalize_channel_name(name): source_basename
    for name, source_basename in LOCKED_SOURCES_RAW.items()
}

# Keyword gate for the source-channel index. Deliberately broad -- a false
# positive here just means one extra harmless candidate gets indexed; a
# false negative means a real match is silently dropped. Names that carry
# no sport-related word of their own (e.g. "Willow TV") are still caught
# below via the canonical-alias check, not this list.
SPORT_KEYWORDS = (
    "sport", "cricket", "football", "soccer", "tennis", "f1", "formula",
    "motogp", "golf", "rugby", "boxing", "wwe", "kabaddi", "badminton",
    "volleyball", "hockey", "basketball", "nba", "wrestling",
)


def is_sports_channel(name, group, norm_name):
    """Gate applied when building the source-channel index: group-title
    mentions sport, OR the raw name hits a sports keyword, OR
    normalization already folded it onto a known canonical alias.
    Anything that clears none of these is excluded, so a movie/kids/news
    channel from a mixed-category source can't accidentally collide with
    a sports channel's normalized name."""
    if "sport" in (group or "").lower():
        return True
    lname = name.lower()
    if any(kw in lname for kw in SPORT_KEYWORDS):
        return True
    if norm_name in CANONICAL_CHANNELS or norm_name in MANUAL_ALIASES.values():
        return True
    return False


def deduplicate_source_channels(channels):
    """Removes exact duplicate source entries -- same normalized channel
    name AND same URL -- collected from overlapping playlists (several of
    the configured sources mirror each other). Also stashes the normalized
    name on each surviving channel dict so it's computed only once per
    channel instead of a second time when building the name -> candidates
    index right after this."""
    seen = set()
    deduped = []
    for ch in channels:
        norm_name = normalize_channel_name(ch["name"])
        key = (norm_name, ch["url"])
        if key in seen:
            continue
        seen.add(key)
        ch["_norm_name"] = norm_name
        deduped.append(ch)
    STATS.source_channels_deduplicated = len(channels) - len(deduped)
    return deduped


# ---------------------------------------------------------------------------
# Wrapper resolution
# ---------------------------------------------------------------------------

def is_wrapper_url(url):
    """Determines if a URL is likely a wrapper playlist/HTML page rather
    than a direct media file."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.endswith((".m3u", ".m3u8", ".txt", ".php", ".asp", ".html", ".htm")) or not path.endswith((".ts", ".m3u8", ".mp4")):
        return True
    return False


_HLS_MEDIA_TAGS = ("#EXT-X-TARGETDURATION", "#EXT-X-MEDIA-SEQUENCE", "#EXT-X-STREAM-INF", "#EXT-X-VERSION")


def _looks_like_hls_manifest(text):
    """True for an actual HLS manifest (master or media playlist) -- the
    final, playable stream -- as opposed to a channel-list playlist or an
    HTML/text wrapper page that merely points at one."""
    return "#EXTM3U" in text and any(tag in text for tag in _HLS_MEDIA_TAGS)


_EMBEDDED_M3U8_RE = re.compile(r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*', re.IGNORECASE)


def _resolve_wrapper_url_uncached(url):
    """
    Follows at most MAX_WRAPPER_HOPS redirects/wrapper hops to find the
    underlying HLS stream.

    An HLS *media* playlist also starts with "#EXTM3U" and also uses
    "#EXTINF:" (one per segment, each lasting seconds). Treating that as
    "a channel list to parse for more links" would explode a
    correctly-resolved stream into its individual, fast-rotating segment
    files and could return one of those as if it were a stable channel
    URL. This checks for real HLS manifest markers FIRST and stops
    immediately if found, since that means we've already arrived; it only
    keeps unwrapping for plain-text redirects and HTML embed pages, and
    gives up (rather than guesses) if it lands on an unrelated
    multi-channel playlist.
    """
    current_url = url
    visited = set()

    for _ in range(MAX_WRAPPER_HOPS):
        if current_url in visited:
            break
        visited.add(current_url)

        try:
            resp = SESSION.get(current_url, timeout=HTTP_TIMEOUT)
        except Exception:
            break

        if resp.status_code != 200:
            break

        text = resp.text.strip()

        if _looks_like_hls_manifest(text):
            return current_url

        if "#EXTM3U" in text:
            # A channel-list playlist, not a single-stream wrapper.
            # Give up rather than explode into every channel in it.
            break

        if text.startswith("http://") or text.startswith("https://"):
            next_url = next((l.strip() for l in text.splitlines() if l.strip().startswith("http")), None)
            if not next_url or next_url == current_url:
                break
            current_url = next_url
            continue

        match = _EMBEDDED_M3U8_RE.search(text)
        if match and match.group(0) != current_url:
            current_url = match.group(0)
            continue

        break

    return current_url


# WRAPPER_CACHE is read AND written from inside worker-thread functions
# (resolve_candidate(), submitted to the shared executor), so both the
# lookup and the store go through the lock -- unlike NORMALIZE_CACHE above.
WRAPPER_CACHE = {}
_wrapper_cache_lock = threading.Lock()


def resolve_wrapper_url(url):
    """Cached wrapper: if this exact wrapper URL was already resolved
    earlier in the run (by any thread, for any channel), reuse that
    result instead of resolving it again."""
    with _wrapper_cache_lock:
        if url in WRAPPER_CACHE:
            STATS.incr("wrapper_cache_hits")
            return WRAPPER_CACHE[url]

    resolved = _resolve_wrapper_url_uncached(url)

    with _wrapper_cache_lock:
        WRAPPER_CACHE[url] = resolved
    return resolved


# ---------------------------------------------------------------------------
# Stream validation
# ---------------------------------------------------------------------------

def _validate_and_test_stream_uncached(url):
    """Validates and tests an HLS stream for availability, validity, and
    latency.

    Tries a cheap HEAD first: a HEAD response has no body, so an
    obviously-dead link (403/404/5xx) -- or one that never answers at
    all -- can be rejected without downloading a single byte of stream
    data. Servers that don't support/allow HEAD simply fall through to
    the GET-based check below, which remains the source of truth for
    everything HEAD can't tell us: Cloudflare/HTML rejection and HLS
    manifest detection both require an actual body, so that GET still
    reads only a small bounded chunk via iter_content (never resp.text,
    which would read/decode the entire, potentially unbounded, live body
    just to peek at the first couple KB) and always closes the response.
    """
    if not url or not url.startswith("http"):
        return False, 0

    start_time = time.time()

    try:
        head_resp = SESSION.head(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if head_resp.status_code in (403, 404, 500, 502, 503, 504):
            return False, 0
    except Exception:
        pass  # HEAD not supported / failed -- fall back to GET entirely

    resp = None
    try:
        resp = SESSION.get(url, timeout=HTTP_TIMEOUT, stream=True)
        if resp.status_code in (403, 404, 500, 502, 503, 504):
            return False, 0

        content_bytes = b""
        for chunk in resp.iter_content(chunk_size=VALIDATION_PEEK_BYTES):
            content_bytes += chunk
            if len(content_bytes) >= VALIDATION_PEEK_BYTES:
                break
        content = content_bytes.decode("utf-8", errors="ignore").lower()

        if "cloudflare" in content or "<html" in content:
            return False, 0

        if ".m3u8" in url or "#extm3u" in content or "ext-x-stream-inf" in content or "extinf" in content:
            latency = int((time.time() - start_time) * 1000)
            return True, latency

    except Exception:
        pass
    finally:
        if resp is not None:
            resp.close()

    return False, 0


# Same reasoning as WRAPPER_CACHE: read and written from inside worker-thread
# functions (test_candidate(), submitted to the shared executor).
STREAM_CACHE = {}
_stream_cache_lock = threading.Lock()


def validate_and_test_stream(url):
    """Cached wrapper: if this exact stream URL was already tested
    earlier in the run, reuse that result instead of testing it again."""
    with _stream_cache_lock:
        if url in STREAM_CACHE:
            STATS.incr("stream_cache_hits")
            return STREAM_CACHE[url]

    result = _validate_and_test_stream_uncached(url)

    with _stream_cache_lock:
        STREAM_CACHE[url] = result
    return result


# ---------------------------------------------------------------------------
# Source priority + scoring
# ---------------------------------------------------------------------------

# Preferred source order, most trusted first, matched against a source
# playlist's lowercase, percent-decoded filename. Anything not listed here
# ("Other sources") scores 0 on this axis.
SOURCE_PRIORITY_ORDER = [
    "combine_playlist.m3u",
    "sports-s1.m3u",
    "sports-s2.m3u",
    "sports-s7.m3u",
    "new-sports-fast.m3u",
    "all.m3u",
    "world-1.m3u",
    "crichd.m3u",
    "bd.m3u",
    "bdix.m3u",
    "github auto update channel.m3u",
]

# Each step down the priority list is worth far more than the largest
# possible combined HTTPS + latency bonus (20 + 50 = 70 at most, see
# score_candidate() below), so a higher-priority source always outranks a
# lower-priority one regardless of how fast or secure the lower-priority
# candidate tests out to be.
_PRIORITY_STEP = 1000
SOURCE_PRIORITY_SCORES = {
    fname: (len(SOURCE_PRIORITY_ORDER) - idx) * _PRIORITY_STEP
    for idx, fname in enumerate(SOURCE_PRIORITY_ORDER)
}


def get_source_basename(source_url):
    """Returns the lowercase, percent-decoded filename a source channel
    came from, for source-priority lookups."""
    path = urlparse(source_url).path
    basename = path.rsplit("/", 1)[-1]
    return unquote(basename).lower()


def source_priority_score(source_url):
    return SOURCE_PRIORITY_SCORES.get(get_source_basename(source_url), 0)


def score_candidate(candidate_url, source_url, latency):
    """Scores a validated stream candidate. Order of influence, most to
    least significant: source priority, then HTTPS, then latency --
    stream validity itself is already a hard gate, since only candidates
    that passed validate_and_test_stream() ever reach this function."""
    score = source_priority_score(source_url)
    if candidate_url.startswith("https://"):
        score += 20
    if latency > 0:
        score += max(0, 50 - int(latency / 20))
    return score


# ---------------------------------------------------------------------------
# Per-channel candidate resolution
# ---------------------------------------------------------------------------

def find_best_replacement(matching_candidates, executor):
    """For one matched master channel: resolve every candidate URL
    (wrapper resolution runs in parallel on the shared executor and is
    cached, so a wrapper shared by several candidates -- or seen for an
    earlier channel entirely -- is only ever actually resolved once),
    dedupe down to one entry per resolved URL keeping its
    highest-priority source, validate all of them in parallel, and
    return the (highest-scoring valid url, its source url) pair, or
    (None, None)."""

    def resolve_candidate(raw_url):
        if is_wrapper_url(raw_url):
            STATS.incr("wrapper_resolved")
            return resolve_wrapper_url(raw_url)
        return raw_url

    resolve_futures = {
        executor.submit(resolve_candidate, cand["url"]): cand
        for cand in matching_candidates
    }

    # resolved_url -> source_url of the highest-priority candidate that
    # resolved to it (deduplicates resolved URLs before validation).
    resolved_candidates = {}
    for future in concurrent.futures.as_completed(resolve_futures):
        cand = resolve_futures[future]
        try:
            real_url = future.result()
        except Exception:
            continue
        if not real_url:
            continue

        source_url = cand["source"]
        existing_source = resolved_candidates.get(real_url)
        if existing_source is None or source_priority_score(source_url) > source_priority_score(existing_source):
            resolved_candidates[real_url] = source_url

    if not resolved_candidates:
        return None, None

    def test_candidate(cand_url, source_url):
        STATS.incr("candidates_tested")
        valid, latency = validate_and_test_stream(cand_url)
        if valid:
            return cand_url, source_url, score_candidate(cand_url, source_url, latency)
        return None, None, -1

    test_futures = {
        executor.submit(test_candidate, url, source): url
        for url, source in resolved_candidates.items()
    }

    best_url, best_source, best_score = None, None, -1
    for future in concurrent.futures.as_completed(test_futures):
        try:
            cand_url, source_url, score = future.result()
        except Exception:
            continue
        if cand_url and score > best_score:
            best_score = score
            best_url = cand_url
            best_source = source_url

    return best_url, best_source


# ---------------------------------------------------------------------------
# Execution report
# ---------------------------------------------------------------------------

def print_execution_report():
    execution_time = time.time() - STATS.start_time
    rows = [
        ("Channels Scanned", STATS.channels_scanned),
        ("Channels Updated", STATS.channels_updated),
        ("Channels Unchanged", STATS.channels_unchanged),
        ("No Match Found", STATS.no_match),
        ("Wrapper URLs Resolved", STATS.wrapper_resolved),
        ("Wrapper Cache Hits", STATS.wrapper_cache_hits),
        ("Broken Streams Rejected", STATS.broken_streams_rejected),
        ("Source Playlists Configured", STATS.source_playlists_configured),
        ("Source Playlists Downloaded", STATS.playlists_downloaded),
        ("Source Channel Entries Collected", STATS.source_channels_collected),
        ("Duplicate Source Entries Removed", STATS.source_channels_deduplicated),
        ("Non-Sports Source Entries Filtered", STATS.source_channels_non_sports_filtered),
        ("Stream Candidates Tested", STATS.candidates_tested),
        ("Stream Cache Hits", STATS.stream_cache_hits),
        ("Execution Time (seconds)", f"{execution_time:.2f}"),
    ]
    print("\n" + "=" * 60)
    print("       IPTV SPORTS AUTO UPDATER - EXECUTION REPORT")
    print("=" * 60)
    for label, value in rows:
        print(f" {label:<34}: {value}")
    print("=" * 60)

    print("\n" + "-" * 60)
    print(" PER-SOURCE BREAKDOWN")
    print("-" * 60)
    for url in SOURCE_PLAYLISTS:
        basename = get_source_basename(url)
        downloaded = "Yes" if STATS.per_source_downloaded.get(url) else "Failed"
        count = STATS.per_source_counts.get(url, 0)
        print(f" {basename:<48} Downloaded: {downloaded:<7} Channels: {count}")

    if STATS.dead_sources:
        print("\n" + "-" * 60)
        print(f" WARNING: SOURCES OFFLINE {DEAD_SOURCE_FAILURE_THRESHOLD}+ CONSECUTIVE RUNS")
        print("-" * 60)
        for url in STATS.dead_sources:
            print(f" {get_source_basename(url)} appears permanently offline.")

    if STATS.updates_log:
        print("\n" + "-" * 60)
        print(" CHANNELS UPDATED THIS RUN")
        print("-" * 60)
        for entry in STATS.updates_log:
            print(f" \u2713 {entry['name']}")
            print(f"   Old:    {entry['old_url']}")
            print(f"   New:    {entry['new_url']}")
            print(f"   Source: {entry['source']}")

    print("\n" + "=" * 60)
    print("Update completed successfully!")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("Starting IPTV Sports Auto Updater...")

    logger.info(f"Downloading master playlist from: {MASTER_PLAYLIST_URL}")
    master_content = fetch_playlist_content(MASTER_PLAYLIST_URL)
    if not master_content:
        logger.error("Failed to download master playlist. Aborting execution.")
        sys.exit(1)

    STATS.source_playlists_configured = len(SOURCE_PLAYLISTS)

    # Reload persisted per-source health from the previous run before
    # anything downloads, so consecutive-failure counts carry forward
    # rather than starting from zero every invocation.
    SOURCE_HEALTH.clear()
    SOURCE_HEALTH.update(load_source_health(SOURCE_HEALTH_PATH))
    RUN_SOURCE_STATUS.clear()

    # One executor for the entire run. Source-playlist downloads, wrapper
    # resolution, and stream validation all submit into this same pool
    # instead of spinning up a fresh ThreadPoolExecutor per phase or per
    # channel.
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

        logger.info(f"Downloading {len(SOURCE_PLAYLISTS)} source playlists...")
        all_source_channels = download_source_playlists(executor)
        STATS.source_channels_collected = len(all_source_channels)
        logger.info(f"Collected {len(all_source_channels)} candidate channel entries from sources.")

        per_source_channel_counts = {}
        for ch in all_source_channels:
            per_source_channel_counts[ch["source"]] = per_source_channel_counts.get(ch["source"], 0) + 1
        for url in SOURCE_PLAYLISTS:
            STATS.per_source_counts[url] = per_source_channel_counts.get(url, 0)
            STATS.per_source_downloaded[url] = RUN_SOURCE_STATUS.get(url, False)

        deduped_channels = deduplicate_source_channels(all_source_channels)
        logger.info(
            f"Removed {STATS.source_channels_deduplicated} exact duplicate source entries; "
            f"{len(deduped_channels)} remain."
        )

        source_channels_by_norm_name = {}
        for ch in deduped_channels:
            norm_name = ch["_norm_name"]
            if not norm_name:
                continue
            if not is_sports_channel(ch["name"], ch["group"], norm_name):
                STATS.source_channels_non_sports_filtered += 1
                continue
            source_channels_by_norm_name.setdefault(norm_name, []).append(ch)

        logger.info(
            f"Indexed {sum(len(v) for v in source_channels_by_norm_name.values())} "
            f"sports-relevant entries under {len(source_channels_by_norm_name)} normalized names "
            f"({STATS.source_channels_non_sports_filtered} non-sports entries filtered out)."
        )

        master_lines = master_content.splitlines()
        updated_master_lines = []

        i = 0
        while i < len(master_lines):
            line = master_lines[i].strip()

            if line.startswith("#EXTINF:"):
                current_extinf = line
                current_name, current_group = extract_name_and_group(current_extinf, default_name="")

                updated_master_lines.append(master_lines[i])
                i += 1

                # The next physical line is only "this channel's URL" if it
                # isn't itself the start of the next channel -- a blank
                # placeholder with no URL yet is followed straight by the
                # next #EXTINF, and naively treating that as a URL line
                # would wrongly swallow the next channel's header.
                has_url_line = i < len(master_lines) and not master_lines[i].strip().startswith("#EXTINF:")

                if not has_url_line:
                    STATS.channels_scanned += 1
                    continue

                url_line = master_lines[i].strip()
                STATS.channels_scanned += 1

                is_sports = current_group.lower() == "sports"
                is_fancode = "fancode" in current_name.lower() or "fancode" in url_line.lower()

                if is_sports and not is_fancode:
                    norm_master_name = normalize_channel_name(current_name)
                    matching_candidates = source_channels_by_norm_name.get(norm_master_name, [])

                    if matching_candidates:
                        candidates_to_use = matching_candidates
                        locked_basename = LOCKED_SOURCES.get(norm_master_name)
                        if locked_basename:
                            locked_candidates = [
                                c for c in matching_candidates
                                if get_source_basename(c["source"]) == locked_basename
                            ]
                            if locked_candidates:
                                candidates_to_use = locked_candidates
                            else:
                                logger.debug(
                                    f"Locked source '{locked_basename}' has no candidate for "
                                    f"'{current_name}' this run; falling back to all sources."
                                )

                        best_url, best_source = find_best_replacement(candidates_to_use, executor)
                        if best_url and best_url != url_line:
                            updated_master_lines.append(best_url)
                            STATS.channels_updated += 1
                            STATS.updates_log.append({
                                "name": current_name,
                                "old_url": url_line,
                                "new_url": best_url,
                                "source": get_source_basename(best_source) if best_source else "unknown",
                            })
                        else:
                            if not best_url:
                                STATS.broken_streams_rejected += 1
                            updated_master_lines.append(url_line)
                            STATS.channels_unchanged += 1
                    else:
                        STATS.no_match += 1
                        updated_master_lines.append(url_line)
                        STATS.channels_unchanged += 1
                else:
                    updated_master_lines.append(url_line)
                    if is_sports and is_fancode:
                        logger.debug(f"Skipping FanCode channel: {current_name}")

                i += 1
            else:
                updated_master_lines.append(master_lines[i])
                i += 1

    for url, record in SOURCE_HEALTH.items():
        if record.get("consecutive_failures", 0) >= DEAD_SOURCE_FAILURE_THRESHOLD:
            STATS.dead_sources.append(url)
            logger.warning(
                f"{get_source_basename(url)} has failed {record['consecutive_failures']} "
                f"consecutive runs and appears permanently offline."
            )

    save_source_health(SOURCE_HEALTH_PATH, SOURCE_HEALTH)

    final_playlist_content = "\n".join(updated_master_lines) + "\n"
    with open(OUTPUT_PLAYLIST_PATH, "w", encoding="utf-8") as f:
        f.write(final_playlist_content)

    print_execution_report()


if __name__ == "__main__":
    main()

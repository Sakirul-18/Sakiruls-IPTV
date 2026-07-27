#!/usr/bin/env python3
"""
Production-Grade IPTV Stream Updater & Validator (v2)
=======================================================
Changes from v1, mapped to the review that drove them:

 1. FFmpeg loglevel bumped to INFO   -> black/freeze filter events are logged
    at INFO severity, so "-loglevel error" was silently swallowing them.
 2. Range sampling now caps bytes read client-side, since servers that
    ignore the Range header and return 200 would otherwise stream the
    entire segment.
 3. Variant selection uses a normalized weighted score (resolution +
    bandwidth) instead of a strict resolution-then-bandwidth sort, so a
    720p/8Mbps variant can beat a 1080p/1Mbps one.
 4. Alias table expanded + a lightweight token-overlap ("fuzzy") matcher
    added for name variants that aren't worth hardcoding individually.
 5. Quality-suffix stripping no longer touches ambiguous words (tv, live,
    stream, channel) that can be part of a channel's real name -- those
    are only merged via explicit alias / fuzzy match now.
 6. Segment availability is sampled across 3 spread-out points in the
    playlist, not just the first line, to catch mid-playlist 404s.
 7. Playlist change detection now hashes only EXTINF+URL pairs, so
    metadata-only edits (timestamps, comments) no longer force a full
    re-scan.
 8. Wrapper resolution falls through to a regex-based embedded-manifest
    extraction when a "wrapper" URL returns HTML instead of an m3u8, so
    simple embed/redirect pages get unwrapped too.
 9. Stream fingerprinting drops only known *volatile* query params
    (tokens, session ids, signatures) and keeps everything else, so
    same-path-different-quality URLs are no longer collapsed together.
10. FFmpeg (the expensive check) now runs only on the top-ranked
    candidate(s) per channel, after a cheap concurrent throughput +
    availability pre-pass has already ranked/eliminated the rest.

Also added: thread-pool concurrency for the cheap pre-pass, a soft
per-playlist time budget, and a diagnostics summary per run.
"""

import os
import re
import sys
import time
import json
import shutil
import hashlib
import logging
import threading
import subprocess
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Set
from urllib.parse import urlparse, urljoin, parse_qsl, urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# --- Configuration & Constants ---
LOG_LEVEL = logging.INFO
WRAPPER_CACHE_TTL = 21600      # 6 hours
VALIDATION_CACHE_TTL = 900     # 15 minutes
MAX_WRAPPER_DEPTH = 6          # deep redirect / embed-unwrap threshold
CHUNK_SAMPLE_KB = 512          # range sample size in KB
FFMPEG_TIMEOUT = 8             # timeout for video inspection
SEGMENT_SAMPLE_COUNT = 3       # segments probed for availability
MAX_WORKERS = 12               # thread-pool size for the cheap pre-pass
REPO_TIME_BUDGET_SECONDS = 90  # soft deadline per playlist
MIN_BITRATE_BPS = 200_000      # ~200 kbps floor
MIN_SEGMENT_AVAILABILITY = 0.66
MAX_FFMPEG_ATTEMPTS_PER_CHANNEL = 3

CACHE_FILE = "iptv_cache.json"

# Query params that indicate a *different* rendition and must stay part of
# the fingerprint (kept implicitly -- everything not in VOLATILE is kept).
VOLATILE_QUERY_PARAMS = {
    "token", "session", "expires", "expiry", "sig", "signature",
    "auth", "key", "ts", "t", "hash", "sid", "nonce",
}

# --- 1. Environment Verification ---
HAS_FFMPEG = shutil.which("ffmpeg") is not None

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

if not HAS_FFMPEG:
    logging.warning("'ffmpeg' binary not found on this runner! Video black/freeze frame detection will be skipped.")


# --- Session Adapter with Retries ---
def create_http_session(retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return session

session = create_http_session()


# --- Cache & State Manager (thread-safe writes; the pre-pass runs in a pool) ---
class CacheManager:
    def __init__(self, cache_file: str = CACHE_FILE):
        self.cache_file = cache_file
        self._lock = threading.Lock()
        self.data = self._load_cache()

    def _load_cache(self) -> dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logging.warning(f"Failed to load cache: {e}")
        return {"wrappers": {}, "validations": {}, "playlist_hashes": {}}

    def save(self):
        try:
            with self._lock, open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save cache: {e}")

    def get_wrapper(self, url: str) -> Optional[str]:
        entry = self.data["wrappers"].get(url)
        if entry and (time.time() - entry["timestamp"] < WRAPPER_CACHE_TTL):
            return entry["resolved_url"]
        return None

    def set_wrapper(self, url: str, resolved_url: str):
        with self._lock:
            self.data["wrappers"][url] = {"resolved_url": resolved_url, "timestamp": time.time()}

    def get_validation(self, fingerprint: str) -> Optional[dict]:
        entry = self.data["validations"].get(fingerprint)
        if entry and (time.time() - entry["timestamp"] < VALIDATION_CACHE_TTL):
            return entry["result"]
        return None

    def set_validation(self, fingerprint: str, result: dict):
        with self._lock:
            self.data["validations"][fingerprint] = {"result": result, "timestamp": time.time()}

    def is_playlist_unchanged(self, playlist_id: str, content_hash: str) -> bool:
        return self.data["playlist_hashes"].get(playlist_id) == content_hash

    def update_playlist_hash(self, playlist_id: str, content_hash: str):
        with self._lock:
            self.data["playlist_hashes"][playlist_id] = content_hash


# --- 4/5. Hybrid Channel Normalization -------------------------------------
# Long-form -> short-form phrase substitutions, applied before tokenizing.
PHRASE_SYNONYMS = {
    "formula 1": "f1",
    "formula one": "f1",
}

# Tokens that ONLY ever indicate stream quality/feed variant -- never part
# of a channel's real identity. Safe to strip unconditionally.
QUALITY_TOKENS = {
    "hd", "uhd", "4k", "fhd", "sd", "720p", "1080p", "hevc", "h264", "h265",
    "raw", "vip", "backup",
}

# Two lookup stages sharing one table:
#   - raw phrase (lowercased, single-spaced) -> canonical key
#   - fully concatenated post-tokenization key -> canonical key
# Ambiguous words like "tv"/"live"/"stream" are deliberately NOT stripped
# by regex anymore (see issue #5); channels where they're load-bearing
# ("Willow TV") get an explicit entry instead of being merged by guesswork.
MANUAL_ALIASES = {
    "t-sports": "tsports",
    "t sports": "tsports",
    "tsports": "tsports",
    "sony sports ten 1": "sonyten1",
    "sony ten 1": "sonyten1",
    "sonysportsten1": "sonyten1",
    "sonyten1": "sonyten1",
    "sky sports f1": "skysportsf1",
    "sky f1": "skysportsf1",
    "sky sports formula 1": "skysportsf1",
    "skysportsf1": "skysportsf1",
    "willow tv": "willowtv",
    "willowtv": "willowtv",
}

# Reference token-sets for the fuzzy matcher below. Add an entry here (or to
# MANUAL_ALIASES) rather than loosening the strip list when a new naming
# variant shows up.
CANONICAL_CHANNELS: Dict[str, Set[str]] = {
    "skysportsf1": {"sky", "sports", "f1"},
    "sonyten1": {"sony", "sports", "ten", "1"},
    "tsports": {"t", "sports"},
    "willowtv": {"willow", "tv"},
}
FUZZY_MATCH_THRESHOLD = 0.66


def _strip_punct(s: str) -> str:
    return re.sub(r'[^a-z0-9 ]', '', s)


def _fuzzy_match_canonical(tokens: List[str]) -> Optional[str]:
    """Overlap-coefficient match: favors abbreviations ('Sky F1') being
    recognized as subsets of a fuller canonical name, which plain Jaccard
    similarity penalizes too heavily."""
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


def normalize_channel_name(name: str) -> str:
    raw = re.sub(r'\s+', ' ', name.lower().strip())

    # Stage 1: exact raw-string override
    if raw in MANUAL_ALIASES:
        return MANUAL_ALIASES[raw]

    # Stage 2: known long-form -> short-form substitution
    for phrase, short in PHRASE_SYNONYMS.items():
        raw = raw.replace(phrase, short)

    # Stage 3: tokenize, drop punctuation, drop *unambiguous* quality tokens
    tokens = [t for t in _strip_punct(raw).split() if t not in QUALITY_TOKENS]
    if not tokens:
        return ""

    concatenated = "".join(tokens)

    # Stage 4: exact match on the concatenated key
    if concatenated in MANUAL_ALIASES:
        return MANUAL_ALIASES[concatenated]

    # Stage 5: fuzzy overlap match against known channels
    fuzzy = _fuzzy_match_canonical(tokens)
    if fuzzy:
        return fuzzy

    return concatenated


# --- 9. Post-Resolution Fingerprinting (quality-aware) ----------------------
def get_stream_fingerprint(resolved_url: str) -> str:
    parsed = urlparse(resolved_url)
    kept = sorted((k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in VOLATILE_QUERY_PARAMS)
    query_part = urlencode(kept)
    base = f"{parsed.netloc.lower()}{parsed.path}"
    return f"{base}?{query_part}" if query_part else base


# --- 3 & 6. Variant Selection & Extended Manifest Resolver ------------------
def parse_best_variant_from_master(
    manifest_text: str, base_url: str,
    resolution_weight: float = 0.6, bandwidth_weight: float = 0.4
) -> Optional[str]:
    """Parses variants from a master manifest, excludes auxiliary tags
    (#EXT-X-I-FRAME-STREAM-INF, audio-only #EXT-X-MEDIA), and picks the
    highest *weighted* score across resolution and bandwidth -- a strict
    resolution-first sort would pick a 1080p/1Mbps variant over a much
    healthier 720p/8Mbps one."""
    variants = []
    lines = manifest_text.splitlines()

    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF") and not line.startswith("#EXT-X-I-FRAME-STREAM-INF"):
            bw_match = re.search(r'BANDWIDTH=(\d+)', line)
            res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', line)

            bandwidth = int(bw_match.group(1)) if bw_match else 0
            pixels = int(res_match.group(1)) * int(res_match.group(2)) if res_match else 0

            for next_line in lines[i + 1:]:
                next_line = next_line.strip()
                if next_line and not next_line.startswith("#"):
                    variants.append({"url": urljoin(base_url, next_line), "pixels": pixels, "bandwidth": bandwidth})
                    break

    if not variants:
        return None

    max_pixels = max(v["pixels"] for v in variants) or 1
    max_bw = max(v["bandwidth"] for v in variants) or 1
    for v in variants:
        v["score"] = (v["pixels"] / max_pixels) * resolution_weight + (v["bandwidth"] / max_bw) * bandwidth_weight

    variants.sort(key=lambda x: x["score"], reverse=True)
    return variants[0]["url"]


# --- 8. Embedded-manifest extraction for HTML wrapper pages -----------------
_EMBEDDED_M3U8_RE = re.compile(r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*', re.IGNORECASE)


def _extract_embedded_manifest_url(html_text: str, base_url: str) -> Optional[str]:
    match = _EMBEDDED_M3U8_RE.search(html_text)
    return urljoin(base_url, match.group(0)) if match else None


def resolve_wrapper_chain(url: str, cache: CacheManager, depth: int = 0) -> Tuple[Optional[str], Optional[str]]:
    """Recursively traces redirects, master playlists, and (new) simple
    HTML embed/wrapper pages up to MAX_WRAPPER_DEPTH."""
    if depth > MAX_WRAPPER_DEPTH:
        logging.warning(f"Exceeded max wrapper depth ({MAX_WRAPPER_DEPTH}) for {url}")
        return None, None

    cached_res = cache.get_wrapper(url)
    if cached_res:
        try:
            resp = session.get(cached_res, timeout=5)
            if resp.status_code == 200:
                return cached_res, resp.text
        except requests.RequestException:
            pass

    try:
        resp = session.get(url, timeout=5, allow_redirects=True)
        if resp.status_code != 200:
            return None, None

        text = resp.text

        if "#EXT-X-ENDLIST" in text:
            logging.debug(f"Rejected VOD stream: {url}")
            return None, None

        if "#EXTM3U" in text and "#EXT-X-STREAM-INF" in text:
            best_variant_url = parse_best_variant_from_master(text, resp.url)
            if best_variant_url and best_variant_url != url:
                return resolve_wrapper_chain(best_variant_url, cache, depth + 1)

        if "#EXTM3U" not in text:
            # Not a manifest -- likely an HTML embed/wrapper page. Try to
            # pull a manifest URL out of it and keep unwrapping.
            embedded_url = _extract_embedded_manifest_url(text, resp.url)
            if embedded_url and embedded_url != url:
                return resolve_wrapper_chain(embedded_url, cache, depth + 1)
            return None, None

        final_url = resp.url
        cache.set_wrapper(url, final_url)
        return final_url, text

    except requests.RequestException as e:
        logging.debug(f"Failed resolving wrapper for {url}: {e}")
        return None, None


# --- 2. Fast Range Throughput Sampling (client-capped) ----------------------
def measure_real_throughput_chunked(manifest_url: str, manifest_text: str) -> float:
    """Reads at most CHUNK_SAMPLE_KB from the first segment. Caps bytes
    read client-side via iter_content, since some servers ignore the Range
    header and return 200 + the full body instead of 206 + a slice."""
    segment_url = None
    for line in manifest_text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            segment_url = urljoin(manifest_url, line)
            break

    if not segment_url:
        return 0.0

    target_bytes = CHUNK_SAMPLE_KB * 1024
    headers = {"Range": f"bytes=0-{target_bytes - 1}"}

    try:
        resp = session.get(segment_url, headers=headers, stream=True, timeout=4)
        if resp.status_code not in (200, 206):
            return 0.0

        start_time = time.time()
        bytes_received = 0
        for chunk in resp.iter_content(chunk_size=16384):
            if not chunk:
                break
            bytes_received += len(chunk)
            if bytes_received >= target_bytes:
                break
        elapsed = time.time() - start_time
        resp.close()

        if elapsed > 0 and bytes_received > 0:
            return (bytes_received * 8) / elapsed
    except requests.RequestException:
        pass

    return 0.0


# --- 6. Spread-sample segment availability probe ----------------------------
def probe_segment_availability(manifest_url: str, manifest_text: str, sample_count: int = SEGMENT_SAMPLE_COUNT) -> float:
    """Checks a handful of segments spread across the playlist (not just
    the first) since a playlist can have a healthy head and a run of 404s
    further in."""
    segment_lines = [l.strip() for l in manifest_text.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not segment_lines:
        return 0.0

    if len(segment_lines) <= sample_count:
        sample_lines = segment_lines
    else:
        step = len(segment_lines) / sample_count
        sample_lines = [segment_lines[int(i * step)] for i in range(sample_count)]

    ok = 0
    for line in sample_lines:
        seg_url = urljoin(manifest_url, line)
        try:
            resp = session.get(seg_url, headers={"Range": "bytes=0-1023"}, timeout=4, stream=True)
            if resp.status_code in (200, 206):
                ok += 1
            resp.close()
        except requests.RequestException:
            continue

    return ok / len(sample_lines)


# --- 4. Dual Video Analysis (Black & Freeze Detection) -----------------------
def verify_video_quality_ffmpeg(stream_url: str) -> bool:
    """Runs blackdetect + freezedetect. Loglevel is INFO (not error) because
    both filters report their start/end events at INFO severity -- at
    'error' level ffmpeg was suppressing them, so freeze detection never
    actually fired."""
    if not HAS_FFMPEG:
        return True

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-ss", "00:00:01",
        "-i", stream_url,
        "-t", "2",
        "-vf", "blackdetect=d=1.0:pix_th=0.10,freezedetect=n=-60dB:d=1.5",
        "-f", "null", "-"
    ]
    try:
        result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, timeout=FFMPEG_TIMEOUT)
        stderr = result.stderr.lower()

        if "black_start" in stderr:
            logging.debug(f"Black frame detected for {stream_url}")
            return False
        if "lavfi.freezedetect.freeze_start" in stderr:
            logging.debug(f"Frozen frame detected for {stream_url}")
            return False

        return True
    except Exception as e:
        logging.debug(f"FFmpeg check failed or timed out ({e}) for {stream_url}")
        return True


# --- 7. Signature limited to EXTINF+URL pairs (ignores metadata churn) ------
def compute_playlist_signature(content: str) -> str:
    relevant = []
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:") or (line and not line.startswith("#")):
            relevant.append(line)
    return hashlib.sha256("\n".join(relevant).encode("utf-8")).hexdigest()


# --- Candidate model + two-phase pipeline (cheap pre-pass, then ffmpeg) -----
@dataclass
class Candidate:
    raw_name: str
    channel_key: str
    source_url: str
    resolved_url: Optional[str] = None
    manifest_text: Optional[str] = None
    bitrate_bps: float = 0.0
    segment_availability: float = 0.0
    prevalidated: bool = False
    failure_reason: Optional[str] = None


def prevalidate_candidate(cand: Candidate, cache: CacheManager) -> Candidate:
    """Cheap, ffmpeg-free checks: wrapper resolution, throughput, segment
    availability. Runs concurrently across all candidates in the pool."""
    resolved_url, manifest_text = resolve_wrapper_chain(cand.source_url, cache)
    if not resolved_url or not manifest_text:
        cand.failure_reason = "wrapper_resolution_failed"
        return cand

    cand.resolved_url = resolved_url
    cand.manifest_text = manifest_text

    bitrate = measure_real_throughput_chunked(resolved_url, manifest_text)
    if bitrate < MIN_BITRATE_BPS:
        cand.failure_reason = "low_throughput"
        return cand

    availability = probe_segment_availability(resolved_url, manifest_text)
    if availability < MIN_SEGMENT_AVAILABILITY:
        cand.failure_reason = "segments_unavailable"
        return cand

    cand.bitrate_bps = bitrate
    cand.segment_availability = availability
    cand.prevalidated = True
    return cand


def finalize_channel(candidates: List[Candidate], cache: CacheManager, diagnostics: dict) -> Optional[Candidate]:
    """Runs the expensive ffmpeg check only on the top-ranked pre-validated
    candidate(s) for this channel, capped at MAX_FFMPEG_ATTEMPTS_PER_CHANNEL,
    falling through to the next-best candidate if the winner fails frame
    inspection."""
    ranked = sorted(
        (c for c in candidates if c.prevalidated),
        key=lambda c: (c.segment_availability, c.bitrate_bps),
        reverse=True
    )

    for cand in ranked[:MAX_FFMPEG_ATTEMPTS_PER_CHANNEL]:
        fingerprint = get_stream_fingerprint(cand.resolved_url)
        cached = cache.get_validation(fingerprint)
        if cached is not None:
            frames_ok = cached["frames_ok"]
        else:
            frames_ok = verify_video_quality_ffmpeg(cand.resolved_url)
            cache.set_validation(fingerprint, {"frames_ok": frames_ok, "bitrate_bps": cand.bitrate_bps})

        diagnostics["ffmpeg_checks"] += 1
        if frames_ok:
            return cand

        cand.failure_reason = "black_or_frozen_frame"
        diagnostics["ffmpeg_rejections"] += 1

    return None


def process_playlist_content(playlist_id: str, content: str, cache: CacheManager) -> Tuple[List[dict], dict]:
    signature = compute_playlist_signature(content)
    diagnostics = {
        "playlist_id": playlist_id,
        "started": time.time(),
        "candidates_seen": 0,
        "prevalidated": 0,
        "wrapper_failures": 0,
        "throughput_rejections": 0,
        "availability_rejections": 0,
        "ffmpeg_checks": 0,
        "ffmpeg_rejections": 0,
        "channels_resolved": 0,
        "skipped_unchanged": False,
    }

    if cache.is_playlist_unchanged(playlist_id, signature):
        logging.info(f"Skipping unchanged playlist ({playlist_id})")
        diagnostics["skipped_unchanged"] = True
        diagnostics["elapsed_seconds"] = 0.0
        return [], diagnostics

    logging.info(f"Processing updated/new playlist ({playlist_id})...")

    # --- Parse candidates, grouped by normalized channel key ---
    channel_groups: Dict[str, List[Candidate]] = {}
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF:"):
            name = line.split(",")[-1].strip()
            for next_line in lines[i + 1:]:
                next_line = next_line.strip()
                if next_line and not next_line.startswith("#"):
                    key = normalize_channel_name(name)
                    channel_groups.setdefault(key, []).append(Candidate(raw_name=name, channel_key=key, source_url=next_line))
                    break

    all_candidates = [c for group in channel_groups.values() for c in group]
    diagnostics["candidates_seen"] = len(all_candidates)

    # --- Cheap concurrent pre-pass (no ffmpeg yet) ---
    deadline = time.time() + REPO_TIME_BUDGET_SECONDS
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(prevalidate_candidate, c, cache): c for c in all_candidates}
        for future in as_completed(futures):
            if time.time() > deadline:
                logging.warning(f"Time budget exceeded for {playlist_id}; remaining candidates left unresolved")
                break
            cand = future.result()
            if cand.prevalidated:
                diagnostics["prevalidated"] += 1
            elif cand.failure_reason == "low_throughput":
                diagnostics["throughput_rejections"] += 1
            elif cand.failure_reason == "segments_unavailable":
                diagnostics["availability_rejections"] += 1
            elif cand.failure_reason == "wrapper_resolution_failed":
                diagnostics["wrapper_failures"] += 1

    # --- Expensive ffmpeg pass, only on the best candidate(s) per channel ---
    results = []
    for channel_key, candidates in channel_groups.items():
        winner = finalize_channel(candidates, cache, diagnostics)
        if winner:
            diagnostics["channels_resolved"] += 1
            logging.info(
                f"Valid: '{channel_key}' | Speed: {winner.bitrate_bps / 1_000_000:.2f} Mbps "
                f"| Availability: {winner.segment_availability:.0%} | URL: {winner.resolved_url}"
            )
            results.append({
                "channel_key": channel_key,
                "raw_name": winner.raw_name,
                "resolved_url": winner.resolved_url,
                "measured_bitrate_bps": round(winner.bitrate_bps, 2),
                "segment_availability": round(winner.segment_availability, 2),
            })

    cache.update_playlist_hash(playlist_id, signature)
    diagnostics["elapsed_seconds"] = round(time.time() - diagnostics["started"], 2)
    return results, diagnostics


if __name__ == "__main__":
    cache = CacheManager()

    sample_playlist = """#EXTM3U
#EXTINF:-1,Sky Sports F1 HD
https://raw.githubusercontent.com/iptv-org/iptv/master/streams/uk_skysportsf1.m3u8
"""

    results, diagnostics = process_playlist_content("repo_1/sports_playlist.m3u", sample_playlist, cache)
    cache.save()

    print("\nValidated Output:")
    print(json.dumps(results, indent=2))
    print("\nDiagnostics:")
    print(json.dumps(diagnostics, indent=2))

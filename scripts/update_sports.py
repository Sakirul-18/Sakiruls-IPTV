#!/usr/bin/env python3
"""
SAKIRULs IPTV Sports Auto Updater (Production Release)

Features:
- Master list (CHANNELS) integrity verification.
- Advanced normalization (accents, camelCase, letter-number splits).
- 4-Tier Matching System (Exact, Token, Token Quality-Stripped, Merged-Token).
- Configurable Source Priority Engine (Lock & Prefer routing).
- Deep Stream Validation (M3U8 signature detection, rejects HTML/blocks).
- Weighted stream scoring (prioritizes HLS/Media over raw latency).
- Exact URL deduplication (preserves authentication tokens).
- Timestamped, detailed diagnostic reports.
"""

import os
import re
import time
import unicodedata
import difflib
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")
REPORTS_DIR = Path("reports")
OVERWRITE_REPORTS = False  # Set to True to only keep the latest report files

SPORTS_GROUP = "Sports"

# HTTP & Concurrency Tuning
REQUEST_TIMEOUT = 6
MAX_CANDIDATES_TO_TEST = 8
MAX_TEST_WORKERS = 10
DOWNLOAD_CHUNK_SIZE = 2048

# Create reports directory
REPORTS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Source Definitions & Priorities
# ---------------------------------------------------------------------------

SKY_SPORTS_PREFERRED_SOURCE = "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s2.m3u"
FANCODE_EXCLUSIVE_SOURCE = "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/playlist.m3u"
TSPORTS_SOURCE = "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/combine_playlist.m3u"
SPORTS_S1 = "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/refs/heads/main/sports-s1.m3u"

SOURCE_URLS = [
    SPORTS_S1,
    SKY_SPORTS_PREFERRED_SOURCE,
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/world-1.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/Toffee-playlist/main/ott_navigator.m3u",
    FANCODE_EXCLUSIVE_SOURCE,
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/refs/heads/main/Github%20Auto%20Update%20Channel.m3u",
    TSPORTS_SOURCE,
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/main/CricHD.m3u",
]

# Source Priority Engine
# Keys should be lowercase keywords found in the channel name.
# "lock": Will ONLY use sources in this list.
# "prefer": Will prioritize these sources, but fallback to others if needed.
SOURCE_PRIORITIES = {
    "sky sports": {
        "lock": [SKY_SPORTS_PREFERRED_SOURCE]
    },
    "t sports": {
        "prefer": [TSPORTS_SOURCE]
    },
    "sony sports": {
        "prefer": [SPORTS_S1, SKY_SPORTS_PREFERRED_SOURCE]
    },
    "star sports": {
        "prefer": [SPORTS_S1, SKY_SPORTS_PREFERRED_SOURCE]
    },
    "bein sports": {
        "prefer": [SPORTS_S1, SKY_SPORTS_PREFERRED_SOURCE]
    }
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
}

CHANNELS = [
    "beIN SPORTS 1", "beIN SPORTS 2", "beIN SPORTS 3", "beIN SPORTS 4",
    "beIN SPORTS 5", "beIN SPORTS 6", "BTV World", "DAZN 1", "Das Erste HD",
    "Eurosport 1", "Eurosport 2", "FanCode Cricket 1", "FanCode Cricket 2",
    "FanCode Cricket 3", "FanCode Golf", "FanCode Motorsport 1",
    "FanCode Motorsport 2", "FanCode Tennis", "FIFA+", "LaLiga TV", "LFC TV",
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
class StreamScore:
    url: str
    score: float
    is_valid: bool
    latency: float
    content_type: str
    details: str

# Global Stats & Reports
STATS = {
    "total_master": len(CHANNELS), "verified_found": 0, "verified_missing": 0,
    "matched": 0, "unmatched": 0, "exact": 0, "token": 0,
    "merged": 0, "fancode": 0, "updated": 0, "kept": 0, "failed": 0
}
REPORT_UNMATCHED: List[str] = []
REPORT_CANDIDATES: List[str] = []
REPORT_VERIFICATION: List[str] = []

# ---------------------------------------------------------------------------
# Name Normalization & Matching Engines
# ---------------------------------------------------------------------------

QUALITY_WORDS = {"hd", "fhd", "uhd", "shd", "sd", "4k", "8k", "2k", "hq", "sq", "lq", "fullhd"}
REGION_WORDS = {"uk", "usa", "us", "fr", "de", "es", "it", "ca", "au", "eu", "in", "bd", "nl", "be"}
GENERIC_FILLER = QUALITY_WORDS | REGION_WORDS | {"sports", "sport", "channel", "tv", "the", "live", "plus"}

MIN_MATCH_TOKENS = 2
MIN_SINGLE_TOKEN_LEN = 4
MAX_MERGE_WINDOW = 3

CHAR_TRANSLITERATIONS = {
    "ß": "ss",
    "+": " plus ",
}

def transliterate(name: str) -> str:
    for src, dst in CHAR_TRANSLITERATIONS.items():
        name = name.replace(src, dst)
    return name

def clean_channel_name(name: str) -> str:
    name = transliterate(name)
    name = ''.join(c for c in unicodedata.normalize('NFKD', str(name)) if unicodedata.category(c) != 'Mn')
    
    # Smart token splitting (camelCase and alphanumeric)
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
    if ls == 0 or ls > ll: return False
    for i in range(ll - ls + 1):
        if long_[i:i + ls] == short:
            return True
    return False

def tokens_containment_match(a_tokens: Tuple[str, ...], b_tokens: Tuple[str, ...]) -> bool:
    if not a_tokens or not b_tokens: return False
    shorter, longer = (a_tokens, b_tokens) if len(a_tokens) <= len(b_tokens) else (b_tokens, a_tokens)
    if len(shorter) < MIN_MATCH_TOKENS:
        if len(shorter) != 1 or len(shorter[0]) < MIN_SINGLE_TOKEN_LEN:
            return False
    if not contiguous_subseq(shorter, longer): return False
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

# ---------------------------------------------------------------------------
# Core Playlist Processing
# ---------------------------------------------------------------------------

GROUP_TITLE_RE = re.compile(r'group-title="([^"]*)"', re.IGNORECASE)

def get_http_session() -> requests.Session:
    """Creates a configured requests Session with connection pooling."""
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=MAX_TEST_WORKERS, pool_maxsize=MAX_TEST_WORKERS)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def download_playlist(url: str, session: requests.Session) -> str:
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
        if response.status_code == 200:
            print(f"[OK] Downloaded: {url}")
            return response.text
        print(f"[FAILED] {url} (HTTP {response.status_code})")
    except Exception as e:
        print(f"[ERROR] {url} -> {e}")
    return ""

def parse_m3u(content: str, source_url: str = "") -> List[ChannelData]:
    channels = []
    current_name, current_group = None, ""
    
    for line in content.splitlines():
        line = line.strip()
        if not line: continue

        if line.startswith("#EXTINF"):
            gt_match = GROUP_TITLE_RE.search(line)
            current_group = gt_match.group(1) if gt_match else ""
            if "," in line:
                current_name = line.rsplit(",", 1)[-1].strip()
            else:
                current_name = None

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
                variants=merge_variants(t_tokens_q)
            ))
            current_name = None
            current_group = ""
    return channels

def load_sources() -> List[ChannelData]:
    all_channels = []
    with get_http_session() as session:
        for url in SOURCE_URLS:
            data = download_playlist(url, session)
            if data:
                channels = parse_m3u(data, source_url=url)
                all_channels.extend(channels)
                print(f"Loaded {len(channels)} channels from {url}")
    return all_channels

# ---------------------------------------------------------------------------
# FanCode Logic
# ---------------------------------------------------------------------------

FANCODE_MASTER_RE = re.compile(r"^FanCode\s+([A-Za-z]+)\s*(\d+)?$", re.IGNORECASE)
FANCODE_SOURCE_RE = re.compile(r"fan\s*code[\s\-]*([a-z]+)", re.IGNORECASE)

def parse_master_fancode(channel_name: str) -> Optional[Tuple[str, int]]:
    m = FANCODE_MASTER_RE.match(channel_name.strip())
    if not m: return None
    category = m.group(1).lower()
    index = int(m.group(2)) if m.group(2) else 1
    return category, index

def extract_source_fancode_category(channel: ChannelData) -> Optional[str]:
    for text in (channel.group, channel.name):
        if not text: continue
        m = FANCODE_SOURCE_RE.search(text)
        if m:
            cat = m.group(1).strip().lower()
            if cat: return cat
    return None

def build_fancode_pool(all_channels: List[ChannelData]) -> Dict[str, List[str]]:
    pool = {}
    seen_urls = {}
    for ch in all_channels:
        if ch.source_url != FANCODE_EXCLUSIVE_SOURCE: continue
        category = extract_source_fancode_category(ch)
        if not category: continue
        
        pool.setdefault(category, [])
        seen_urls.setdefault(category, set())
        
        if ch.url not in seen_urls[category]:
            seen_urls[category].add(ch.url)
            pool[category].append(ch.url)

    for category, urls in pool.items():
        print(f"[FANCODE POOL] {category}: {len(urls)} live event(s) found")
    return pool

# ---------------------------------------------------------------------------
# Master Verification
# ---------------------------------------------------------------------------

def verify_master_playlist(playlist_lines: List[str]):
    """Checks the physical M3U file against the expected CHANNELS list."""
    found_in_m3u = []
    
    for line in playlist_lines:
        if line.startswith("#EXTINF") and 'group-title="Sports"' in line:
            if "," in line:
                name = line.rsplit(",", 1)[-1].strip()
                found_in_m3u.append(name)

    master_set = set(CHANNELS)
    found_set = set(found_in_m3u)
    
    missing = master_set - found_set
    unknown = found_set - master_set
    
    # Detect duplicates in M3U
    seen = set()
    duplicates = [x for x in found_in_m3u if x in seen or seen.add(x)]

    REPORT_VERIFICATION.append("=== MASTER PLAYLIST VERIFICATION ===")
    REPORT_VERIFICATION.append(f"Expected Channels : {len(master_set)}")
    REPORT_VERIFICATION.append(f"Found in M3U      : {len(found_in_m3u)}")
    
    if missing:
        REPORT_VERIFICATION.append(f"\n[WARNING] Missing {len(missing)} expected channels from M3U:")
        for m in sorted(missing): REPORT_VERIFICATION.append(f"  - {m}")
        STATS["verified_missing"] = len(missing)
    else:
        REPORT_VERIFICATION.append("\n[OK] All expected master channels exist in the M3U.")

    if unknown:
        REPORT_VERIFICATION.append(f"\n[INFO] Found {len(unknown)} channels in M3U not in master list:")
        for u in sorted(unknown): REPORT_VERIFICATION.append(f"  - {u}")

    if duplicates:
        REPORT_VERIFICATION.append(f"\n[WARNING] Found {len(duplicates)} duplicate entries in M3U:")
        for d in sorted(set(duplicates)): REPORT_VERIFICATION.append(f"  - {d}")

    STATS["verified_found"] = len(found_set)

# ---------------------------------------------------------------------------
# Smarter Matching & Priority Engine
# ---------------------------------------------------------------------------

def get_source_rules(channel_name: str) -> Dict[str, List[str]]:
    """Returns lock/prefer rules based on the channel name."""
    name_lower = channel_name.lower()
    for keyword, rules in SOURCE_PRIORITIES.items():
        if keyword in name_lower:
            return rules
    return {}

def get_all_matches(channel_name: str, all_channels: List[ChannelData]) -> Tuple[List[ChannelData], Optional[str], List[Tuple[float, str, str]], List[ChannelData]]:
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
            # difflib similarity for logging closest matches
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
                if matched_variant: break
                
            if matched_variant:
                merged.append(ch)

        # Sort closest descending
        closest = sorted(closest, key=lambda x: x[0], reverse=True)[:5]
        
        if exact: return exact, "exact", closest
        if tokens_match: return tokens_match, "token", closest
        if tokens_q_match: return tokens_q_match, "token-quality-stripped", closest
        if merged: return merged, "merged-token", closest
        return [], None, closest

    # Pass 1: Preferred / Locked Sources
    preferred_channels = [c for c in all_channels if c.source_url in allowed_sources] if allowed_sources else all_channels
    hits, tier, closest = search_pass(preferred_channels)
    
    if hits:
        return hits, tier, closest, preferred_channels

    # Pass 2: Fallback to non-preferred (only if not locked)
    if not is_locked and allowed_sources:
        non_preferred = [c for c in all_channels if c.source_url not in allowed_sources]
        hits2, tier2, closest2 = search_pass(non_preferred)
        if hits2:
            return hits2, tier2, closest2, non_preferred
        
        # Combine closest if nothing found
        all_closest = sorted(closest + closest2, key=lambda x: x[0], reverse=True)[:5]
        return [], None, all_closest, preferred_channels + non_preferred

    return [], None, closest, preferred_channels

# ---------------------------------------------------------------------------
# Deep Stream Validation & Scoring
# ---------------------------------------------------------------------------

def validate_stream(url: str, session: requests.Session, is_preferred: bool) -> StreamScore:
    """
    Downloads chunk to verify payload.
    Scoring logic prioritizes real HLS data, subtracts latency.
    Highest score wins.
    """
    try:
        start = time.monotonic()
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True, allow_redirects=True)
        latency = time.monotonic() - start
        
        if resp.status_code >= 400:
            return StreamScore(url, 0, False, latency, f"HTTP {resp.status_code}", "Failed HTTP status")
            
        c_type = resp.headers.get("Content-Type", "").lower()
        
        # Immediate rejection of Cloudflare blocks or login pages
        if "text/html" in c_type:
            resp.close()
            return StreamScore(url, 0, False, latency, c_type, "Rejected: HTML Page (Block/Login)")

        # Read the first few KB for deep inspection
        chunk = next(resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE), b"")
        text_content = chunk.decode(errors="ignore")
        resp.close()

        is_hls = "#EXTM3U" in text_content or "#EXTINF" in text_content
        is_media = "video/" in c_type or "mpeg" in c_type or url.endswith((".ts", ".mp4", ".mkv"))

        if not (is_hls or is_media):
            return StreamScore(url, 0, False, latency, c_type, "Rejected: Invalid payload signature")

        # Base score starts at 0. Add bonuses.
        score = 0.0
        if is_hls: score += 1000.0
        if is_media: score += 800.0
        if is_preferred: score += 500.0
        
        # Latency penalty: 1 second = -10 points. 
        # So a 100ms stream is preferred over a 900ms stream of the same type.
        score -= (latency * 10)

        return StreamScore(url, round(score, 2), True, latency, c_type, "Valid Stream")

    except Exception as e:
        return StreamScore(url, 0, False, 0.0, "error", f"Exception: {str(e)}")

def pick_best_stream(candidates: List[ChannelData], preferred_sources: List[str], session: requests.Session) -> Optional[ChannelData]:
    if not candidates: return None
    
    # Exact URL Deduplication (keeps unique query params/tokens intact)
    seen_urls = set()
    unique_candidates = []
    for cand in candidates:
        if cand.url not in seen_urls:
            seen_urls.add(cand.url)
            unique_candidates.append(cand)

    test_candidates = unique_candidates[:MAX_CANDIDATES_TO_TEST]
    if len(test_candidates) == 1:
        return test_candidates[0]

    valid_scores: List[Tuple[StreamScore, ChannelData]] = []
    
    with ThreadPoolExecutor(max_workers=min(MAX_TEST_WORKERS, len(test_candidates))) as executor:
        future_to_cand = {
            executor.submit(validate_stream, c.url, session, c.source_url in preferred_sources): c 
            for c in test_candidates
        }
        
        for future in as_completed(future_to_cand):
            cand = future_to_cand[future]
            try:
                res = future.result()
                if res.is_valid:
                    valid_scores.append((res, cand))
                print(f"    [VALIDATOR] Score: {res.score:>7.2f} | Latency: {res.latency:.2f}s | {res.details} -> {res.url}")
            except Exception:
                print(f"    [VALIDATOR] Exception during test -> {cand.url}")

    if not valid_scores:
        print("    [WARNING] No streams passed validation! Falling back to first candidate.")
        return test_candidates[0]

    # Sort descending by score
    valid_scores.sort(key=lambda x: x[0].score, reverse=True)
    best_score, best_cand = valid_scores[0]
    
    print(f"    -> [WINNER] Selected with score {best_score.score} ({best_cand.source_url})")
    return best_cand

# ---------------------------------------------------------------------------
# Main Update Pipeline
# ---------------------------------------------------------------------------

def read_playlist() -> List[str]:
    if not PLAYLIST_FILE.exists():
        print("Playlist not found!")
        return []
    return PLAYLIST_FILE.read_text(encoding="utf-8").splitlines()

def update_sports_section(lines: List[str], all_channels: List[ChannelData], fancode_pool: Dict[str, List[str]]) -> List[str]:
    output = []
    i = 0
    n = len(lines)
    
    verify_master_playlist(lines)
    
    session = get_http_session()

    while i < n:
        line = lines[i]
        
        if line.startswith("#EXTINF") and 'group-title="Sports"' in line:
            channel_name = line.rsplit(",", 1)[-1].strip()
            output.append(line)
            i += 1

            old_url = None
            if i < n and lines[i].strip() and not lines[i].lstrip().startswith("#"):
                old_url = lines[i]

            fancode_info = parse_master_fancode(channel_name)
            new_url_str = None

            if fancode_info:
                category, index = fancode_info
                pooled_urls = fancode_pool.get(category, [])
                if index - 1 < len(pooled_urls):
                    new_url_str = pooled_urls[index - 1]
                    print(f"[FANCODE FOUND] {channel_name} -> pool[{category}][{index - 1}]")
                    STATS["fancode"] += 1
                    STATS["matched"] += 1
                else:
                    print(f"[FANCODE NOT FOUND] {channel_name} (only {len(pooled_urls)} live)")
                    STATS["unmatched"] += 1
                    REPORT_UNMATCHED.append(f"Channel: {channel_name} | Type: FanCode | Reason: Only {len(pooled_urls)} live in pool.")
            else:
                matches_info, tier, closest, searched_pool = get_all_matches(channel_name, all_channels)
                
                if matches_info:
                    print(f"[{len(matches_info)} MATCH(ES) - {tier}] {channel_name}")
                    STATS["matched"] += 1
                    
                    if "exact" in tier: STATS["exact"] += 1
                    elif "quality" in tier or tier == "token": STATS["token"] += 1
                    elif "merged" in tier: STATS["merged"] += 1

                    rules = get_source_rules(channel_name)
                    pref = rules.get("lock") or rules.get("prefer") or []
                    best_match = pick_best_stream(matches_info, pref, session)
                    new_url_str = best_match.url
                    
                    # Log to candidate report
                    c_report = [f"Master Channel: {channel_name}"]
                    for m in matches_info:
                        c_report.append(f"  - Candidate: {m.name} | Source: {m.source_url.split('/')[-1]}")
                    c_report.append(f"  > Winner: {best_match.source_url.split('/')[-1]} ({new_url_str})")
                    REPORT_CANDIDATES.append("\n".join(c_report))
                
                else:
                    print(f"[NOT FOUND] {channel_name}")
                    STATS["unmatched"] += 1
                    STATS["failed"] += 1
                    
                    u_report = [
                        f"Channel: {channel_name}",
                        f"Searched {len(searched_pool)} allowed sources.",
                        f"Tiers attempted: Exact, Token, Merged-Token"
                    ]
                    if closest:
                        u_report.append("Closest fallback candidates found (Levenshtein ratio):")
                        for score, c_name, src in closest:
                            u_report.append(f"  - {c_name} (Ratio: {score:.2f} | {src.split('/')[-1]})")
                    else:
                        u_report.append("Closest candidates: None")
                    REPORT_UNMATCHED.append("\n".join(u_report))

            final_url = new_url_str if new_url_str else old_url

            if new_url_str:
                STATS["updated"] += 1
            else:
                STATS["kept"] += 1

            if old_url is not None:
                i += 1

            if final_url:
                output.append(final_url)
        else:
            output.append(line)
            i += 1

    session.close()
    return output

def save_playlist(lines: List[str]):
    PLAYLIST_FILE.write_text("\n".join(lines), encoding="utf-8")
    print("\n[SUCCESS] Playlist saved successfully.")

def generate_reports():
    print("\n" + "="*45)
    print("           UPDATE SUMMARY")
    print("="*45)
    print(f"Total Master List Channels : {STATS['total_master']}")
    print(f"Found in physical M3U      : {STATS['verified_found']}")
    print(f"Missing from M3U           : {STATS['verified_missing']}")
    print("-" * 45)
    print(f"Matched Channels           : {STATS['matched']}")
    print(f"Unmatched Channels         : {STATS['unmatched']}")
    print("-" * 45)
    print(f"Exact Matches              : {STATS['exact']}")
    print(f"Token Matches              : {STATS['token']}")
    print(f"Merged-Token Matches       : {STATS['merged']}")
    print(f"FanCode Live Events        : {STATS['fancode']}")
    print("-" * 45)
    print(f"Updated URLs               : {STATS['updated']}")
    print(f"Kept Old URLs (Failed)     : {STATS['kept']}")
    print("="*45)
    
    timestamp = "" if OVERWRITE_REPORTS else "_" + datetime.now().strftime("%Y-%m-%d_%H%M%S")
    
    f_unmatched = REPORTS_DIR / f"unmatched_channels{timestamp}.txt"
    f_candidates = REPORTS_DIR / f"candidate_matches{timestamp}.txt"
    f_verification = REPORTS_DIR / f"master_verification{timestamp}.txt"
    
    f_verification.write_text("\n".join(REPORT_VERIFICATION), encoding="utf-8")
    f_unmatched.write_text("\n\n".join(REPORT_UNMATCHED), encoding="utf-8")
    f_candidates.write_text("\n\n".join(REPORT_CANDIDATES), encoding="utf-8")
    
    print(f"\nDiagnostic reports saved to '{REPORTS_DIR.name}/' directory:")
    print(f" - {f_verification.name}")
    print(f" - {f_unmatched.name}")
    print(f" - {f_candidates.name}")

def main():
    print("Starting Production Sports Updater...")
    all_channels = load_sources()
    if not all_channels:
        print("No sources available. Stopping.")
        return

    playlist = read_playlist()
    if not playlist:
        print("Playlist is empty. Stopping.")
        return

    fancode_pool = build_fancode_pool(all_channels)

    updated_playlist = update_sports_section(
        playlist,
        all_channels,
        fancode_pool
    )

    save_playlist(updated_playlist)
    generate_reports()
    print("Update completed gracefully.")

if __name__ == "__main__":
    main()

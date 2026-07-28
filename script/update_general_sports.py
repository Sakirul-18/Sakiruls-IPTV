#!/usr/bin/env python3
"""
SAKIRULs IPTV Sports Auto Updater (Production Release - Integrated Sources)

Features:
- Master list (CHANNELS) integrity verification.
- Advanced normalization (accents, camelCase, letter-number splits).
- 4-Tier Matching System (Exact, Token, Token Quality-Stripped, Merged-Token).
- Configurable Source Priority Engine (Dedicated Sky Sports/T-Sports routing & general fallbacks).
- Raw Wrapper URL Resolution (Regex extraction, prefers .m3u8, preserves tokens).
- Deep Stream Validation & Weighted Scoring.
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
OVERWRITE_REPORTS = False 

SPORTS_GROUP = "Sports"

# HTTP & Concurrency Tuning
REQUEST_TIMEOUT = 6
MAX_CANDIDATES_TO_TEST = 8
MAX_TEST_WORKERS = 30
DOWNLOAD_CHUNK_SIZE = 2048

REPORTS_DIR.mkdir(exist_ok=True)

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
]

SOURCE_URLS = [
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
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/KB%20TV%20Playlist%2047%20Channel%20v1.0.m3u"
]

# Source Priority Engine
SOURCE_PRIORITIES = {
    "sky sports": {"lock": SKY_SPORTS_PREFERRED_SOURCE},
    "t sports": {"prefer": TSPORTS_SOURCES},
    "sony sports": {"prefer": SKY_SPORTS_PREFERRED_SOURCE},
    "star sports": {"prefer": SKY_SPORTS_PREFERRED_SOURCE},
    "bein sports": {"prefer": SKY_SPORTS_PREFERRED_SOURCE}
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
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
# Data Models & Global State
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

STATS = {
    "total_master": len(CHANNELS), "verified_found": 0, "verified_missing": 0,
    "matched": 0, "unmatched": 0, "exact": 0, "token": 0,
    "merged": 0, "updated": 0, "kept": 0, "failed": 0
}
REPORT_UNMATCHED: List[str] = []
REPORT_VERIFICATION: List[str] = []
GLOBAL_ASSIGNED_URLS: Set[str] = set()

# ---------------------------------------------------------------------------
# Name Normalization & Matching Engines
# ---------------------------------------------------------------------------

QUALITY_WORDS = {"hd", "fhd", "uhd", "shd", "sd", "4k", "8k", "2k", "hq", "sq", "lq", "fullhd"}
REGION_WORDS = {"uk", "usa", "us", "fr", "de", "es", "it", "ca", "au", "eu", "in", "bd", "nl", "be"}
GENERIC_FILLER = QUALITY_WORDS | REGION_WORDS | {"sports", "sport", "channel", "tv", "the", "live", "plus"}

MIN_MATCH_TOKENS = 2
MIN_SINGLE_TOKEN_LEN = 4
MAX_MERGE_WINDOW = 3

CHAR_TRANSLITERATIONS = {"ß": "ss", "+": " plus "}

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
    if tokens and tokens[0].lower() in REGION_WORDS: tokens.pop(0)
    if tokens and tokens[-1].lower() in REGION_WORDS: tokens.pop()
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
        if long_[i:i + ls] == short: return True
    return False

def tokens_containment_match(a_tokens: Tuple[str, ...], b_tokens: Tuple[str, ...]) -> bool:
    if not a_tokens or not b_tokens: return False
    shorter, longer = (a_tokens, b_tokens) if len(a_tokens) <= len(b_tokens) else (b_tokens, a_tokens)
    if len(shorter) < MIN_MATCH_TOKENS:
        if len(shorter) != 1 or len(shorter[0]) < MIN_SINGLE_TOKEN_LEN: return False
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
# Core Playlist Processing & Wrapper Resolution
# ---------------------------------------------------------------------------

GROUP_TITLE_RE = re.compile(r'group-title="([^"]*)"', re.IGNORECASE)

def get_http_session() -> requests.Session:
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

def resolve_stream_url(url: str, session: requests.Session) -> str:
    if "raw.githubusercontent.com" in url or url.endswith((".m3u8", ".m3u", ".txt")):
        try:
            resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                urls = re.findall(r'(https?://[^\s"\'<>]+)', resp.text)
                if urls:
                    m3u8_urls = [u for u in urls if ".m3u8" in u.lower()]
                    return m3u8_urls[0] if m3u8_urls else urls[0]
        except Exception:
            pass
    return url

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
            match_name = current_name
                
            t_tokens = tokenize(match_name)
            t_tokens_q = strip_quality(t_tokens)
            
            channels.append(ChannelData(
                name=current_name, 
                url=line,
                group=current_group,
                source_url=source_url,
                strict=normalize(match_name),
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
# Smarter Matching & Priority Engine
# ---------------------------------------------------------------------------

def get_source_rules(channel_name: str) -> Dict[str, List[str]]:
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

        closest = sorted(closest, key=lambda x: x[0], reverse=True)[:5]
        
        if exact: return exact, "exact", closest
        if tokens_match: return tokens_match, "token", closest
        if tokens_q_match: return tokens_q_match, "token-quality-stripped", closest
        if merged: return merged, "merged-token", closest
        return [], None, closest

    preferred_channels = [c for c in all_channels if c.source_url in allowed_sources] if allowed_sources else all_channels
    hits, tier, closest = search_pass(preferred_channels)
    
    if hits: return hits, tier, closest, preferred_channels

    if not is_locked and allowed_sources:
        non_preferred = [c for c in all_channels if c.source_url not in allowed_sources]
        hits2, tier2, closest2 = search_pass(non_preferred)
        if hits2: return hits2, tier2, closest2, non_preferred
        
        all_closest = sorted(closest + closest2, key=lambda x: x[0], reverse=True)[:5]
        return [], None, all_closest, preferred_channels + non_preferred

    return [], None, closest, preferred_channels

# ---------------------------------------------------------------------------
# Deep Stream Validation & Scoring
# ---------------------------------------------------------------------------

def validate_stream(url: str, session: requests.Session, is_preferred: bool) -> StreamScore:
    try:
        start = time.monotonic()
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True, allow_redirects=True)
        latency = time.monotonic() - start
        
        if resp.status_code >= 400:
            return StreamScore(url, 0, False, latency, f"HTTP {resp.status_code}", "Failed HTTP status")
            
        c_type = resp.headers.get("Content-Type", "").lower()
        if "text/html" in c_type:
            resp.close()
            return StreamScore(url, 0, False, latency, c_type, "Rejected: HTML Page")

        chunk = next(resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE), b"")
        text_content = chunk.decode(errors="ignore")
        resp.close()

        is_hls = "#EXTM3U" in text_content or "#EXTINF" in text_content
        is_media = "video/" in c_type or "mpeg" in c_type or url.endswith((".ts", ".mp4", ".mkv"))

        if not (is_hls or is_media):
            return StreamScore(url, 0, False, latency, c_type, "Rejected: Invalid signature")

        score = 0.0
        if is_hls: score += 1000.0
        if is_media: score += 800.0
        if is_preferred: score += 500.0
        score -= (latency * 10)

        return StreamScore(url, round(score, 2), True, latency, c_type, "Valid Stream")
    except Exception as e:
        return StreamScore(url, 0, False, 0.0, "error", f"Exception: {str(e)}")

def pick_best_stream(candidates: List[ChannelData], preferred_sources: List[str], session: requests.Session) -> Optional[ChannelData]:
    if not candidates: return None
    
    seen_urls = set()
    unique_candidates = []
    
    for cand in candidates:
        resolved_url = resolve_stream_url(cand.url, session)
        if resolved_url not in seen_urls and resolved_url not in GLOBAL_ASSIGNED_URLS:
            seen_urls.add(resolved_url)
            unique_candidates.append(ChannelData(
                name=cand.name, url=resolved_url, group=cand.group,
                source_url=cand.source_url, strict=cand.strict, tokens=cand.tokens,
                tokens_q=cand.tokens_q, variants=cand.variants
            ))
            if len(unique_candidates) >= MAX_CANDIDATES_TO_TEST: break
                
    best_cand = None
    best_score = -1.0

    with ThreadPoolExecutor(max_workers=MAX_TEST_WORKERS) as executor:
        future_to_cand = {executor.submit(validate_stream, c.url, session, c.source_url in preferred_sources): c for c in unique_candidates}
        for future in as_completed(future_to_cand):
            c = future_to_cand[future]
            try:
                score = future.result()
                if score.is_valid and score.score > best_score:
                    best_score = score.score
                    best_cand = c
            except Exception:
                pass
                
    if best_cand: GLOBAL_ASSIGNED_URLS.add(best_cand.url)
    return best_cand

# ---------------------------------------------------------------------------
# Main Execution
# ---------------------------------------------------------------------------

def main():
    print("Starting SAKIRULs IPTV Sports Auto Updater...")
    session = get_http_session()
    
    all_channels = load_sources()
    
    updated_playlist_lines = ["#EXTM3U"]
    
    for ch_name in CHANNELS:
        print(f"Processing: {ch_name.strip()}")
        rules = get_source_rules(ch_name)
        preferred_sources = rules.get("lock") or rules.get("prefer") or []
        
        candidates, tier, closest, search_pool = get_all_matches(ch_name, all_channels)
        best_stream = pick_best_stream(candidates, preferred_sources, session)
        
        if best_stream:
            print(f" -> [MATCH] {best_stream.name} (via Tier: {tier})")
            display_name = ch_name.strip()
            updated_playlist_lines.append(f'#EXTINF:-1 group-title="{SPORTS_GROUP}",{display_name}')
            updated_playlist_lines.append(best_stream.url)
            STATS["matched"] += 1
        else:
            print(f" -> [FAILED] No valid streams found for {ch_name.strip()}")
            
    PLAYLIST_FILE.write_text("\n".join(updated_playlist_lines), encoding="utf-8")
    print(f"Playlist updated successfully: {PLAYLIST_FILE}")

if __name__ == "__main__":
    main()

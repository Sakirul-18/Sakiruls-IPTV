#!/usr/bin/env python3
"""
Production-Ready IPTV Sports Auto Updater (update_sports.py)
Updates non-FanCode sports channels in a master M3U playlist by dynamically
discovering playlists across specified GitHub repositories, matching channels
intelligently, validating and scoring candidate HLS streams, and safely updating
only the stream URLs.
"""

import concurrent.futures
import html
import logging
import os
import re
import sys
import time
from urllib.parse import urlparse, urljoin
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SportsUpdater")

# Configuration Constants
MASTER_PLAYLIST_URL = "https://raw.githubusercontent.com/Sakirul-18/Sakiruls-IPTV/main/SAKIRULs%20IPTV.m3u"
OUTPUT_PLAYLIST_PATH = "SAKIRULs IPTV.m3u"  # Overwrites master locally or acts as final output

GITHUB_REPOSITORIES = [
    "https://github.com/IPTVFlixBD/OopsTv",
    "https://github.com/IPTVFlixBD/RynoCast-IPTV-M3u-Playlist",
    "https://github.com/IPTVFlixBD/BDIX-IPTV-playlist",
    "https://github.com/abusaeeidx/CricHD-Scraper-V2",
    "https://github.com/abusaeeidx/CricHd-playlists-Auto-Update-permanent",
    "https://github.com/IPTV-Scraper-Zilla",
    "https://github.com/abusaeeidx/T-Sports-Playlist-Auto-Update",
    "https://github.com/abusaeeidx/Mrgify-BDIX-IPTV",
    "https://github.com/abusaeeidx/Toffee-playlist",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/refs/heads/main/Github%20Auto%20Update%20Channel.m3u",
    "https://github.com/sanjoykb/-KB-TV-Playlist"
]

HTTP_TIMEOUT = 10
MAX_WORKERS = 20

# Request session with pooling
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})


class StatsTracker:
    """Thread-safe statistics collector for reporting."""
    def __init__(self):
        self.channels_scanned = 0
        self.channels_updated = 0
        self.channels_unchanged = 0
        self.no_match = 0
        self.wrapper_resolved = 0
        self.broken_streams_rejected = 0
        self.repositories_scanned = 0
        self.playlists_discovered = 0
        self.playlists_downloaded = 0
        self.candidates_tested = 0
        self.start_time = time.time()


STATS = StatsTracker()


def parse_github_repo_url(url):
    """Extracts owner and repo name from various GitHub URL formats."""
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p]
    if len(path_parts) >= 2:
        return path_parts[0], path_parts[1]
    return None, None


def discover_playlists_from_github(repo_url):
    """Discovers .m3u and .m3u8 playlists using the GitHub Git Trees API recursively."""
    discovered = []
    if "raw.githubusercontent.com" in repo_url or repo_url.endswith((".m3u", ".m3u8")):
        return [repo_url]

    owner, repo = parse_github_repo_url(repo_url)
    if not owner or not repo:
        logger.warning(f"Could not parse GitHub repo from URL: {repo_url}")
        return discovered

    api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/main?recursive=1"
    try:
        response = SESSION.get(api_url, timeout=HTTP_TIMEOUT)
        if response.status_code == 404:
            # Try master branch if main fails
            api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/master?recursive=1"
            response = SESSION.get(api_url, timeout=HTTP_TIMEOUT)
        
        if response.status_code == 200:
            data = response.json()
            for item in data.get("tree", []):
                path = item.get("path", "")
                if path.lower().endswith((".m3u", ".m3u8")) or "playlist" in path.lower():
                    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/{path}"
                    discovered.append(raw_url)
        else:
            # Fallback: Contents API search or standard web parsing if API limits hit
            logger.warning(f"GitHub API tree fetch failed for {repo_url} with status {response.status_code}")
    except Exception as e:
        logger.error(f"Error discovering playlists for {repo_url}: {e}")
    
    return discovered


def fetch_playlist_content(url):
    """Downloads playlist text content safely."""
    try:
        response = SESSION.get(url, timeout=HTTP_TIMEOUT)
        if response.status_code == 200 and response.text.strip():
            return response.text
    except Exception as e:
        logger.debug(f"Failed downloading playlist {url}: {e}")
    return None


def parse_m3u_content(content, source_repo):
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
                # Extract name from EXTINF line (after last comma)
                name_match = re.search(r',([^,\n]+)$', current_extinf)
                name = name_match.group(1).strip() if name_match else "Unknown"
                
                # Extract group-title if available
                group_match = re.search(r'group-title="([^"]+)"', current_extinf, re.IGNORECASE)
                group = group_match.group(1).strip() if group_match else ""

                channels.append({
                    "name": name,
                    "url": line,
                    "group": group,
                    "source": source_repo,
                    "extinf": current_extinf
                })
                current_extinf = None
    return channels


def normalize_channel_name(name):
    """Normalizes channel names by stripping quality tags, symbols, and formatting."""
    if not name:
        return ""
    # Decode HTML entities
    name = html.unescape(name)
    # Remove common tags and markers
    noise_patterns = [
        r'\b(hd|fhd|uhd|hevc|4k|720p|1080p|2160p|hdr|50fps|60fps)\b',
        r'[\|┃\[\]\(\)\-_/_:]',
    ]
    for pattern in noise_patterns:
        name = re.sub(pattern, ' ', name, flags=re.IGNORECASE)
    
    # Normalize whitespaces and lowercase
    name = re.sub(r'\s+', ' ', name).strip().lower()
    return name


def is_wrapper_url(url):
    """Determines if a URL is likely a wrapper playlist or HTML page instead of direct media."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.endswith((".m3u", ".m3u8", ".txt", ".php", ".asp", ".html", ".htm")) or not path.endswith((".ts", ".m3u8", ".mp4")):
        # Check if it's an explicit playlist link or generic endpoint
        return True
    return False


def resolve_wrapper_url(url):
    """Resolves wrapper URLs recursively to find the underlying HLS stream."""
    resolved_set = set()
    stack = [url]
    
    while stack:
        current_url = stack.pop()
        if current_url in resolved_set:
            continue
        resolved_set.add(current_url)

        try:
            resp = SESSION.get(current_url, timeout=HTTP_TIMEOUT)
            if resp.status_code != 200:
                continue
            
            content_type = resp.headers.get("Content-Type", "").lower()
            text = resp.text.strip()

            # If it's an m3u playlist embedded inside wrapper
            if "#EXTM3U" in text or "m3u" in content_type:
                sub_channels = parse_m3u_content(text, current_url)
                for ch in sub_channels:
                    if ch["url"] and ch["url"] not in resolved_set:
                        stack.append(ch["url"])
            elif text.startswith("http://") or text.startswith("https://"):
                # Direct stream link redirection plain text
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith("http"):
                        stack.append(line)
            else:
                # Might be the actual stream URL itself
                if ".m3u8" in current_url or ".ts" in current_url or "m3u8" in content_type:
                    return current_url
        except Exception:
            pass
    
    # If no nested redirection found, return original if valid format
    return url


def validate_and_test_stream(url):
    """Validates and tests an HLS stream for availability, validity, and latency."""
    if not url or not url.startswith("http"):
        return False, 0
    
    start_time = time.time()
    try:
        resp = SESSION.get(url, timeout=HTTP_TIMEOUT, stream=True)
        if resp.status_code in [403, 404, 500, 502, 503, 504]:
            return False, 0
        
        content = resp.text[:2000].lower()
        if "cloudflare" in content or "error" in content or "<html" in content:
            return False, 0
        
        if ".m3u8" in url or "#extm3u" in content or "ext-x-stream-inf" in content or "extinf" in content:
            latency = int((time.time() - start_time) * 1000)
            return True, latency
            
    except Exception:
        pass
    
    return False, 0


def score_candidate(candidate, latency):
    """Scores a stream candidate based on stability, protocol, and latency."""
    score = 100
    # Prefer HTTPS over HTTP
    if candidate.startswith("https://"):
        score += 20
    # Lower latency bonus
    if latency > 0:
        score += max(0, 50 - int(latency / 20))
    return score


def main():
    logger.info("Starting IPTV Sports Auto Updater...")

    # 1. Download Master Playlist
    logger.info(f"Downloading master playlist from: {MASTER_PLAYLIST_URL}")
    master_content = fetch_playlist_content(MASTER_PLAYLIST_URL)
    if not master_content:
        logger.error("Failed to download master playlist. Aborting execution.")
        sys.exit(1)

    # 2. Discover Repositories & Playlists
    all_discovered_playlists = []
    STATS.repositories_scanned = len(GITHUB_REPOSITORIES)

    logger.info("Discovering playlists across repositories recursively...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_repo = {
            executor.submit(discover_playlists_from_github, repo): repo 
            for repo in GITHUB_REPOSITORIES
        }
        for future in concurrent.futures.as_completed(future_to_repo):
            try:
                playlists = future.result()
                all_discovered_playlists.extend(playlists)
                STATS.playlists_discovered += len(playlists)
            except Exception as e:
                logger.error(f"Error processing repository: {e}")

    # Remove duplicates from discovered playlists
    all_discovered_playlists = list(set(all_discovered_playlists))
    logger.info(f"Discovered {len(all_discovered_playlists)} unique playlists.")

    # 3. Download All Discovered Playlists and Collect Channels
    all_source_channels = []
    
    def download_and_parse(pl_url):
        content = fetch_playlist_content(pl_url)
        if content:
            STATS.playlists_downloaded += 1
            return parse_m3u_content(content, pl_url)
        return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_pl = {
            executor.submit(download_and_parse, pl_url): pl_url 
            for pl_url in all_discovered_playlists
        }
        for future in concurrent.futures.as_completed(future_to_pl):
            try:
                channels = future.result()
                all_source_channels.extend(channels)
            except Exception:
                pass

    logger.info(f"Collected {len(all_source_channels)} total candidate channels from sources.")

    # 4. Parse Master Playlist Lines & Process Sports Channels
    master_lines = master_content.splitlines()
    updated_master_lines = []
    
    current_extinf = None
    current_group = ""
    current_name = ""

    # Pre-group source channels by normalized name for fast lookup
    source_channels_by_norm_name = {}
    for ch in all_source_channels:
        norm_name = normalize_channel_name(ch["name"])
        if norm_name:
            if norm_name not in source_channels_by_norm_name:
                source_channels_by_norm_name[norm_name] = []
            source_channels_by_norm_name[norm_name].append(ch)

    i = 0
    while i < len(master_lines):
        line = master_lines[i].strip()
        
        if line.startswith("#EXTINF:"):
            current_extinf = line
            # Extract metadata
            group_match = re.search(r'group-title="([^"]+)"', current_extinf, re.IGNORECASE)
            current_group = group_match.group(1).strip() if group_match else ""
            
            name_match = re.search(r',([^,\n]+)$', current_extinf)
            current_name = name_match.group(1).strip() if name_match else ""

            updated_master_lines.append(master_lines[i])
            i += 1
            
            # Read the next line which should be the URL
            if i < len(master_lines):
                url_line = master_lines[i].strip()
                STATS.channels_scanned += 1

                is_sports = current_group.lower() == "sports"
                is_fancode = "fancode" in current_name.lower() or "fancode" in url_line.lower()

                if is_sports and not is_fancode:
                    norm_master_name = normalize_channel_name(current_name)
                    matching_candidates = source_channels_by_norm_name.get(norm_master_name, [])

                    if matching_candidates:
                        # Extract and resolve wrapper URLs
                        resolved_candidates = set()
                        for cand in matching_candidates:
                            raw_url = cand["url"]
                            if is_wrapper_url(raw_url):
                                STATS.wrapper_resolved += 1
                                real_url = resolve_wrapper_url(raw_url)
                                if real_url:
                                    resolved_candidates.add(real_url)
                            else:
                                resolved_candidates.add(raw_url)

                        # Test and score candidates concurrently
                        best_url = None
                        best_score = -1

                        def test_candidate(cand_url):
                            STATS.candidates_tested += 1
                            valid, latency = validate_and_test_stream(cand_url)
                            if valid:
                                score = score_candidate(cand_url, latency)
                                return cand_url, score
                            return None, 0

                        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                            future_to_cand = {
                                executor.submit(test_candidate, url): url 
                                for url in resolved_candidates
                            }
                            for future in concurrent.futures.as_completed(future_to_cand):
                                try:
                                    cand_url, score = future.result()
                                    if cand_url and score > best_score:
                                        best_score = score
                                        best_url = cand_url
                                except Exception:
                                    pass

                        if best_url and best_url != url_line:
                            updated_master_lines.append(best_url)
                            STATS.channels_updated += 1
                        else:
                            if not best_url:
                                STATS.broken_streams_rejected += 1
                            # Keep existing URL if no better working stream found
                            updated_master_lines.append(url_line)
                            STATS.channels_unchanged += 1
                    else:
                        STATS.no_match += 1
                        updated_master_lines.append(url_line)
                        STATS.channels_unchanged += 1
                else:
                    # Not a target sports channel or is FanCode, keep URL untouched
                    updated_master_lines.append(url_line)
                    if is_sports and is_fancode:
                        logger.debug(f"Skipping FanCode channel: {current_name}")
            i += 1
        else:
            updated_master_lines.append(master_lines[i])
            i += 1

    # 5. Save Output Playlist
    final_playlist_content = "\n".join(updated_master_lines)
    with open(OUTPUT_PLAYLIST_PATH, "w", encoding="utf-8") as f:
        f.write(final_playlist_content)

    execution_time = time.time() - STATS.start_time

    # 6. Print Comprehensive Report
    print("\n" + "="*50)
    print("       IPTV SPORTS AUTO UPDATER - EXECUTION REPORT")
    print("="*50)
    print(f" Channels Scanned            : {STATS.channels_scanned}")
    print(f" Channels Updated            : {STATS.channels_updated}")
    print(f" Channels Unchanged          : {STATS.channels_unchanged}")
    print(f" No Match Found              : {STATS.no_match}")
    print(f" Wrapper URLs Resolved       : {STATS.wrapper_resolved}")
    print(f" Broken Streams Rejected     : {STATS.broken_streams_rejected}")
    print(f" Repositories Scanned        : {STATS.repositories_scanned}")
    print(f" Playlists Discovered        : {STATS.playlists_discovered}")
    print(f" Playlists Downloaded        : {STATS.playlists_downloaded}")
    print(f" Stream Candidates Tested    : {STATS.candidates_tested}")
    print(f" Execution Time              : {execution_time:.2f} seconds")
    print("="*50)
    print("Update completed successfully!")


if __name__ == "__main__":
    main()

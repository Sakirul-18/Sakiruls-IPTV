#!/usr/bin/env python3
"""
Production-Ready IPTV Sports Auto Updater (update_sports.py)

Updates non-FanCode "Sports" channels in a master M3U playlist by
discovering playlists across a fixed list of GitHub repositories,
matching channels intelligently (with a real alias/fuzzy matcher, not
just string cleanup), validating and scoring candidate HLS streams,
and replacing ONLY the stream URL for a match. Channel names, EXTINF
metadata, groups, and ordering are never touched, and FanCode channels
are always left alone (handled by a separate fancode.py).

Fixes applied on top of the first-pass version, with the reasoning kept
as comments at each site so the "why" travels with the code:

  1. Repo list had "https://github.com/IPTV-Scraper-Zilla" (missing the
     "abusaeeidx/" owner) -- that entry silently discovered 0 playlists.
  2. discover_playlists_from_github() guessed "main" then "master" for
     the API call, but always built the raw.githubusercontent.com URL
     with a hardcoded "main" regardless of which branch actually
     answered -- so on any master-default repo, every constructed URL
     pointed at a branch that might not have that file. Now it looks up
     the repo's real default_branch and reuses that exact branch name
     everywhere.
  3. Playlist discovery matched any path containing the word "playlist"
     (READMEs, docs, images) in addition to real .m3u/.m3u8 files.
     Tightened to the actual extensions only.
  4. resolve_wrapper_url() treated ANY "#EXTM3U" response as "a channel
     list to parse for more links to follow" -- but a live HLS *media*
     playlist also starts with "#EXTM3U" and also uses "#EXTINF:" (one
     per segment). That meant a correctly-resolved stream could get
     exploded into its individual, seconds-lived segment files, and a
     segment URL -- not a stable channel URL -- could be returned as
     "resolved". Now it checks for real HLS manifest markers first and
     stops immediately if found; it only keeps unwrapping for plain
     redirects and HTML embed pages, capped at a small hop limit.
  5. validate_and_test_stream() called resp.text on a stream=True
     response to peek at content -- .text still reads and decodes the
     ENTIRE body first. If a candidate URL is a live video segment
     rather than a text manifest, that can hang or pull down a large
     amount of data just to check the first couple KB. Now it reads a
     small bounded chunk via iter_content and always closes the
     response.
  6. The master-playlist walk assumed the line right after any #EXTINF
     is always that channel's URL (or a blank placeholder). For a
     channel with a blank placeholder immediately followed by another
     #EXTINF (no URL and no blank line in between), that next channel's
     own header would get swallowed and reprinted as if it were "the
     URL", losing that channel from the output entirely. Now it checks
     whether the next line is actually the start of another channel
     before treating it as a URL line.
  7. Channel-name normalization only stripped quality suffixes and lone
     punctuation characters -- it didn't drop bracketed region tags
     ("┃UK┃ Sky Sports F1" kept "UK" as a real word), didn't fold
     "Formula 1" -> "F1", and had no alias/fuzzy layer at all, so the
     exact examples in the spec ("T Sports"/"TSports"/"T-Sports"/
     "Bangla T Sports"/"TSports HD" all matching one channel) did not
     actually match each other. Added bracket-tag stripping, a phrase
     synonym table, a manual alias table, and a fallback fuzzy
     token-overlap matcher.
  8. STATS counters were plain ints incremented from inside functions
     that run concurrently in a ThreadPoolExecutor (playlists_downloaded,
     candidates_tested) -- "+= 1" isn't atomic, so some increments could
     be silently lost under real concurrency, making the printed report
     inaccurate. Those two now go through a lock-protected incr().
  9. GitHub API calls were unauthenticated (60 requests/hour/IP, shared
     across everyone on that GitHub Actions IP range). If a GITHUB_TOKEN
     is available in the environment (GitHub Actions provides one
     automatically), it's now sent -- but ONLY on api.github.com calls,
     never attached to the shared session used for fetching arbitrary
     third-party stream/wrapper URLs.
"""

import concurrent.futures
import html
import logging
import os
import re
import sys
import threading
import time
from urllib.parse import urlparse
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
OUTPUT_PLAYLIST_PATH = "SAKIRULs IPTV.m3u"  # Overwrites master locally / acts as final output

GITHUB_REPOSITORIES = [
    "https://github.com/IPTVFlixBD/OopsTv",
    "https://github.com/IPTVFlixBD/RynoCast-IPTV-M3u-Playlist",
    "https://github.com/IPTVFlixBD/BDIX-IPTV-playlist",
    "https://github.com/abusaeeidx/CricHD-Scraper-V2",
    "https://github.com/abusaeeidx/CricHd-playlists-Auto-Update-permanent",
    "https://github.com/abusaeeidx/IPTV-Scraper-Zilla",
    "https://github.com/abusaeeidx/T-Sports-Playlist-Auto-Update",
    "https://github.com/abusaeeidx/Mrgify-BDIX-IPTV",
    "https://github.com/abusaeeidx/Toffee-playlist",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/refs/heads/main/Github%20Auto%20Update%20Channel.m3u",
    "https://github.com/sanjoykb/-KB-TV-Playlist",
]

HTTP_TIMEOUT = 10
MAX_WORKERS = 20
MAX_WRAPPER_HOPS = 8
VALIDATION_PEEK_BYTES = 2048

# Sent ONLY on api.github.com calls (see discover_playlists_from_github),
# never attached to the shared SESSION used for third-party stream/wrapper
# URLs -- a token has no business leaving GitHub's API.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_API_HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# Request session with pooling
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})


class StatsTracker:
    """Statistics collector for the end-of-run report. Most counters are
    only ever touched from the single main thread (the master-playlist
    walk is sequential), but playlists_downloaded and candidates_tested
    are updated from inside functions that run in a ThreadPoolExecutor,
    so those two go through incr() under a lock."""
    def __init__(self):
        self._lock = threading.Lock()
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

    def incr(self, field, amount=1):
        with self._lock:
            setattr(self, field, getattr(self, field) + amount)


STATS = StatsTracker()


def parse_github_repo_url(url):
    """Extracts owner and repo name from various GitHub URL formats."""
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p]
    if len(path_parts) >= 2:
        return path_parts[0], path_parts[1]
    return None, None


def discover_playlists_from_github(repo_url):
    """Discovers .m3u/.m3u8 playlists using the GitHub Git Trees API
    recursively, against the repo's actual default branch."""
    discovered = []
    if "raw.githubusercontent.com" in repo_url or repo_url.endswith((".m3u", ".m3u8")):
        return [repo_url]

    owner, repo = parse_github_repo_url(repo_url)
    if not owner or not repo:
        logger.warning(f"Could not parse GitHub repo from URL: {repo_url}")
        return discovered

    # Look up the real default branch instead of guessing -- and reuse
    # that same branch name for the raw.githubusercontent URLs below.
    branch = None
    try:
        repo_info_resp = SESSION.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers=GITHUB_API_HEADERS, timeout=HTTP_TIMEOUT
        )
        if repo_info_resp.status_code == 200:
            branch = repo_info_resp.json().get("default_branch")
    except Exception as e:
        logger.debug(f"Could not fetch repo info for {repo_url}: {e}")

    branches_to_try = [branch] if branch else []
    branches_to_try += [b for b in ("main", "master") if b not in branches_to_try]

    tree = None
    used_branch = None
    for candidate_branch in branches_to_try:
        try:
            response = SESSION.get(
                f"https://api.github.com/repos/{owner}/{repo}/git/trees/{candidate_branch}?recursive=1",
                headers=GITHUB_API_HEADERS, timeout=HTTP_TIMEOUT
            )
            if response.status_code == 200:
                tree = response.json()
                used_branch = candidate_branch
                break
            if response.status_code == 403:
                logger.warning(f"GitHub API rate-limited while scanning {repo_url}")
        except Exception as e:
            logger.error(f"Error discovering playlists for {repo_url}: {e}")

    if tree is None or used_branch is None:
        logger.warning(f"Could not read the file tree for {repo_url} (tried branches: {branches_to_try})")
        return discovered

    for item in tree.get("tree", []):
        path = item.get("path", "")
        if path.lower().endswith((".m3u", ".m3u8")):
            discovered.append(f"https://raw.githubusercontent.com/{owner}/{repo}/{used_branch}/{path}")

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
                name_match = re.search(r',([^,\n]+)$', current_extinf)
                name = name_match.group(1).strip() if name_match else "Unknown"

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


# --- Channel name normalization -------------------------------------------
# Bracketed 2-4 letter decorative region/language tags -- e.g. "|UK|",
# "[PK]", "┃BD┃" -- carry no channel identity of their own, so the whole
# bracketed span is dropped, not just the bracket characters.
DECORATIVE_BRACKET_RE = re.compile(r'[\|┃\[\(]\s*[A-Za-z]{2,4}\s*[\|┃\]\)]')

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


def normalize_channel_name(name):
    """Normalizes channel names by stripping quality tags, decorative
    bracketed region tags, and punctuation/formatting noise, then folds
    known name variants onto one canonical key via an exact alias match
    (checked before AND after phrase substitution) and, failing that, a
    fuzzy token-overlap match."""
    if not name:
        return ""
    name = html.unescape(name)
    name = DECORATIVE_BRACKET_RE.sub(' ', name)

    noise_patterns = [
        r'\b(hd|fhd|uhd|hevc|4k|720p|1080p|2160p|hdr|50fps|60fps)\b',
        r'[\|┃\[\]\(\)\-_/:]',
    ]
    for pattern in noise_patterns:
        name = re.sub(pattern, ' ', name, flags=re.IGNORECASE)

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


def resolve_wrapper_url(url):
    """
    Follows at most MAX_WRAPPER_HOPS redirects/wrapper hops to find the
    underlying HLS stream.

    An HLS *media* playlist also starts with "#EXTM3U" and also uses
    "#EXTINF:" (one per segment, each lasting seconds). Treating that as
    "a channel list to parse for more links" -- like the first pass did
    -- would explode a correctly-resolved stream into its individual,
    fast-rotating segment files and could return one of those as if it
    were a stable channel URL. This checks for real HLS manifest markers
    FIRST and stops immediately if found, since that means we've already
    arrived; it only keeps unwrapping for plain-text redirects and HTML
    embed pages, and gives up (rather than guesses) if it lands on an
    unrelated multi-channel playlist.
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


def validate_and_test_stream(url):
    """Validates and tests an HLS stream for availability, validity, and
    latency. Reads only a small bounded chunk of the body -- a candidate
    URL may point straight at a live video segment rather than a text
    manifest, and resp.text would read/decode the entire (potentially
    unbounded, live) body just to peek at the first couple KB."""
    if not url or not url.startswith("http"):
        return False, 0

    start_time = time.time()
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


def score_candidate(candidate, latency):
    """Scores a stream candidate based on protocol and latency."""
    score = 100
    if candidate.startswith("https://"):
        score += 20
    if latency > 0:
        score += max(0, 50 - int(latency / 20))
    return score


def main():
    logger.info("Starting IPTV Sports Auto Updater...")

    logger.info(f"Downloading master playlist from: {MASTER_PLAYLIST_URL}")
    master_content = fetch_playlist_content(MASTER_PLAYLIST_URL)
    if not master_content:
        logger.error("Failed to download master playlist. Aborting execution.")
        sys.exit(1)

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

    all_discovered_playlists = list(set(all_discovered_playlists))
    logger.info(f"Discovered {len(all_discovered_playlists)} unique playlists.")

    all_source_channels = []

    def download_and_parse(pl_url):
        content = fetch_playlist_content(pl_url)
        if content:
            STATS.incr("playlists_downloaded")
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

    master_lines = master_content.splitlines()
    updated_master_lines = []

    source_channels_by_norm_name = {}
    for ch in all_source_channels:
        norm_name = normalize_channel_name(ch["name"])
        if norm_name:
            source_channels_by_norm_name.setdefault(norm_name, []).append(ch)

    i = 0
    while i < len(master_lines):
        line = master_lines[i].strip()

        if line.startswith("#EXTINF:"):
            current_extinf = line
            group_match = re.search(r'group-title="([^"]+)"', current_extinf, re.IGNORECASE)
            current_group = group_match.group(1).strip() if group_match else ""

            name_match = re.search(r',([^,\n]+)$', current_extinf)
            current_name = name_match.group(1).strip() if name_match else ""

            updated_master_lines.append(master_lines[i])
            i += 1

            # The next physical line is only "this channel's URL" if it
            # isn't itself the start of the next channel -- a blank
            # placeholder with no URL yet is followed straight by the
            # next #EXTINF, and the old version would wrongly swallow
            # that next channel's header as if it were this one's URL.
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

                    best_url = None
                    best_score = -1

                    def test_candidate(cand_url):
                        STATS.incr("candidates_tested")
                        valid, latency = validate_and_test_stream(cand_url)
                        if valid:
                            return cand_url, score_candidate(cand_url, latency)
                        return None, 0

                    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                        future_to_cand = {
                            executor.submit(test_candidate, c_url): c_url
                            for c_url in resolved_candidates
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

    final_playlist_content = "\n".join(updated_master_lines) + "\n"
    with open(OUTPUT_PLAYLIST_PATH, "w", encoding="utf-8") as f:
        f.write(final_playlist_content)

    execution_time = time.time() - STATS.start_time

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

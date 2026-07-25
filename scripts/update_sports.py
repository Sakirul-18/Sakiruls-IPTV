#!/usr/bin/env python3
"""
SAKIRULs IPTV Sports Auto Updater

Rules:
- Your channel names are the master list.
- Search sources for matching channels.
- Replace only URLs.
- Never delete missing channels.

Matching strategy (in order of confidence):
  1. Exact match (ignoring case/punctuation).
  2. Token containment: your channel name's words appear as a contiguous
     run inside the source channel's name (or vice versa), e.g. "TNT
     Sports 1" <-> "TNT 1", or "BEIN SPORTS 1" <-> "FR| BEIN SPORTS 1 HD".
  3. Same as #2, but also ignoring quality tags (HD/FHD/UHD/...), e.g.
     "NPO 1 HD" <-> "NPO 1  8K+ UHD".
  A match is only accepted if it shares at least one real, non-generic
  word - this stops junk like a "##### SPORTS HD #####" divider entry
  from matching every "X Sports HD" channel.

FanCode is special: the source doesn't publish "FanCode Cricket 1/2/3"
by name - it publishes whatever match is *currently live* under a
group-title like "Fancode-Cricket". This script pools every URL it
finds per category (Cricket/Golf/Tennis/Motorsport/...) and assigns
them in order to your numbered FanCode slots. This means:
  - FanCode channels only fill in when something in that category is
    actually live/listed on FanCode at the time the script runs.
  - If FanCode adds a channel or category tomorrow, it's picked up
    automatically, with no code changes needed.

Speed test: when a channel has more than one candidate URL (e.g. the
same channel from 3 different regions, or several mirrors), each
candidate is tested concurrently (HTTP HEAD/GET with a short timeout)
and the fastest one that actually responds is used. If none respond,
the first candidate found is used as a best-effort fallback rather
than leaving the channel empty.
"""

from pathlib import Path
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")
SPORTS_GROUP = "Sports"


SKY_SPORTS_PREFERRED_SOURCE = "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s2.m3u"
FANCODE_EXCLUSIVE_SOURCE = "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/playlist.m3u"


SOURCE_URLS = [
    # IPTVFlixBD Sports S1
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/refs/heads/main/sports-s1.m3u",

    # IPTVFlixBD Sports S2
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s2.m3u",

    # IPTVFlixBD World
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/world-1.m3u",

    # Toffee
    "https://raw.githubusercontent.com/abusaeeidx/Toffee-playlist/main/ott_navigator.m3u",

    # FanCode
    "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/playlist.m3u",

    # KB TV
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/refs/heads/main/Github%20Auto%20Update%20Channel.m3u",

    # T Sports
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/combine_playlist.m3u",

    # CricHD
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/main/CricHD.m3u",
]


HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

CHANNELS = [
    "616 Sports 4K",

    "beIN SPORTS 1",
    "beIN SPORTS 2",
    "beIN SPORTS 3",
    "beIN SPORTS 4",
    "beIN SPORTS 5",
    "beIN SPORTS 6",

    "BTV World",
    "Das Erste HD",
    "Eurosport 1",
    "F1 TV Pro",

    "FanCode Cricket 1",
    "FanCode Cricket 2",
    "FanCode Cricket 3",
    "FanCode Tennis",
    "FanCode Motorsport 1",
    "FanCode Golf",
    "FanCode Motorsport 2",

    "FIFA Plus Channel",
    "FOX Sports 1 USA",

    "MotoGP VideoPass",
    "Motorsport.tv",

    "NPO 1 HD",
    "NPO 2 HD",
    "NPO 3 HD",

    "Sky Sports Cricket",
    "Sky Sports F1",
    "Sky Sports Football",
    "Sky Sports Main Event",
    "Sky Sports Premier League",

    "Sony Sports Ten 1",
    "Sony Sports Ten 2",
    "Sony Sports Ten 3",

    "Sports18 1 HD",

    "Star Sports 1",
    "Star Sports 1 Hindi",
    "Star Sports Select 1",
    "Star Sports Select 2",

    "SuperSport Cricket",
    "SuperSport Football",
    "SuperSport Premier League",

    "T Sports HD",
    "Tennis Channel",
    "Tipik HD",

    "TNT Sports 1",
    "TNT Sports 2",
    "TNT Sports 3",

    "TSN 1",
    "TSN 3",

    "TVP Sport HD",
    "USA Network",
    "VRT 1 HD",

    "Willow Cricket HD",
]


# ---------------------------------------------------------------------------
# Name normalization / matching
# ---------------------------------------------------------------------------

QUALITY_WORDS = {
    "hd", "fhd", "uhd", "shd", "sd", "4k", "8k", "2k", "hq", "sq", "lq", "fullhd"
}

REGION_WORDS = {
    "uk", "usa", "us", "fr", "de", "es", "it", "ca", "au", "eu", "in", "bd", "nl", "be"
}

# Words too generic to ever count as "the thing that makes two names match".
GENERIC_FILLER = QUALITY_WORDS | REGION_WORDS | {"sports", "sport", "channel", "tv", "the", "live", "plus"}

# Minimum number of tokens the shorter side must have before we trust a
# containment match at all (avoids single-token noise matches).
MIN_MATCH_TOKENS = 2

# Speed test tuning
MAX_CANDIDATES_TO_TEST = 8
REQUEST_TIMEOUT = 6
MAX_TEST_WORKERS = 8


def clean_channel_name(name):
    """
    Strips region prefixes/suffixes, decorative symbols, and brackets so that
    names like "┃UK┃ SKY SPORTS CRICKET HD" become "SKY SPORTS CRICKET HD".
    """
    # Replace non-alphanumeric decorative characters (bars, brackets, stars, etc.) with space
    cleaned = re.sub(r"[┃\|│║\[\]\(\)\{\}#\-_\*]+", " ", name)
    
    # Remove standalone region tokens at start or end of string
    tokens = cleaned.split()
    if tokens and tokens[0].lower() in REGION_WORDS:
        tokens.pop(0)
    if tokens and tokens[-1].lower() in REGION_WORDS:
        tokens.pop()

    return " ".join(tokens)


def normalize(name):
    """Strict normalization: lowercase, alphanumeric only after cleaning decor."""
    cleaned = clean_channel_name(name)
    return re.sub(r"[^a-z0-9]", "", cleaned.lower())


def tokenize(name):
    """Split into lowercase word/number tokens, dropping punctuation and region noise."""
    cleaned = clean_channel_name(name).lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    tokens = tuple(w for w in cleaned.split() if w and w not in REGION_WORDS)
    return tokens


def strip_quality(tokens):
    return tuple(t for t in tokens if t not in QUALITY_WORDS)


def contiguous_subseq(short, long_):
    """True if `short` appears as a contiguous run inside `long_`."""
    ls, ll = len(short), len(long_)
    if ls == 0 or ls > ll:
        return False
    for i in range(ll - ls + 1):
        if long_[i:i + ls] == short:
            return True
    return False


def tokens_containment_match(a_tokens, b_tokens):
    """
    True if one token sequence is a contiguous run inside the other,
    AND that shared run includes at least one non-generic word.
    """
    if not a_tokens or not b_tokens:
        return False

    shorter, longer = (a_tokens, b_tokens) if len(a_tokens) <= len(b_tokens) else (b_tokens, a_tokens)

    if len(shorter) < MIN_MATCH_TOKENS:
        return False

    if not contiguous_subseq(shorter, longer):
        return False

    return any(t not in GENERIC_FILLER for t in shorter)


# ---------------------------------------------------------------------------
# M3U parsing
# ---------------------------------------------------------------------------

GROUP_TITLE_RE = re.compile(r'group-title="([^"]*)"', re.IGNORECASE)


def download_playlist(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        if response.status_code == 200:
            print(f"[OK] Downloaded: {url}")
            return response.text

        print(f"[FAILED] {url} ({response.status_code})")

    except Exception as e:
        print(f"[ERROR] {url}")
        print(e)

    return ""


def parse_m3u(content, source_url=""):
    """
    Convert M3U into:
    [
        {"name": channel name, "url": stream url, "group": group-title, "source_url": source_url}
    ]

    Uses rsplit on the LAST comma to get the display name, since some
    sources (e.g. FanCode) put an extra comma earlier in the #EXTINF
    line (right after the duration), which would otherwise corrupt a
    naive "split on first comma" parse.
    """

    channels = []

    lines = content.splitlines()
    current_name = None
    current_group = ""

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):

            gt_match = GROUP_TITLE_RE.search(line)
            current_group = gt_match.group(1) if gt_match else ""

            if "," in line:
                current_name = line.rsplit(",", 1)[-1].strip()
            else:
                current_name = None

        elif not line.startswith("#"):

            if current_name:
                channels.append(
                    {
                        "name": current_name,
                        "url": line,
                        "group": current_group,
                        "source_url": source_url,
                    }
                )

            current_name = None
            current_group = ""

    return channels


def load_sources():

    all_channels = []

    for url in SOURCE_URLS:

        data = download_playlist(url)

        if data:

            channels = parse_m3u(data, source_url=url)
            all_channels.extend(channels)

            print(f"Loaded {len(channels)} channels from {url}")

    return all_channels


# ---------------------------------------------------------------------------
# FanCode: pooled-by-category, since the source lists live events, not
# fixed channel names.
# ---------------------------------------------------------------------------

FANCODE_MASTER_RE = re.compile(r"^FanCode\s+([A-Za-z]+)\s*(\d+)?$", re.IGNORECASE)
FANCODE_SOURCE_RE = re.compile(r"fan\s*code[\s\-]*([a-z]+)", re.IGNORECASE)


def parse_master_fancode(channel_name):
    """
    If channel_name is a numbered FanCode master entry (e.g. "FanCode
    Cricket 1", "FanCode Golf"), return (category, index). Otherwise None.
    """
    m = FANCODE_MASTER_RE.match(channel_name.strip())
    if not m:
        return None

    category = m.group(1).lower()
    index = int(m.group(2)) if m.group(2) else 1
    return category, index


def extract_source_fancode_category(channel):
    """
    Figure out a source channel's FanCode category, checking group-title
    first (that's where FanCode-BD actually puts it, e.g.
    group-title="Fancode-Cricket") and falling back to the channel name.
    Generic on purpose - a brand new category tomorrow is picked up
    automatically, no code changes needed.
    """
    for text in (channel.get("group", ""), channel.get("name", "")):
        if not text:
            continue
        m = FANCODE_SOURCE_RE.search(text)
        if m:
            cat = m.group(1).strip().lower()
            if cat:
                return cat
    return None


def build_fancode_pool(all_channels):
    """
    category -> ordered list of deduped URLs, gathered ONLY from the designated FanCode source.
    """
    pool = {}
    seen_urls = {}

    for channel in all_channels:
        if channel.get("source_url") != FANCODE_EXCLUSIVE_SOURCE:
            continue

        category = extract_source_fancode_category(channel)

        if not category:
            continue

        pool.setdefault(category, [])
        seen_urls.setdefault(category, set())

        if channel["url"] not in seen_urls[category]:
            seen_urls[category].add(channel["url"])
            pool[category].append(channel["url"])

    for category, urls in pool.items():
        print(f"[FANCODE POOL] {category}: {len(urls)} channel(s) found")

    return pool


# ---------------------------------------------------------------------------
# General matching
# ---------------------------------------------------------------------------

def get_all_matches(channel_name, all_channels):
    """
    Search sources for name matches, in order of confidence:
      1. exact normalized match
      2. token containment (full tokens)
      3. token containment ignoring quality words (hd/fhd/uhd/...)
    Returns (deduped URL list, tier name) from the first tier with any
    hits, or ([], None) if nothing matched at all.

    Note: Channels starting with "Sky Sports" are restricted ONLY to
    SKY_SPORTS_PREFERRED_SOURCE.
    """
    is_sky_sports = channel_name.strip().lower().startswith("sky sports")

    target_strict = normalize(channel_name)
    target_tokens = tokenize(channel_name)
    target_tokens_q = strip_quality(target_tokens)

    exact_hits, full_token_hits, quality_hits = [], [], []

    for channel in all_channels:
        if is_sky_sports and channel.get("source_url") != SKY_SPORTS_PREFERRED_SOURCE:
            continue

        cand_name = channel["name"]
        cand_strict = normalize(cand_name)

        if cand_strict == target_strict:
            exact_hits.append(channel["url"])
            continue

        cand_tokens = tokenize(cand_name)

        if tokens_containment_match(target_tokens, cand_tokens):
            full_token_hits.append(channel["url"])
            continue

        cand_tokens_q = strip_quality(cand_tokens)
        if tokens_containment_match(target_tokens_q, cand_tokens_q):
            quality_hits.append(channel["url"])

    def dedupe(urls):
        seen = set()
        out = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    if exact_hits:
        return dedupe(exact_hits), "exact"
    if full_token_hits:
        return dedupe(full_token_hits), "token"
    if quality_hits:
        return dedupe(quality_hits), "token-quality-stripped"

    return [], None


# ---------------------------------------------------------------------------
# Speed test: when there's more than one candidate, test them and use
# whichever responds fastest.
# ---------------------------------------------------------------------------

def test_url(url, timeout=REQUEST_TIMEOUT):
    """Return response time in seconds if the URL responds OK, else None."""
    try:
        start = time.monotonic()
        resp = requests.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        elapsed = time.monotonic() - start
        if resp.status_code < 400:
            return elapsed
    except Exception:
        pass

    try:
        start = time.monotonic()
        resp = requests.get(url, headers=HEADERS, timeout=timeout, stream=True)
        elapsed = time.monotonic() - start
        if resp.status_code < 400:
            resp.close()
            return elapsed
    except Exception:
        pass

    return None


def pick_fastest(urls):
    """
    Given candidate URLs, test them concurrently and return whichever
    responds fastest. Falls back to the first candidate if none respond
    (better to hand the player *something* than nothing).
    """
    if not urls:
        return None

    candidates = urls[:MAX_CANDIDATES_TO_TEST]

    if len(candidates) == 1:
        return candidates[0]

    results = {}
    with ThreadPoolExecutor(max_workers=min(MAX_TEST_WORKERS, len(candidates))) as executor:
        future_to_url = {executor.submit(test_url, u): u for u in candidates}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                elapsed = future.result()
            except Exception:
                elapsed = None
            if elapsed is not None:
                results[url] = elapsed
                print(f"    [SPEED] {elapsed:.2f}s  {url}")
            else:
                print(f"    [SPEED] failed        {url}")

    if not results:
        print("    [SPEED] none responded, keeping first candidate as best effort")
        return candidates[0]

    best_url = min(results, key=results.get)
    return best_url


# ---------------------------------------------------------------------------
# Playlist update
# ---------------------------------------------------------------------------

def read_playlist():
    if not PLAYLIST_FILE.exists():
        print("Playlist not found!")
        return []

    return PLAYLIST_FILE.read_text(
        encoding="utf-8"
    ).splitlines()


def update_sports_section(lines, all_channels, fancode_pool):

    output = []

    i = 0
    n = len(lines)

    while i < n:

        line = lines[i]

        if (
            line.startswith("#EXTINF")
            and 'group-title="Sports"' in line
        ):

            channel_name = line.rsplit(",", 1)[-1].strip()

            output.append(line)
            i += 1

            # Only treat the next line as an existing URL if it actually
            # looks like one. Many channels in the master playlist have
            # no URL line at all - don't swallow the next channel's
            # #EXTINF line as if it were a URL.
            old_url = None
            if i < n and lines[i].strip() and not lines[i].lstrip().startswith("#"):
                old_url = lines[i]

            fancode_info = parse_master_fancode(channel_name)

            if fancode_info:
                category, index = fancode_info
                pooled_urls = fancode_pool.get(category, [])

                if index - 1 < len(pooled_urls):
                    new_url = pooled_urls[index - 1]
                    print(f"[FANCODE FOUND] {channel_name} -> pool[{category}][{index - 1}]")
                else:
                    new_url = None
                    print(f"[FANCODE NOT FOUND] {channel_name} (only {len(pooled_urls)} live in pool)")
            else:
                matches, tier = get_all_matches(channel_name, all_channels)

                if matches:
                    print(f"[{len(matches)} MATCH(ES) - {tier}] {channel_name}")
                    new_url = pick_fastest(matches)
                else:
                    new_url = None
                    print(f"[NOT FOUND] {channel_name}")

            final_url = new_url if new_url else old_url

            if old_url is not None:
                # consume the line we peeked at
                i += 1

            if final_url:
                output.append(final_url)

        else:
            output.append(line)
            i += 1

    return output


def save_playlist(lines):

    PLAYLIST_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    print("Playlist saved successfully.")


def main():

    print("Starting Sports updater...")

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

    print("Update completed.")


if __name__ == "__main__":
    main()

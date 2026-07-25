#!/usr/bin/env python3
"""
SAKIRULs IPTV Sports Auto Updater

Rules:
- Your channel names are the master list.
- Search 7 sources for matching channels.
- Replace only URLs.
- Never delete missing channels.
- FanCode categories (Cricket, Motorsport, Golf, Tennis, ...) are auto-pooled
  and assigned in order, since the source only lists them by category name
  (e.g. "FanCode Cricket") without numbering.
- T Sports is handled specially (prefers the dedicated T-Sports source).
- If an exact name match isn't found, a "loose" match is tried that ignores
  generic filler words (sports/hd/channel/tv/the) so e.g. "TNT Sports 1"
  can match a source's "TNT 1".
"""

from pathlib import Path
import re
import requests


PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")
SPORTS_GROUP = "Sports"


SOURCE_URLS = [
    # IPTVFlixBD Sports
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

# Words that are always safe to ignore (packaging/quality descriptors,
# never part of a channel's actual identity).
MILD_GENERIC_WORDS = {"hd", "channel", "tv", "the"}

# Words that are usually filler but occasionally ARE the channel's identity
# (e.g. "T Sports" - stripping "Sports" there would leave just "T"). Only
# used as a second-pass fallback, and only trusted if enough of the name
# survives.
AGGRESSIVE_GENERIC_WORDS = MILD_GENERIC_WORDS | {"sports", "sport"}

# Minimum length (after stripping generic words) before we trust a loose
# match. Prevents very short leftovers (like "T" from "T Sports HD") from
# matching all sorts of unrelated channels.
MIN_LOOSE_LEN = 3


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


def normalize(name):
    """Strict normalization: lowercase, alphanumeric only."""
    return re.sub(
        r"[^a-z0-9]",
        "",
        name.lower()
    )


def loose_normalize(name, aggressive=False):
    """
    Loose normalization: drops generic filler words.
    aggressive=False strips only truly generic words (hd/tv/channel/the).
    aggressive=True additionally strips "sports"/"sport", which usually
    is filler (e.g. "TNT Sports 1" ~ "TNT 1") but occasionally is part of
    a channel's real identity (e.g. "T Sports").
    """
    n = name.lower()
    n = re.sub(r"[^a-z0-9\s]", " ", n)
    stopwords = AGGRESSIVE_GENERIC_WORDS if aggressive else MILD_GENERIC_WORDS
    words = [w for w in n.split() if w not in stopwords]
    return "".join(words)


def parse_m3u(content):
    """
    Convert M3U into:
    [
        {
            "name": channel name,
            "url": stream url
        }
    ]
    """

    channels = []

    lines = content.splitlines()
    current_name = None

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):

            if "," in line:
                current_name = line.split(",", 1)[1].strip()

        elif not line.startswith("#"):

            if current_name:
                channels.append(
                    {
                        "name": current_name,
                        "url": line
                    }
                )

                current_name = None

    return channels


# Matches a master-list FanCode entry like "FanCode Cricket 1" or
# "FanCode Golf" (no trailing number = implicitly slot 1).
FANCODE_MASTER_RE = re.compile(r"^FanCode\s+([A-Za-z]+)\s*(\d+)?$", re.IGNORECASE)


def parse_master_fancode(channel_name):
    """
    If channel_name is a numbered FanCode master entry
    (e.g. "FanCode Cricket 1", "FanCode Golf"), return (category, index).
    Otherwise return None.
    """
    m = FANCODE_MASTER_RE.match(channel_name.strip())
    if not m:
        return None

    category = m.group(1).lower()
    index = int(m.group(2)) if m.group(2) else 1
    return category, index


def extract_source_fancode_category(source_channel_name):
    """
    If a source channel is a FanCode channel (e.g. "FanCode Cricket",
    "FanCode Cricket 2", "Fancode Golf"), return its category
    (e.g. "cricket"). Otherwise return None.

    This is intentionally generic (no hardcoded category list) so that
    if the source adds a brand-new FanCode category tomorrow, it still
    gets picked up automatically.
    """
    n = source_channel_name.strip()

    if not re.match(r"^fan\s*code\b", n, re.IGNORECASE):
        return None

    rest = re.sub(r"^fan\s*code\s*", "", n, flags=re.IGNORECASE).strip()
    rest = re.sub(r"\s*\d+\s*$", "", rest).strip()

    if not rest:
        return None

    return rest.lower()


def build_fancode_pool(sources):
    """
    Build a dict: category -> ordered list of deduped URLs, gathered from
    every source. This lets us fill FanCode Cricket 1/2/3 etc even when the
    source itself has no numbering, and automatically grows if the source
    adds more channels for that category later.
    """
    pool = {}
    seen_urls = {}

    for source in sources:
        for channel in source["channels"]:
            category = extract_source_fancode_category(channel["name"])

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


def find_channel_url(channel_name, sources):
    """
    Search all sources for a matching channel name.
    Tries an exact (strict) match first, then falls back to a "loose"
    match that ignores generic filler words. Returns URL if found,
    otherwise None.
    """

    target = normalize(channel_name)
    mild_target = loose_normalize(channel_name, aggressive=False)
    aggressive_target = loose_normalize(channel_name, aggressive=True)

    ordered_sources = sources

    # FanCode priority
    if channel_name.startswith("FanCode"):
        ordered_sources = sorted(
            sources,
            key=lambda x: "fancode" not in x["source"].lower()
        )

    # T Sports priority
    if channel_name == "T Sports HD":
        ordered_sources = sorted(
            sources,
            key=lambda x: "t-sports" not in x["source"].lower()
        )

    # Phase 1: exact strict match
    for source in ordered_sources:
        for channel in source["channels"]:
            if normalize(channel["name"]) == target:
                print(
                    f"[FOUND] {channel_name} "
                    f"from {source['source']}"
                )
                return channel["url"]

    # Phase 2: mild loose match (ignores hd/tv/channel/the only)
    if len(mild_target) >= MIN_LOOSE_LEN:
        for source in ordered_sources:
            for channel in source["channels"]:
                if loose_normalize(channel["name"], aggressive=False) == mild_target:
                    print(
                        f"[FOUND-FUZZY] {channel_name} ~ {channel['name']} "
                        f"from {source['source']}"
                    )
                    return channel["url"]

    # Phase 3: aggressive loose match (also ignores sports/sport)
    if len(aggressive_target) >= MIN_LOOSE_LEN:
        for source in ordered_sources:
            for channel in source["channels"]:
                if loose_normalize(channel["name"], aggressive=True) == aggressive_target:
                    print(
                        f"[FOUND-FUZZY-AGGRESSIVE] {channel_name} ~ {channel['name']} "
                        f"from {source['source']}"
                    )
                    return channel["url"]

    print(f"[NOT FOUND] {channel_name}")

    return None


def load_sources():

    all_sources = []

    for url in SOURCE_URLS:

        data = download_playlist(url)

        if data:

            channels = parse_m3u(data)

            all_sources.append(
                {
                    "source": url,
                    "channels": channels
                }
            )

            print(
                f"Loaded {len(channels)} channels"
            )

    return all_sources


def read_playlist():
    if not PLAYLIST_FILE.exists():
        print("Playlist not found!")
        return []

    return PLAYLIST_FILE.read_text(
        encoding="utf-8"
    ).splitlines()


def update_sports_section(lines, sources, fancode_pool):

    output = []

    i = 0
    n = len(lines)

    while i < n:

        line = lines[i]

        if (
            line.startswith("#EXTINF")
            and 'group-title="Sports"' in line
        ):

            channel_name = line.split(",", 1)[1].strip()

            output.append(line)
            i += 1

            # Only treat the next line as an existing URL if it actually
            # looks like one (not another #EXTINF / comment line, and not
            # simply absent). Some channels in the master playlist have no
            # URL line at all yet - don't swallow the next channel's
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
                    print(f"[FANCODE NOT FOUND] {channel_name} (only {len(pooled_urls)} in pool)")
            else:
                new_url = find_channel_url(channel_name, sources)

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

    sources = load_sources()

    if not sources:
        print("No sources available. Stopping.")
        return

    playlist = read_playlist()

    if not playlist:
        print("Playlist is empty. Stopping.")
        return

    fancode_pool = build_fancode_pool(sources)

    updated_playlist = update_sports_section(
        playlist,
        sources,
        fancode_pool
    )

    save_playlist(updated_playlist)

    print("Update completed.")


if __name__ == "__main__":
    main()

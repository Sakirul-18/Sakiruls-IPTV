#!/usr/bin/env python3
"""
FanCode IPTV Auto Updater
Rules:
- Update ONLY FanCode channels.
- Replace ONLY the stream URL.
- Never delete channels.
- Never change #EXTINF metadata.
- If the source has fewer URLs than the playlist,
  leave the remaining channels unchanged.
"""
from pathlib import Path
import re
import requests

# ========= SETTINGS =========
PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")
SOURCE_URL = (
    "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/data.json"
)

# Maps the source's "event_category" value -> your playlist's channel name.
# Matching is normalized (case/whitespace-insensitive, and tolerant of
# "Motorsport" vs "Motorsports") so mirror-to-mirror spelling drift
# doesn't silently break a channel.
CATEGORY_TO_CHANNEL = {
    "Cricket": "Fancode Cricket",
    "Golf": "Fancode Golf",
    "Motorsports": "Fancode Motorsport",
    "Tennis": "Fancode Tennis",
}

TIMEOUT = 20
VERBOSE = True  # set False to quiet the per-line debug output


def _normalize(text):
    """Lowercase, strip, and drop a trailing 's' so 'Motorsport' and
    'Motorsports' are treated the same."""
    t = text.strip().lower()
    return t[:-1] if t.endswith("s") else t


# Build a normalized lookup once: normalized category -> channel name
_NORMALIZED_LOOKUP = {
    _normalize(category): channel_name
    for category, channel_name in CATEGORY_TO_CHANNEL.items()
}


def download_source():
    """Download the FanCode source JSON."""
    response = requests.get(SOURCE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def parse_source(data):
    """
    Read the FanCode source JSON and collect stream URLs by target
    channel name, keyed off each match's "event_category" field.
    Prefers "adfree_url"; falls back to "dai_url" if the ad-free
    link isn't posted yet for that match. Matches without either key
    (not currently LIVE) are skipped.
    """
    urls = {name: [] for name in CATEGORY_TO_CHANNEL.values()}
    unmapped_categories = set()

    for match in data.get("matches", []):
        category = match.get("event_category")
        if not category:
            continue

        channel_name = _NORMALIZED_LOOKUP.get(_normalize(category))
        if channel_name is None:
            unmapped_categories.add(category)
            continue

        stream_url = match.get("adfree_url") or match.get("dai_url")
        if stream_url:
            urls[channel_name].append(stream_url)

    if VERBOSE and unmapped_categories:
        others = ", ".join(sorted(unmapped_categories))
        print(f"  (source also has these categories, not tracked: {others})")

    return urls


def update_playlist(source_urls):
    """
    Update the FanCode channel entries in the playlist.

    Handles both:
      - #EXTINF followed by an existing http URL line -> replace it.
      - #EXTINF followed by a blank line OR directly by the next
        #EXTINF (no separator at all) -> insert a URL if one is
        available; otherwise leave it as-is.
    """
    lines = PLAYLIST_FILE.read_text(encoding="utf-8").splitlines()
    counters = {name: 0 for name in CATEGORY_TO_CHANNEL.values()}
    output = []
    i = 0
    while i < len(lines):
        line = lines[i]
        output.append(line)

        if line.startswith("#EXTINF"):
            lower_line = line.lower()
            current = None
            for channel_name in CATEGORY_TO_CHANNEL.values():
                if channel_name.lower() in lower_line:
                    current = channel_name
                    break

            if current:
                index = counters[current]
                has_next = i + 1 < len(lines)
                next_is_url = has_next and lines[i + 1].strip().startswith("http")

                if next_is_url:
                    if index < len(source_urls[current]):
                        output.append(source_urls[current][index])
                        if VERBOSE:
                            print(f"  [line {i+1}] {current} slot {index}: replaced URL")
                    else:
                        output.append(lines[i + 1])
                        if VERBOSE:
                            print(f"  [line {i+1}] {current} slot {index}: no new URL, kept existing")
                    i += 1
                else:
                    if index < len(source_urls[current]):
                        output.append(source_urls[current][index])
                        if VERBOSE:
                            print(f"  [line {i+1}] {current} slot {index}: filled empty slot")
                        if has_next and lines[i + 1].strip() == "":
                            i += 1
                    elif VERBOSE:
                        print(f"  [line {i+1}] {current} slot {index}: no URL available, left empty")

                counters[current] += 1

        i += 1

    PLAYLIST_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")

    if VERBOSE:
        print("  Playlist slot counts found:", counters)


def main():
    print("Downloading FanCode source...")
    source_data = download_source()
    source_urls = parse_source(source_data)
    for name, urls in source_urls.items():
        print(f"  {name}: {len(urls)} URL(s) found")
    print("Updating playlist...")
    update_playlist(source_urls)
    print("Done!")


if __name__ == "__main__":
    main()

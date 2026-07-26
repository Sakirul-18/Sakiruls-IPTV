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
import json
import requests

# ========= SETTINGS =========
PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")
SOURCE_URL = (
    "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/data.json"
)

# Maps the source's "event_category" value -> a list of your playlist's channel names.
# This handles multiple playlist slots corresponding to the same source category.
CATEGORY_TO_CHANNELS = {
    "Cricket": ["Fancode Cricket", "FanCode Cricket 2", "FanCode Cricket 3"],  # Adjust names to match your .m3u file exactly
    "Golf": ["Fancode Golf"],
    "Motorsports": ["Fancode Motorsport", "Fancode Motorsport 2"],
    "Tennis": ["Fancode Tennis", "Fancode Tennis 2"],
    "Football": ["Fancode Football"],  # Added if you expanded to football or other sports
    "Basketball": ["Fancode Basketball"],
    "Badminton": ["Fancode Badminton"],
    "Volleyball": ["Fancode Volleyball"]
}

# Flattened list of all target channel names for iteration/counters
ALL_TARGET_CHANNELS = [ch for channels in CATEGORY_TO_CHANNELS.values() for ch in channels]

TIMEOUT = 20


def download_source():
    """Download the FanCode source JSON."""
    response = requests.get(SOURCE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()  # parsed dict, not raw text lines


def parse_source(data):
    """
    Read the FanCode source JSON and collect stream URLs by source category,
    then map them out to all corresponding target channels.
    """
    # Group raw URLs by source category first
    category_urls = {category: [] for category in CATEGORY_TO_CHANNELS.keys()}

    for match in data.get("matches", []):
        category = match.get("event_category")
        if category not in category_urls:
            continue  # not a category we track

        stream_url = match.get("adfree_url") or match.get("dai_url")
        if stream_url:
            category_urls[category].append(stream_url)

    # Distribute the URLs to each target channel mapped to that category
    urls = {name: [] for name in ALL_TARGET_CHANNELS}
    for category, target_channels in CATEGORY_TO_CHANNELS.items():
        avail_urls = category_urls.get(category, [])
        for channel_name in target_channels:
            # Assign a copy of the category's streams to each mapped channel slot
            urls[channel_name] = list(avail_urls)

    return urls


def update_playlist(source_urls):
    """
    Update the FanCode channel entries in the playlist.
    """
    lines = PLAYLIST_FILE.read_text(encoding="utf-8").splitlines()
    counters = {name: 0 for name in ALL_TARGET_CHANNELS}
    output = []
    i = 0
    while i < len(lines):
        line = lines[i]
        output.append(line)

        if line.startswith("#EXTINF"):
            lower_line = line.lower()
            current = None
            for channel_name in ALL_TARGET_CHANNELS:
                if channel_name.lower() in lower_line:
                    current = channel_name
                    break

            if current:
                index = counters[current]
                has_next = i + 1 < len(lines)
                next_is_url = has_next and lines[i + 1].strip().startswith("http")

                if next_is_url:
                    # Existing URL line -- replace it if we have a new one
                    if index < len(source_urls[current]):
                        output.append(source_urls[current][index])
                    else:
                        output.append(lines[i + 1])
                    i += 1  # consumed the original URL line
                else:
                    # Blank placeholder -- insert a URL if one is available
                    if index < len(source_urls[current]):
                        output.append(source_urls[current][index])
                        if has_next and lines[i + 1].strip() == "":
                            i += 1  # consume the blank placeholder line

                counters[current] += 1

        i += 1

    PLAYLIST_FILE.write_text(
        "\n".join(output) + "\n",
        encoding="utf-8"
    )


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

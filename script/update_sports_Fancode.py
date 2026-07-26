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

# Maps the source's "event_category" value -> your playlist's channel name.
# NOTE: verify "Motorsports" matches exactly what the source uses -- some
# FanCode mirrors spell it "Motorsport" (singular). Check data.json if
# the Motorsport channel stops updating.
CATEGORY_TO_CHANNEL = {
    "Cricket": "FanCode Cricket",
    "Golf": "FanCode Golf",
    "Motorsports": "FanCode Motorsport",
    "Tennis": "FanCode Tennis",
}

TIMEOUT = 20


def download_source():
    """Download the FanCode source JSON."""
    response = requests.get(SOURCE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()  # parsed dict, not raw text lines


def parse_source(data):
    """
    Read the FanCode source JSON and collect stream URLs by target
    channel name, keyed off each match's "event_category" field.
    Prefers "adfree_url"; falls back to "dai_url" if the ad-free
    link isn't posted yet for that match.
    """
    urls = {name: [] for name in CATEGORY_TO_CHANNEL.values()}

    for match in data.get("matches", []):
        category = match.get("event_category")
        channel_name = CATEGORY_TO_CHANNEL.get(category)
        if channel_name is None:
            continue  # not a category we track (e.g. Football)

        stream_url = match.get("adfree_url") or match.get("dai_url")
        if stream_url:
            urls[channel_name].append(stream_url)

    return urls


def update_playlist(source_urls):
    """
    Update only the URL lines for the FanCode channels.
    """
    lines = PLAYLIST_FILE.read_text(encoding="utf-8").splitlines()
    counters = {name: 0 for name in CATEGORY_TO_CHANNEL.values()}

    current = None
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF"):
            current = None
            for channel_name in CATEGORY_TO_CHANNEL.values():
                if channel_name in line:
                    current = channel_name
                    break
        elif current and line.startswith("http"):
            index = counters[current]
            if index < len(source_urls[current]):
                lines[i] = source_urls[current][index]
            counters[current] += 1
            current = None

    PLAYLIST_FILE.write_text(
        "\n".join(lines) + "\n",
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

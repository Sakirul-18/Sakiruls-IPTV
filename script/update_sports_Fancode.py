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
# Matching is done case-insensitively, so "Fancode Cricket" and
# "FanCode Cricket" are treated the same.
# NOTE: verify "Motorsports" matches exactly what the source uses -- some
# FanCode mirrors spell it "Motorsport" (singular). Check data.json if
# the Motorsport channel stops updating.
CATEGORY_TO_CHANNEL = {
    "Cricket": "Fancode Cricket",
    "Golf": "Fancode Golf",
    "Motorsports": "Fancode Motorsport",
    "Tennis": "Fancode Tennis",
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
    Update the FanCode channel entries in the playlist.

    Some FanCode slots in the playlist are placeholders with no URL yet
    (an #EXTINF line followed by a blank line instead of an http line).
    This handles both cases:
      - If a URL line already exists after #EXTINF, replace it.
      - If it's a blank placeholder, insert a URL line if one is
        available; otherwise leave the blank line untouched.
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
                    # else: leave the blank placeholder exactly as it is

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

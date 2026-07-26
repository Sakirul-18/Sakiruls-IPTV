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

TARGET_CHANNELS = [
    "FanCode Cricket",
    "FanCode Cricket",
    "FanCode Cricket",
    "FanCode Golf",
    "FanCode Motorsport",
    "FanCode Motorsport",
    "FanCode Tennis",
]

TIMEOUT = 20

def download_source():
    """Download the FanCode source playlist."""
    response = requests.get(SOURCE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text.splitlines()

def parse_source(lines):
    """
    Read the FanCode source playlist and collect URLs by category.
    """
    urls = {
        "FanCode Cricket": [],
        "FanCode Golf": [],
        "FanCode Motorsport": [],
        "FanCode Tennis": [],
    }

    current = None

    for line in lines:
        line = line.strip()

        if line.startswith("#EXTINF"):
            lower = line.lower()

            if 'group-title="fancode-cricket"' in lower:
                current = "FanCode Cricket"
            elif 'group-title="fancode-golf"' in lower:
                current = "FanCode Golf"
            elif 'group-title="fancode-motorsport"' in lower:
                current = "FanCode Motorsport"
            elif 'group-title="fancode-tennis"' in lower:
                current = "FanCode Tennis"
            else:
                current = None

        elif current and line.startswith("http"):
            urls[current].append(line)
            current = None

    return urls

def update_playlist(source_urls):
    """
    Update only the URL lines for the FanCode channels.
    """
    lines = PLAYLIST_FILE.read_text(encoding="utf-8").splitlines()

    counters = {
        "FanCode Cricket": 0,
        "FanCode Golf": 0,
        "FanCode Motorsport": 0,
        "FanCode Tennis": 0,
    }

    current = None

    for i, line in enumerate(lines):
        if line.startswith("#EXTINF"):
            current = None

            if "FanCode Cricket" in line:
                current = "FanCode Cricket"
            elif "FanCode Golf" in line:
                current = "FanCode Golf"
            elif "FanCode Motorsport" in line:
                current = "FanCode Motorsport"
            elif "FanCode Tennis" in line:
                current = "FanCode Tennis"

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

    source_lines = download_source()
    source_urls = parse_source(source_lines)

    print("Updating playlist...")
    update_playlist(source_urls)

    print("Done!")


if __name__ == "__main__":
    main()

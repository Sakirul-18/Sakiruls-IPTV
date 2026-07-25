#!/usr/bin/env python3
"""
update_sports.py

Automatically updates the Sports section of:
SAKIRULs IPTV.m3u

Sources:
- IPTVFlixBD
- BDxTV
- CricHD
- IPTV-Scraper-Zilla
- Toffee
- RynoCast

Author: ChatGPT
"""

import os
import re
import sys
from pathlib import Path

import requests

# -------------------------------------------------------
# Configuration
# -------------------------------------------------------

PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")

SOURCE_URLS = [
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s2.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/world-1.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/BDxTV/main/playlist.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/CricHd-playlists-Auto-Update-permanent/main/crichd.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/Toffee-playlist/main/ott_navigator.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/RynoCast-IPTV-M3u-Playlist/main/all.m3u",
]

CHANNELS = [
    "616 Sports 4K",
    "beIN Sports 1",
    "beIN Sports 2",
    "beIN Sports 3",
    "beIN Sports 4",
    "beIN Sports 5",
    "beIN Sports 6",
    "BTV",
    "DAS ERSTE HD",
    "Eurosport",
    "F1 TV",
    "FIFA World Cup",
    "FOX Sports 1",
    "MotoGP",
    "Motorsport TV",
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
    "Sports18",
    "Star Sports 1",
    "Star Sports 1 Hindi",
    "Star Sports Select 1",
    "Star Sports Select 2",
    "SuperSport Cricket",
    "SuperSport Football",
    "SuperSport Premier League",
    "T Sports",
    "Tennis Channel",
    "TIPIK HD",
    "TNT Sports 1",
    "TNT Sports 2",
    "TNT Sports 3",
    "TSN 1",
    "TSN 3",
    "TVP Sports",
    "USA Network",
    "VRT 1 HD",
    "Willow Cricket",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


# -------------------------------------------------------
# Helper Functions
# -------------------------------------------------------

def download(url):
    """Download a playlist."""

    try:
        response = requests.get(url, headers=HEADERS, timeout=20)

        if response.status_code == 200:
            print(f"[OK] {url}")
            return response.text

        print(f"[FAIL {response.status_code}] {url}")

    except Exception as e:
        print(f"[ERROR] {url}")
        print(e)

    return ""
  # --------------------------------------------------
# Parse M3U
# --------------------------------------------------

def parse_playlist(content):
    """Return [(name, url)] from an M3U playlist."""
    channels = []

    current_name = None

    for line in content.splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):
            if "," in line:
                current_name = line.split(",", 1)[1].strip()
            else:
                current_name = None

        elif not line.startswith("#"):
            if current_name:
                channels.append((current_name, line))
                current_name = None

    return channels


# --------------------------------------------------
# Search Channel
# --------------------------------------------------

def find_channel(channel_name, playlists):
    """
    Find a channel using exact match first,
    then partial match.
    """

    target = channel_name.lower()

    # Exact match
    for playlist in playlists:
        for name, url in playlist:
            if name.lower() == target:
                return url

    # Partial match
    for playlist in playlists:
        for name, url in playlist:
            if target in name.lower():
                return url

    # Reverse partial
    for playlist in playlists:
        for name, url in playlist:
            if name.lower() in target:
                return url

    return None


# --------------------------------------------------
# Download All Source Playlists
# --------------------------------------------------

all_playlists = []

for source in SOURCES:

    print(f"Downloading: {source}")

    data = download(source)

    if data:

        parsed = parse_playlist(data)

        print(f"Found {len(parsed)} channels")

        all_playlists.append(parsed)

    else:

        print("Skipped.")
      # --------------------------------------------------
# Build Updated Playlist
# --------------------------------------------------

output = "#EXTM3U\n\n"

found = 0
missing = 0

for channel in TARGET_CHANNELS:

    url = find_channel(channel, all_playlists)

    if url:

        found += 1

        output += (
            f'#EXTINF:-1 group-title="Sports", {channel}\n'
            f'{url}\n\n'
        )

        print(f"[FOUND] {channel}")

    else:

        missing += 1

        output += (
            f'#EXTINF:-1 group-title="Sports", {channel}\n\n'
        )

        print(f"[MISSING] {channel}")


# --------------------------------------------------
# Save Playlist
# --------------------------------------------------

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(output)

print()
print("=" * 40)
print("Finished!")
print(f"Found   : {found}")
print(f"Missing : {missing}")
print(f"Saved   : {OUTPUT_FILE}")
print("=" * 40)

#!/usr/bin/env python3
"""
FanCode IPTV Auto Updater
Mapping: JSON event_category -> M3U Channel Name
"""
from pathlib import Path
import json
import requests

# ========= CONFIGURATION =========
PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")
SOURCE_URL = (
    "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/data.json"
)

# 1. Maps JSON "event_category" -> Your Playlist Channel Name
CATEGORY_TO_CHANNEL = {
    "Cricket": "Fancode Cricket",
    "Golf": "Fancode Golf",
    "Motorsports": "Fancode Motorsport",
    "Motorsport": "Fancode Motorsport",  # Handles singular spelling in JSON source
    "Tennis": "Fancode Tennis",
}

TIMEOUT = 20


def download_source():
    """Download the FanCode source JSON."""
    try:
        r = requests.get(SOURCE_URL, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Failed to download JSON source: {e}")
        return None


def parse_source(data):
    """
    Scans the JSON source for 'event_category', maps it to your channel name,
    and collects all active match stream URLs.
    """
    target_channels = set(CATEGORY_TO_CHANNEL.values())
    urls = {name: [] for name in target_channels}

    if not data or "matches" not in data:
        return urls

    for match in data.get("matches", []):
        cat = match.get("event_category")
        channel_name = CATEGORY_TO_CHANNEL.get(cat)

        if channel_name:
            stream_url = match.get("adfree_url") or match.get("dai_url")
            if stream_url:
                urls[channel_name].append(stream_url)

    return urls


def update_playlist(source_urls):
    """
    Scans M3U for channel names matching the categories,
    and updates/replaces stream URLs accordingly.
    """
    if not PLAYLIST_FILE.exists():
        print(f"Error: Master playlist '{PLAYLIST_FILE}' not found.")
        return

    lines = PLAYLIST_FILE.read_text(encoding="utf-8").splitlines()
    target_channels = set(CATEGORY_TO_CHANNEL.values())
    counters = {name: 0 for name in target_channels}
    output = []
    i = 0

    while i < len(lines):
        line = lines[i]
        output.append(line)

        if line.startswith("#EXTINF"):
            lower_line = line.lower()

            # Find matching target channel name in this #EXTINF line
            matched_channel = None
            for ch_name in target_channels:
                if ch_name.lower() in lower_line:
                    matched_channel = ch_name
                    break

            if matched_channel:
                idx = counters[matched_channel]
                has_next = (i + 1) < len(lines)
                next_is_url = has_next and lines[i + 1].strip().startswith("http")

                # If JSON source provided a stream for this slot
                if idx < len(source_urls[matched_channel]):
                    output.append(source_urls[matched_channel][idx])
                    if next_is_url:
                        i += 1  # Replace old URL line
                    elif has_next and lines[i + 1].strip() == "":
                        i += 1  # Consume blank placeholder line
                else:
                    # No new URL in JSON; keep existing URL if present
                    if next_is_url:
                        output.append(lines[i + 1])
                        i += 1

                counters[matched_channel] += 1

        i += 1

    # Save changes back to file
    PLAYLIST_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")


def main():
    print("Fetching FanCode JSON data...")
    data = download_source()
    if not data:
        print("Skipping update due to fetch error.")
        return

    source_urls = parse_source(data)

    print("\n--- JSON Scraped Streams ---")
    for name, urls in source_urls.items():
        print(f"  {name}: {len(urls)} stream URL(s) found")

    print("\nUpdating M3U playlist...")
    update_playlist(source_urls)
    print("Done! FanCode update complete.")


if __name__ == "__main__":
    main()

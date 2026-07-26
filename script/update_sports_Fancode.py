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
SCRIPT_DIR = Path(__file__).resolve().parent
PLAYLIST_FILE = SCRIPT_DIR.parent / "SAKIRULs IPTV.m3u"
SOURCE_URL = (
    "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/data.json"
)

# Maps your playlist channel name patterns (case-insensitive) to the source's "event_category"
# Because you have multiple channels with the exact same name, the script 
# consumes URLs from the category list sequentially.
CHANNEL_TO_CATEGORY = {
    "fancode cricket": "Cricket",
    "fancode golf": "Golf",
    "fancode motorsport": "Motorsports",
    "fancode tennis": "Tennis",
}

TIMEOUT = 20


def download_source():
    """Download the FanCode source JSON."""
    response = requests.get(SOURCE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def parse_source(data):
    """
    Read the FanCode source JSON and collect stream URLs grouped by source category.
    """
    category_urls = {}

    for match in data.get("matches", []):
        category = match.get("event_category")
        if not category:
            continue
            
        stream_url = match.get("adfree_url") or match.get("dai_url")
        if stream_url:
            if category not in category_urls:
                category_urls[category] = []
            category_urls[category].append(stream_url)

    return category_urls


def update_playlist(source_categories):
    """
    Update the FanCode channel entries in the playlist sequentially.
    """
    lines = PLAYLIST_FILE.read_text(encoding="utf-8").splitlines()
    
    # Track how many URLs we have consumed per source category key
    category_counters = {cat: 0 for cat in CHANNEL_TO_CATEGORY.values()}
    
    output = []
    i = 0
    while i < len(lines):
        line = lines[i]
        output.append(line)

        if line.startswith("#EXTINF"):
            lower_line = line.lower()
            matched_category = None
            
            # Find which category this #EXTINF line belongs to
            for ch_pattern, source_cat in CHANNEL_TO_CATEGORY.items():
                if ch_pattern in lower_line:
                    matched_category = source_cat
                    break

            if matched_category:
                urls = source_categories.get(matched_category, [])
                index = category_counters[matched_category]
                
                has_next = i + 1 < len(lines)
                next_is_url = has_next and lines[i + 1].strip().startswith("http")

                if next_is_url:
                    # Existing URL line -- replace it if a new URL is available
                    if index < len(urls):
                        output.append(urls[index])
                    else:
                        output.append(lines[i + 1]) # Keep old URL if no new one exists
                    i += 1  # consumed the original URL line
                else:
                    # Blank placeholder -- insert a URL if one is available
                    if index < len(urls):
                        output.append(urls[index])
                        if has_next and lines[i + 1].strip() == "":
                            i += 1  # consume the blank placeholder line

                category_counters[matched_category] += 1

        i += 1

    PLAYLIST_FILE.write_text(
        "\n".join(output) + "\n",
        encoding="utf-8"
    )


def main():
    print("Downloading FanCode source...")
    source_data = download_source()
    source_categories = parse_source(source_data)
    
    for cat, urls in source_categories.items():
        print(f"  Category '{cat}': {len(urls)} URL(s) found")
        
    print("Updating playlist...")
    update_playlist(source_categories)
    print("Done!")


if __name__ == "__main__":
    main()

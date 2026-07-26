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
import requests

# ========= SETTINGS =========
SCRIPT_DIR = Path(__file__).resolve().parent
PLAYLIST_FILE = SCRIPT_DIR.parent / "SAKIRULs IPTV.m3u"
SOURCE_URL = "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/data.json"

# Match by simple channel-name keywords in your playlist
CHANNEL_TO_CATEGORY = {
    "cricket": "Cricket",
    "golf": "Golf",
    "motorsport": "Motorsports",
    "tennis": "Tennis",
}

TIMEOUT = 20


def download_source():
    """Download the FanCode source JSON."""
    print(f"Source URL: {SOURCE_URL}")
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
            category_urls.setdefault(category, []).append(stream_url)

    return category_urls


def count_playlist_slots(lines):
    """
    Count how many matching FanCode #EXTINF entries exist in the playlist
    for each configured category.
    """
    counts = {cat: 0 for cat in CHANNEL_TO_CATEGORY.values()}

    for line in lines:
        if not line.startswith("#EXTINF"):
            continue

        lower_line = line.lower()
        for pattern, category in CHANNEL_TO_CATEGORY.items():
            if pattern in lower_line:
                counts[category] += 1
                break

    return counts


def update_playlist(source_categories):
    """
    Update the FanCode channel entries in the playlist sequentially.
    """
    playlist_path = PLAYLIST_FILE.resolve()
    print(f"Playlist path: {playlist_path}")

    if not PLAYLIST_FILE.exists():
        raise FileNotFoundError(f"Playlist file not found: {playlist_path}")

    lines = PLAYLIST_FILE.read_text(encoding="utf-8").splitlines()

    playlist_counts = count_playlist_slots(lines)
    print("\nPlaylist FanCode slots found:")
    for category in CHANNEL_TO_CATEGORY.values():
        print(f"  {category}: {playlist_counts.get(category, 0)} entry(ies)")

    print("\nSource URLs found:")
    for category in CHANNEL_TO_CATEGORY.values():
        print(f"  {category}: {len(source_categories.get(category, []))} URL(s)")

    category_counters = {cat: 0 for cat in CHANNEL_TO_CATEGORY.values()}
    output = []
    i = 0
    replacements = 0
    kept_existing = 0
    inserted_blank = 0

    while i < len(lines):
        line = lines[i]
        output.append(line)

        if line.startswith("#EXTINF"):
            lower_line = line.lower()
            matched_category = None

            for pattern, source_cat in CHANNEL_TO_CATEGORY.items():
                if pattern in lower_line:
                    matched_category = source_cat
                    break

            if matched_category:
                urls = source_categories.get(matched_category, [])
                index = category_counters[matched_category]

                has_next = i + 1 < len(lines)
                next_line = lines[i + 1] if has_next else ""
                next_is_url = has_next and next_line.strip().startswith("http")

                if next_is_url:
                    if index < len(urls):
                        old_url = next_line
                        new_url = urls[index]
                        output.append(new_url)
                        replacements += 1
                        print(
                            f"Replaced {matched_category} #{index + 1}: "
                            f"{old_url} -> {new_url}"
                        )
                    else:
                        output.append(next_line)
                        kept_existing += 1
                        print(
                            f"Kept existing {matched_category} #{index + 1}: "
                            f"no new URL available"
                        )
                    i += 1
                else:
                    if index < len(urls):
                        new_url = urls[index]
                        output.append(new_url)
                        inserted_blank += 1
                        print(
                            f"Inserted {matched_category} #{index + 1}: "
                            f"{new_url}"
                        )
                        if has_next and next_line.strip() == "":
                            i += 1
                    else:
                        print(
                            f"Left blank {matched_category} #{index + 1}: "
                            f"no new URL available"
                        )

                category_counters[matched_category] += 1

        i += 1

    PLAYLIST_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")

    print("\nSaved playlist to:")
    print(f"  {playlist_path}")
    print("\nSummary:")
    print(f"  Replacements made: {replacements}")
    print(f"  Inserted into blank slots: {inserted_blank}")
    print(f"  Existing URLs kept: {kept_existing}")


def main():
    print("Downloading FanCode source...")
    source_data = download_source()
    source_categories = parse_source(source_data)

    print("\nUpdating playlist...")
    update_playlist(source_categories)
    print("Done!")


if __name__ == "__main__":
    main()

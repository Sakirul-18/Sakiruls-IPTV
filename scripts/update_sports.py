#!/usr/bin/env python3
"""
update_sports.py

Automatically updates the Sports section of:
SAKIRULs IPTV.m3u

Only the entries under group-title="Sports" are refreshed;
every other channel/group already in the playlist is left
untouched. If the playlist doesn't exist yet, it is created
containing just the Sports section.

Sources:
- IPTVFlixBD
- BDxTV
- CricHD
- IPTV-Scraper-Zilla
- Toffee
- RynoCast
"""

import re
from pathlib import Path

import requests

# -------------------------------------------------------
# Configuration
# -------------------------------------------------------

PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")
SPORTS_GROUP_TITLE = "Sports"

SOURCE_URLS = [
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s2.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/world-1.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/BDxTV/main/playlist.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/CricHd-playlists-Auto-Update-permanent/main/ALL.m3u",
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
# Parse a source M3U into (name, url) pairs
# --------------------------------------------------

def parse_playlist(content):
    """Return [(name, url)] from a source M3U playlist."""
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
# Parse the *target* playlist, preserving every block
# (needed so we can leave non-Sports groups untouched)
# --------------------------------------------------

def parse_target_playlist(content):
    """
    Split an existing playlist into:
      - header: everything before the first #EXTINF (e.g. #EXTM3U)
      - blocks: list of dicts {group, text} where `text` is the
        raw, untouched text of that entry (EXTINF line + any
        extra #-lines + the stream URL)
    """
    lines = content.splitlines()

    header_lines = []
    blocks = []

    current_block_lines = []
    seen_first_entry = False

    def flush_block():
        if current_block_lines:
            text = "\n".join(current_block_lines)
            group_match = re.search(r'group-title="([^"]*)"', current_block_lines[0])
            group = group_match.group(1) if group_match else ""
            blocks.append({"group": group, "text": text})

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("#EXTINF"):
            flush_block()
            current_block_lines = [line]
            seen_first_entry = True

        elif not seen_first_entry:
            if stripped:
                header_lines.append(line)

        else:
            if not stripped:
                # blank separator line between blocks -> ignore
                continue

            current_block_lines.append(line)
            if not stripped.startswith("#"):
                # this line is the stream URL -> block is complete
                flush_block()
                current_block_lines = []

    flush_block()

    header = "\n".join(header_lines) if header_lines else "#EXTM3U"

    return header, blocks


# --------------------------------------------------
# Search Channel
# --------------------------------------------------

def normalize(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())


def find_channel(channel_name, playlists):
    """
    Find a channel using exact match first, then a normalized
    (case/space/punctuation-insensitive) substring match in
    either direction.
    """
    target = normalize(channel_name)

    # Exact
    for playlist in playlists:
        for name, url in playlist:
            if normalize(name) == target:
                return url

    # Contains
    for playlist in playlists:
        for name, url in playlist:
            source = normalize(name)

            if not source:
                # Names that normalize to "" (e.g. non a-z0-9 names)
                # would otherwise match every target, since "" is a
                # substring of everything in Python.
                continue

            if target in source:
                return url

            if source in target:
                return url

    return None


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    # ---- Download all source playlists ----
    all_playlists = []

    for source in SOURCE_URLS:
        print(f"Downloading: {source}")

        data = download(source)

        if data:
            parsed = parse_playlist(data)
            print(f"Found {len(parsed)} channels")
            all_playlists.append(parsed)
        else:
            print("Skipped.")

    # ---- Build the new Sports blocks ----
    sports_blocks = []
    found = 0
    missing = 0

    for channel in CHANNELS:
        url = find_channel(channel, all_playlists)

        if url:
            found += 1
            text = f'#EXTINF:-1 group-title="Sports", {channel}\n{url}'
            sports_blocks.append(text)
            print(f"[FOUND] {channel}")
        else:
            missing += 1
            print(f"[MISSING] {channel}")
            continue

    new_sports_text = "\n\n".join(sports_blocks)

    # ---- Merge into the existing playlist, if any ----
    if PLAYLIST_FILE.exists():
        existing_content = PLAYLIST_FILE.read_text(encoding="utf-8")
    else:
        existing_content = "#EXTM3U"

    header, blocks = parse_target_playlist(existing_content)

    non_sports_blocks = [
        b["text"] for b in blocks
        if b["group"].strip().lower() != SPORTS_GROUP_TITLE.lower()
    ]

    # Remember where the old Sports section was so the new one
    # goes back in roughly the same place instead of always at
    # the bottom of the file.
    sports_insert_index = next(
        (i for i, b in enumerate(blocks)
         if b["group"].strip().lower() == SPORTS_GROUP_TITLE.lower()),
        None,
    )

    if not sports_blocks:
        # Nothing was found at all -> leave non-sports groups as-is
        # rather than inserting an empty Sports block.
        final_blocks = non_sports_blocks
    elif sports_insert_index is None:
        # No previous Sports section -> append at the end
        final_blocks = non_sports_blocks + [new_sports_text]
    else:
        # Count how many non-sports blocks came before the old
        # Sports section, and reinsert at that same position.
        insert_at = sum(
            1 for b in blocks[:sports_insert_index]
            if b["group"].strip().lower() != SPORTS_GROUP_TITLE.lower()
        )
        final_blocks = (
            non_sports_blocks[:insert_at]
            + [new_sports_text]
            + non_sports_blocks[insert_at:]
        )

    output = header + "\n\n" + "\n\n".join(final_blocks) + "\n"

    # ---- Save Playlist ----
    PLAYLIST_FILE.write_text(output, encoding="utf-8")

    print()
    print("=" * 40)
    print("Finished!")
    print(f"Found   : {found}")
    print(f"Missing : {missing}")
    print(f"Saved   : {PLAYLIST_FILE}")
    print("=" * 40)


if __name__ == "__main__":
    main()

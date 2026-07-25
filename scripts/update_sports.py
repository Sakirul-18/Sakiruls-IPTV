#!/usr/bin/env python3
"""
SAKIRULs IPTV Sports Auto Updater

Rules:
- Your channel names are the master list.
- Search 7 sources for matching channels.
- Replace only URLs.
- Never delete missing channels.
- FanCode and T Sports are handled specially.
"""

from pathlib import Path
import re
import requests


PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")
SPORTS_GROUP = "Sports"


SOURCE_URLS = [
    # IPTVFlixBD Sports
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s2.m3u",

    # IPTVFlixBD World
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/world-1.m3u",

    # Toffee
    "https://raw.githubusercontent.com/abusaeeidx/Toffee-playlist/main/ott_navigator.m3u",

    # FanCode
    "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/playlist.m3u",

    # KB TV
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/refs/heads/main/Github%20Auto%20Update%20Channel.m3u",

    # T Sports
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/combine_playlist.m3u",

    # CricHD
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/main/CricHD.m3u",
]


HEADERS = {
    "User-Agent": "Mozilla/5.0"
}
CHANNELS = [
    "616 Sports 4K",

    "beIN SPORTS 1",
    "beIN SPORTS 2",
    "beIN SPORTS 3",
    "beIN SPORTS 4",
    "beIN SPORTS 5",
    "beIN SPORTS 6",

    "BTV World",
    "Das Erste HD",
    "Eurosport 1",
    "F1 TV Pro",

    "FanCode Cricket 1",
    "FanCode Cricket 2",
    "FanCode Cricket 3",
    "FanCode Tennis",
    "FanCode Motorsport 1",
    "FanCode Golf",
    "FanCode Motorsport 2",

    "FIFA Plus Channel",
    "FOX Sports 1 USA",

    "MotoGP VideoPass",
    "Motorsport.tv",

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

    "Sports18 1 HD",

    "Star Sports 1",
    "Star Sports 1 Hindi",
    "Star Sports Select 1",
    "Star Sports Select 2",

    "SuperSport Cricket",
    "SuperSport Football",
    "SuperSport Premier League",

    "T Sports HD",
    "Tennis Channel",
    "Tipik HD",

    "TNT Sports 1",
    "TNT Sports 2",
    "TNT Sports 3",

    "TSN 1",
    "TSN 3",

    "TVP Sport HD",
    "USA Network",
    "VRT 1 HD",

    "Willow Cricket HD",
]


def download_playlist(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        if response.status_code == 200:
            print(f"[OK] Downloaded: {url}")
            return response.text

        print(f"[FAILED] {url} ({response.status_code})")

    except Exception as e:
        print(f"[ERROR] {url}")
        print(e)

    return ""


def normalize(name):
    return re.sub(
        r"[^a-z0-9]",
        "",
        name.lower()
    )


def parse_m3u(content):
    """
    Convert M3U into:
    [
        {
            "name": channel name,
            "url": stream url
        }
    ]
    """

    channels = []

    lines = content.splitlines()
    current_name = None

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):

            if "," in line:
                current_name = line.split(",", 1)[1].strip()

        elif not line.startswith("#"):

            if current_name:
                channels.append(
                    {
                        "name": current_name,
                        "url": line
                    }
                )

                current_name = None

    return channels


def find_channel_url(channel_name, sources):
    """
    Search all sources for an identical channel name.
    Returns URL if found, otherwise None.
    """

    target = normalize(channel_name)

    # FanCode priority
    if channel_name.startswith("FanCode"):
        sources = sorted(
            sources,
            key=lambda x: "Fancode" not in x["source"]
        )

    # T Sports priority
    if channel_name == "T Sports HD":
        sources = sorted(
            sources,
            key=lambda x: "T-Sports" not in x["source"]
        )

    for source in sources:

        for channel in source["channels"]:

            if normalize(channel["name"]) == target:
                print(
                    f"[FOUND] {channel_name} "
                    f"from {source['source']}"
                )

                return channel["url"]

    print(f"[NOT FOUND] {channel_name}")

    return None


def load_sources():

    all_sources = []

    for url in SOURCE_URLS:

        data = download_playlist(url)

        if data:

            channels = parse_m3u(data)

            all_sources.append(
                {
                    "source": url,
                    "channels": channels
                }
            )

            print(
                f"Loaded {len(channels)} channels"
            )

    return all_sources


def read_playlist():
    if not PLAYLIST_FILE.exists():
        print("Playlist not found!")
        return []

    return PLAYLIST_FILE.read_text(
        encoding="utf-8"
    ).splitlines()


def update_sports_section(lines, sources):

    output = []

    i = 0

    while i < len(lines):

        line = lines[i]

        if (
            line.startswith("#EXTINF")
            and 'group-title="Sports"' in line
        ):

            channel_name = line.split(",", 1)[1].strip()

            output.append(line)

            old_url = ""

            if i + 1 < len(lines):
                old_url = lines[i + 1]

            new_url = find_channel_url(
                channel_name,
                sources
            )

            if new_url:
                output.append(new_url)
            else:
                # Keep original link
                output.append(old_url)

            i += 2

        else:
            output.append(line)
            i += 1

    return output


def save_playlist(lines):

    PLAYLIST_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    print("Playlist saved successfully.")


def main():

    print("Starting Sports updater...")

    sources = load_sources()

    if not sources:
        print("No sources available. Stopping.")
        return

    playlist = read_playlist()

    if not playlist:
        print("Playlist is empty. Stopping.")
        return

    updated_playlist = update_sports_section(
        playlist,
        sources
    )

    save_playlist(updated_playlist)

    print("Update completed.")


if __name__ == "__main__":
    main()

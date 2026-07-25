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

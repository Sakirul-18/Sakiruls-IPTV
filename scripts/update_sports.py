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

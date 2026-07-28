import os
import re
import time
import requests
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION ---
MASTER_PLAYLIST = "SAKIRULs IPTV.m3u"
MAX_WORKERS = 15  # Number of concurrent threads for fetching & pinging

SOURCES = [
    # IPTVFlixBD - OopsTv
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/2.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/Sports-s7.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/alfa-wc.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/all-sports.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/asia.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/bd-spo.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/bd-test.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/bear.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/chspo.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/new-sp-s4.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/new-sports-fast.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s1.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/sports-s2.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/wc5.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/wc8.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/OopsTv/main/world-1.m3u",
    # IPTVFlixBD - BDIX
    "https://raw.githubusercontent.com/IPTVFlixBD/BDIX-IPTV-playlist/main/A1x.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/BDIX-IPTV-playlist/main/BDIX.m3u",
    "https://raw.githubusercontent.com/IPTVFlixBD/BDIX-IPTV-playlist/main/IPTV-mix.m3u",
    # IPTVFlixBD
    "https://raw.githubusercontent.com/IPTVFlixBD/iptv-playlist/main/PlexTV.m3u8",
    # CricHD
    "https://raw.githubusercontent.com/abusaeeidx/CricHd-playlists-Auto-Update-permanent/main/ALL.m3u",
    # IPTV Scraper Zilla
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/main/CricHD.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/main/BD.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/main/SamsungTVPlus-All.m3u",
    # Toffee
    "https://raw.githubusercontent.com/abusaeeidx/Toffee-playlist/main/ott_navigator.m3u",
    # T Sports
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/combine_playlist.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/ns_player.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/ott_navigator.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/T-Sports-Playlist-Auto-Update/main/universal_player.m3u",
    # KB TV
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/FIFA%20Live%20Playlist%20Server%20v1.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/FIFA%20Special%20KB%20Live%20Tv%20Playlist%20v1.4.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/Github%20Auto%20Update%20Channel.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/KB%20Live%20Tv%20121%20Channel%20v1.2.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/KB%20Live%20Tv%20Playlist%20v1.3.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/KB%20Live%20Tv%20Playlist%20v1.6.m3u",
    "https://raw.githubusercontent.com/sanjoykb/-KB-TV-Playlist/main/KB%20TV%20Playlist%2047%20Channel%20v1.0.m3u"
]

def clean_name(name):
    """Clean up channel name for better matching."""
    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'\(.*?\)', '', name)
    return name.strip()

def extract_numbers(text):
    """Extract all numbers from channel names to avoid digit mismatching."""
    return re.findall(r'\d+', text)

def similar(a, b):
    """
    Fuzzy matching ratio.
    Guards against matching 'Sports 1' with 'Sports 2' by strict number comparison.
    """
    nums_a = extract_numbers(a)
    nums_b = extract_numbers(b)
    
    # If both names contain numbers and they differ (e.g. 1 vs 2), reject match
    if nums_a and nums_b and nums_a != nums_b:
        return 0.0
        
    return SequenceMatcher(None, a.lower().replace(" ", ""), b.lower().replace(" ", "")).ratio()

def extract_wrapper(url, session):
    """Extract real stream link if wrapped inside a text playlist."""
    if any(ext in url for ext in [".m3u8", ".ts", "/ts"]):
        return url
    try:
        r = session.get(url, timeout=4)
        if r.status_code == 200:
            match = re.search(r'(https?://[^\s]+\.(?:m3u8|ts|m3u)[^\s]*)', r.text)
            if match:
                return match.group(1)
            match = re.search(r'(https?://[^\s]+)', r.text)
            if match:
                return match.group(1)
    except Exception:
        pass
    return url

def ping_url(url, session):
    """Test URL latency in parallel."""
    start = time.time()
    try:
        r = session.head(url, timeout=3, allow_redirects=True)
        if r.status_code in [200, 301, 302]:
            return url, time.time() - start
    except Exception:
        pass
    return url, float('inf')

def get_best_link(urls, session):
    """Pings multiple URLs concurrently and picks the fastest responding stream."""
    if not urls:
        return None
    if len(urls) == 1:
        return extract_wrapper(urls[0], session)

    best_url = urls[0]
    best_time = float('inf')

    with ThreadPoolExecutor(max_workers=min(len(urls), 10)) as executor:
        futures = [executor.submit(ping_url, u, session) for u in urls]
        for future in as_completed(futures):
            url, latency = future.result()
            if latency < best_time:
                best_time = latency
                best_url = url

    return extract_wrapper(best_url, session)

def parse_m3u(content):
    """Parses M3U preserving extra metadata lines (like #EXTGRP, headers, etc.)."""
    channels = []
    blocks = content.split("#EXTINF:")
    for block in blocks[1:]:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        info_line = lines[0]
        name = info_line.split(",")[-1].strip()

        meta_lines = []
        url = None

        for line in lines[1:]:
            if line.startswith(("http://", "https://", "rtmp://", "rtsp://", "udp://")):
                url = line
                break
            else:
                meta_lines.append(line)

        if name and url:
            channels.append({
                "raw_info": "#EXTINF:" + info_line,
                "meta_lines": meta_lines,
                "name": name,
                "clean_name": clean_name(name),
                "url": url
            })
    return channels

def fetch_source(source_url, session):
    """Worker function to fetch source playlists concurrently."""
    try:
        r = session.get(source_url, timeout=8)
        if r.status_code == 200:
            return parse_m3u(r.text)
    except Exception as e:
        print(f"⚠️ Failed to fetch {source_url}: {e}")
    return []

def main():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })

    print(f"Fetching {len(SOURCES)} source playlists in parallel...")
    source_channels = {}

    # Concurrent Playlist Fetching
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {executor.submit(fetch_source, url, session): url for url in SOURCES}
        for future in as_completed(future_to_url):
            channels = future.result()
            for ch in channels:
                c_name = ch['clean_name'].lower()
                if c_name not in source_channels:
                    source_channels[c_name] = []
                if ch['url'] not in source_channels[c_name]:
                    source_channels[c_name].append(ch['url'])

    print(f"Indexed {len(source_channels)} unique channel titles from sources.")

    if not os.path.exists(MASTER_PLAYLIST):
        print(f"❌ Master playlist {MASTER_PLAYLIST} not found!")
        return

    with open(MASTER_PLAYLIST, 'r', encoding='utf-8') as f:
        master_parsed = parse_m3u(f.read())

    print(f"Updating {MASTER_PLAYLIST}...")
    updated_lines = ["#EXTM3U\n"]

    for my_ch in master_parsed:
        # Helper block generator to preserve channel tags & metadata
        def format_channel_block(stream_url):
            block = [my_ch['raw_info']]
            block.extend(my_ch['meta_lines'])
            block.append(stream_url)
            return "\n".join(block) + "\n"

        # 1. SKIP FANCODE - Handled separately
        if "fancode" in my_ch['name'].lower():
            updated_lines.append(format_channel_block(my_ch['url']))
            continue

        # 2. Check category rules
        group_match = re.search(r'''group-title=["']?([^"',\n\r]+)''', my_ch['raw_info'], re.IGNORECASE)
        group_title = group_match.group(1).lower() if group_match else ""
        is_sports = ("sport" in group_title) if group_title else ("sport" in my_ch['raw_info'].lower())

        # 🔒 CATEGORY GUARD: Skip non-sports channels
        if not is_sports:
            updated_lines.append(format_channel_block(my_ch['url']))
            continue

        # 3. Fuzzy Matching
        best_match_name = None
        highest_ratio = 0.0

        for src_name in source_channels.keys():
            ratio = similar(my_ch['clean_name'], src_name)
            if ratio > 0.85 and ratio > highest_ratio:
                highest_ratio = ratio
                best_match_name = src_name

        if best_match_name:
            print(f"✅ Matched '{my_ch['name']}' -> '{best_match_name}' (Ratio: {highest_ratio:.2f})")
            urls = source_channels[best_match_name]
            best_link = get_best_link(urls, session)
            updated_lines.append(format_channel_block(best_link))
        else:
            updated_lines.append(format_channel_block(my_ch['url']))

    with open(MASTER_PLAYLIST, 'w', encoding='utf-8') as f:
        f.writelines(updated_lines)

    print("🎉 Update complete! All sports channels optimized and metadata preserved.")

if __name__ == "__main__":
    main()

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
    """Fuzzy matching ratio."""
    nums_a = extract_numbers(a)
    nums_b = extract_numbers(b)
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
    """Test URL latency using a GET request (better for IPTV streams)."""
    start = time.time()
    try:
        # stream=True connects to verify the stream is active without downloading the video file
        r = session.get(url, timeout=3, stream=True, allow_redirects=True)
        if r.status_code in [200, 206, 301, 302]:
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

def parse_source_m3u(content):
    """Parses source M3U files (only used for grabbing URLs, not master file)."""
    channels = []
    blocks = content.split("#EXTINF:")
    for block in blocks[1:]:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        name = lines[0].split(",")[-1].strip()
        url = None
        for line in lines[1:]:
            if line.startswith(("http://", "https://", "rtmp://", "rtsp://", "udp://")):
                url = line
                break
        if name and url:
            channels.append({"clean_name": clean_name(name), "url": url})
    return channels

def fetch_source(source_url, session):
    """Worker function to fetch source playlists concurrently."""
    try:
        r = session.get(source_url, timeout=8)
        if r.status_code == 200:
            return parse_source_m3u(r.text)
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

    print(f"Updating {MASTER_PLAYLIST} while preserving ALL custom text/names...")
    
    # Read the file line-by-line to preserve structure entirely
    with open(MASTER_PLAYLIST, 'r', encoding='utf-8') as f:
        master_lines = f.readlines()

    updated_lines = []
    i = 0
    while i < len(master_lines):
        line = master_lines[i].strip()

        # If it's a channel declaration, process it
        if line.startswith("#EXTINF:"):
            info_line = line
            j = i + 1
            url_line_index = -1
            
            # Find the URL that belongs to this channel
            while j < len(master_lines):
                if master_lines[j].strip().startswith(("http://", "https://", "rtmp://", "rtsp://", "udp://")):
                    url_line_index = j
                    break
                j += 1

            if url_line_index == -1:
                # No URL found, keep the line and move on
                updated_lines.append(master_lines[i])
                i += 1
                continue

            name = info_line.split(",")[-1].strip()
            clean_ch_name = clean_name(name)

            # Skip Fancode updates here
            if "fancode" in name.lower():
                for k in range(i, url_line_index + 1):
                    updated_lines.append(master_lines[k])
                i = url_line_index + 1
                continue

            # Check if it is a sports channel
            group_match = re.search(r'''group-title=["']?([^"',\n\r]+)''', info_line, re.IGNORECASE)
            group_title = group_match.group(1).lower() if group_match else ""
            is_sports = ("sport" in group_title) if group_title else ("sport" in info_line.lower())

            if not is_sports:
                for k in range(i, url_line_index + 1):
                    updated_lines.append(master_lines[k])
                i = url_line_index + 1
                continue

            # Find best match from scraped sources
            best_match_name = None
            highest_ratio = 0.0

            for src_name in source_channels.keys():
                ratio = similar(clean_ch_name, src_name)
                if ratio > 0.85 and ratio > highest_ratio:
                    highest_ratio = ratio
                    best_match_name = src_name

            # Write data to file
            if best_match_name:
                print(f"✅ Updated URL for your channel: '{name}'")
                urls = source_channels[best_match_name]
                best_link = get_best_link(urls, session)
                
                # Append original channel name/info and meta lines exactly as they were
                for k in range(i, url_line_index):
                    updated_lines.append(master_lines[k])
                # Append the new best URL
                updated_lines.append(best_link + "\n")
            else:
                # No match found, keep the old block exactly as is
                for k in range(i, url_line_index + 1):
                    updated_lines.append(master_lines[k])

            i = url_line_index + 1 # Skip past the old URL block
        else:
            # THIS KEEPS ALL CUSTOM CATEGORIES, # SPORTS, AND BLANK LINES SAFE
            updated_lines.append(master_lines[i])
            i += 1

    with open(MASTER_PLAYLIST, 'w', encoding='utf-8') as f:
        f.writelines(updated_lines)

    print("🎉 Update complete! Custom names preserved, fast links injected.")

if __name__ == "__main__":
    main()

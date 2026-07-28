import os
import re
import time
import requests
from difflib import SequenceMatcher

# --- CONFIGURATION ---
MASTER_PLAYLIST = "SAKIRULs IPTV.m3u"

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

def similar(a, b):
    """Fuzzy matching ratio to compare channel names."""
    return SequenceMatcher(None, a.lower().replace(" ", ""), b.lower().replace(" ", "")).ratio()

def clean_name(name):
    """Clean up channel name for better matching."""
    name = re.sub(r'\[.*?\]', '', name) # Remove tags like [HD]
    name = re.sub(r'\(.*?\)', '', name)
    return name.strip()

def extract_wrapper(url):
    """If a URL is a wrapper (e.g., github raw txt/m3u), extract the real .m3u8 stream."""
    if ".m3u8" in url or ".ts" in url or "/ts" in url:
        return url
    
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            match = re.search(r'(https?://[^\s]+\.(?:m3u8|ts|m3u)[^\s]*)', r.text)
            if match:
                return match.group(1)
            match = re.search(r'(https?://[^\s]+)', r.text)
            if match:
                return match.group(1)
    except Exception as e:
        print(f"Failed to unwrap {url}: {e}")
    
    return url

def get_best_link(urls):
    """Pings multiple URLs and returns the one with the lowest latency/fastest response."""
    if not urls:
        return None
    if len(urls) == 1:
        return extract_wrapper(urls[0])
        
    best_url = None
    best_time = float('inf')
    
    for url in urls:
        start = time.time()
        try:
            r = requests.head(url, timeout=3, allow_redirects=True)
            if r.status_code in [200, 302, 301]:
                elapsed = time.time() - start
                if elapsed < best_time:
                    best_time = elapsed
                    best_url = url
        except:
            continue
            
    chosen_url = best_url if best_url else urls[0]
    return extract_wrapper(chosen_url)

def parse_m3u(content):
    """Parses an M3U file into a list of dictionaries."""
    channels = []
    blocks = content.split("#EXTINF:")
    for block in blocks[1:]:
        lines = block.strip().split("\n")
        if len(lines) >= 2:
            info_line = lines[0]
            name = info_line.split(",")[-1].strip()
            
            url = None
            for line in lines[1:]:
                if line.startswith("http"):
                    url = line.strip()
                    break
                    
            if name and url:
                channels.append({
                    "raw_info": "#EXTINF:" + info_line,
                    "name": name,
                    "clean_name": clean_name(name),
                    "url": url,
                    "original_block": "#EXTINF:" + block
                })
    return channels

def main():
    print("Fetching source playlists...")
    source_channels = {}
    
    # 1. Scrape all sources
    for source in SOURCES:
        try:
            r = requests.get(source, timeout=10)
            if r.status_code == 200:
                parsed = parse_m3u(r.text)
                for ch in parsed:
                    c_name = ch['clean_name'].lower()
                    if c_name not in source_channels:
                        source_channels[c_name] = []
                    source_channels[c_name].append(ch['url'])
        except Exception as e:
            print(f"Failed to fetch {source}: {e}")

    # 2. Read master playlist
    if not os.path.exists(MASTER_PLAYLIST):
        print(f"Master playlist {MASTER_PLAYLIST} not found!")
        return

    with open(MASTER_PLAYLIST, 'r', encoding='utf-8') as f:
        master_content = f.read()
    
    master_parsed = parse_m3u(master_content)
    
    print(f"Updating {MASTER_PLAYLIST}...")
    updated_lines = ["#EXTM3U\n"]
    
    # 3. Match and Update (SPORTS CATEGORY ONLY)
    for my_ch in master_parsed:
        # SKIP FANCODE - Handled separately
        if "fancode" in my_ch['name'].lower():
            updated_lines.append(f"{my_ch['raw_info']}\n{my_ch['url']}\n")
            continue

        # Extract group-title attribute (Category) from #EXTINF header
        group_match = re.search(r'group-title=["\']?([^"',\n\r]+)', my_ch['raw_info'], re.IGNORECASE)
        group_title = group_match.group(1).lower() if group_match else ""

        # Check if category contains "sport" (e.g., Sports, BD Sports, Sports HD)
        is_sports = ("sport" in group_title) if group_title else ("sport" in my_ch['raw_info'].lower())

        # 🔒 CATEGORY GUARD: Skip non-sports channels and keep original links untouched!
        if not is_sports:
            updated_lines.append(f"{my_ch['raw_info']}\n{my_ch['url']}\n")
            continue

        # Fuzzy Matching (Runs ONLY for Sports channels)
        best_match_name = None
        highest_ratio = 0
        
        for src_name in source_channels.keys():
            ratio = similar(my_ch['clean_name'], src_name)
            if ratio > 0.85 and ratio > highest_ratio:
                highest_ratio = ratio
                best_match_name = src_name
                
        if best_match_name:
            print(f"Matched Sports Channel '{my_ch['name']}' with source '{best_match_name}' (Ratio: {highest_ratio:.2f})")
            urls = source_channels[best_match_name]
            best_link = get_best_link(urls)
            
            # Write updated sports channel
            updated_lines.append(f"{my_ch['raw_info']}\n{best_link}\n")
        else:
            # If no match found in sources, keep original link
            updated_lines.append(f"{my_ch['raw_info']}\n{my_ch['url']}\n")

    # 4. Save the updated master playlist
    with open(MASTER_PLAYLIST, 'w', encoding='utf-8') as f:
        f.writelines(updated_lines)
    
    print("Update complete! Only Sports category links were updated.")

if __name__ == "__main__":
    main()

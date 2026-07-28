#!/usr/bin/env python3
import subprocess
import os

MASTER_PLAYLIST = "SAKIRULs IPTV.m3u"

def parse_m3u_content(content):
    """Parses M3U string content into a dictionary of {channel_name: stream_url}."""
    channels = {}
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
                channels[name] = url
    return channels

def main():
    print("========================================")
    print("      IPTV CHANNEL UPDATE TRACKER      ")
    print("========================================\n")

    if not os.path.exists(MASTER_PLAYLIST):
        print(f"Error: Master playlist '{MASTER_PLAYLIST}' not found!")
        return

    # 1. Fetch original file state from Git HEAD (before python scripts ran)
    try:
        old_content = subprocess.check_output(
            ["git", "show", f"HEAD:{MASTER_PLAYLIST}"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8", errors="ignore")
    except Exception:
        print("Note: First run or unable to read Git HEAD.")
        return

    # 2. Read updated file state from disk
    with open(MASTER_PLAYLIST, "r", encoding="utf-8", errors="ignore") as f:
        new_content = f.read()

    # 3. Parse both M3U states
    old_channels = parse_m3u_content(old_content)
    new_channels = parse_m3u_content(new_content)

    # 4. Compare channel URLs
    updated_channels = []
    for ch_name, new_url in new_channels.items():
        old_url = old_channels.get(ch_name)
        if old_url and old_url != new_url:
            updated_channels.append({
                "name": ch_name,
                "old_url": old_url,
                "new_url": new_url
            })

    # 5. Output summary to GitHub Actions log
    if updated_channels:
        print(f"✅ Total Channels Updated: {len(updated_channels)}\n")
        for idx, item in enumerate(updated_channels, 1):
            print(f"[{idx}] Channel: {item['name']}")
            print(f"    ├─ Old Link: {item['old_url']}")
            print(f"    └─ New Link: {item['new_url']}\n")
    else:
        print("ℹ️ No channel links changed during this run.")

    print("========================================")

if __name__ == "__main__":
    main()

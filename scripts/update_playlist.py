import re
import requests
from collections import defaultdict, deque

SOURCE = "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/playlist.m3u"
TARGET = "SAKIRULs IPTV.m3u"

# Download FanCode playlist
remote = requests.get(SOURCE, timeout=30).text.splitlines()

# Read your playlist
with open(TARGET, "r", encoding="utf-8") as f:
    local = f.read().splitlines()

# Extract every #EXTINF + URL pair from the FanCode playlist
remote_entries = []
i = 0
while i < len(remote):
    if remote[i].startswith("#EXTINF"):
        if i + 1 < len(remote):
            remote_entries.append((remote[i], remote[i + 1]))
            i += 2
        else:
            i += 1
    else:
        i += 1

# Group remote entries by category (group-title), preserving their order
remote_by_category = defaultdict(deque)
for extinf, url in remote_entries:
    match = re.search(r'group-title="([^"]+)"', extinf)
    category = match.group(1) if match else None
    if category:
        remote_by_category[category].append(url)

# The category each local Fancode slot expects, in the order they appear
patterns = [
    "Fancode-Cricket",
    "Fancode-Cricket",
    "Fancode-Cricket",
    "Fancode-Tennis",
    "Fancode-Motorsports",
    "Fancode-Golf",
    "Fancode-Motorsports",
]

new_playlist = []
index = 0
i = 0
updated = 0
skipped = 0
while i < len(local):
    line = local[i]
    if (
        line.startswith("#EXTINF")
        and index < len(patterns)
        and patterns[index] in line
    ):
        category = patterns[index]
        new_playlist.append(line)
        if remote_by_category[category]:
            # Swap in the next available live stream URL for this category
            new_playlist.append(remote_by_category[category].popleft())
            updated += 1
        elif i + 1 < len(local):
            # No live stream for this category right now -> keep the old URL
            new_playlist.append(local[i + 1])
            skipped += 1
        index += 1
        i += 2
    else:
        new_playlist.append(line)
        i += 1

with open(TARGET, "w", encoding="utf-8") as f:
    f.write("\n".join(new_playlist))

print(f"Playlist updated successfully. {updated} channel(s) updated, {skipped} left unchanged (no live stream available).")

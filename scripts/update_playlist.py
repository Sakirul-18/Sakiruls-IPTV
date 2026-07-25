import requests

SOURCE = "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/playlist.m3u"
TARGET = "SAKIRULs IPTV.m3u"

# Download FanCode playlist
remote = requests.get(SOURCE, timeout=30).text.splitlines()

# Read your playlist
with open(TARGET, "r", encoding="utf-8") as f:
    local = f.read().splitlines()

# Extract first 7 channels from FanCode playlist
remote_channels = []

i = 0
while i < len(remote):
    if remote[i].startswith("#EXTINF"):
        if i + 1 < len(remote):
            remote_channels.append((remote[i], remote[i + 1]))
        i += 2
    else:
        i += 1

remote_channels = remote_channels[:7]

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

while i < len(local):
    line = local[i]

    if (
        line.startswith("#EXTINF")
        and index < len(patterns)
        and patterns[index] in line
    ):
        new_playlist.append(line)
        new_playlist.append(remote_channels[index][1])

        index += 1
        i += 2
    else:
        new_playlist.append(line)
        i += 1

with open(TARGET, "w", encoding="utf-8") as f:
    f.write("\n".join(new_playlist))

print("Playlist updated successfully.")

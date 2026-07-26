#!/usr/bin/env python3
"""
FanCode IPTV Auto Updater (dynamic version)

What it does, simply:
- Downloads the FanCode source JSON.
- Groups its live stream URLs by "event_category" (Cricket, Golf, Tennis, ...).
- For each category, finds the matching "Fancode <category>" channel in your
  playlist (name matching is case/whitespace/singular-plural tolerant, so
  "Motorsports" in the source still matches your existing "Fancode Motorsport"
  channel).
- Fills in URLs for that channel's existing slots, in order.
- If the source has MORE live matches than you have slots for that channel,
  extra slots are created automatically.
- If a category shows up that you don't have a channel for AT ALL yet
  (e.g. Motorsport goes live and you deleted all its placeholders), a new
  "Fancode <Category>" channel is created automatically, right after your
  other Fancode channels.

Rules preserved from before:
- Only FanCode channels are touched.
- #EXTINF metadata is never changed for existing entries.
- Existing channels/slots are never deleted.
- Categories with no live match right now are simply left alone.
"""
from pathlib import Path
import requests

# ========= SETTINGS =========
PLAYLIST_FILE = Path("SAKIRULs IPTV.m3u")
SOURCE_URL = (
    "https://raw.githubusercontent.com/IPTVFlixBD/Fancode-BD/refs/heads/main/data.json"
)
CHANNEL_PREFIX = "Fancode"  # used both to spot existing FanCode lines and to
                            # name brand-new channels
TIMEOUT = 20
VERBOSE = True


def _normalize(text):
    """Lowercase, strip, and drop one trailing 's' so 'Motorsport' and
    'Motorsports' (or any category/plural drift) are treated as the same
    channel."""
    t = text.strip().lower()
    return t[:-1] if t.endswith("s") else t


def download_source():
    response = requests.get(SOURCE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def group_source_urls(data):
    """category (as written in the source) -> list of live stream URLs.
    Matches with no adfree_url/dai_url are not currently live and are
    skipped automatically."""
    grouped = {}
    for match in data.get("matches", []):
        category = match.get("event_category")
        if not category:
            continue
        url = match.get("adfree_url") or match.get("dai_url")
        if not url:
            continue
        grouped.setdefault(category, []).append(url)
    return grouped


def find_existing_channel_names(lines):
    """normalized name -> exact channel name as it already appears in the
    playlist, for every existing #EXTINF line that looks like a FanCode
    channel."""
    existing = {}
    for line in lines:
        if line.startswith("#EXTINF") and CHANNEL_PREFIX.lower() in line.lower():
            name = line.rsplit(",", 1)[-1].strip()
            existing.setdefault(_normalize(name), name)
    return existing


def resolve_channel_name(category, existing_lookup):
    """Reuse your existing channel name if one already matches this
    category; otherwise mint a new 'Fancode <Category>' name."""
    wanted = _normalize(f"{CHANNEL_PREFIX} {category}")
    return existing_lookup.get(wanted, f"{CHANNEL_PREFIX} {category.strip()}")


def update_playlist(source_urls_by_category):
    lines = PLAYLIST_FILE.read_text(encoding="utf-8").splitlines()
    existing_lookup = find_existing_channel_names(lines)

    # category -> (resolved channel name, urls)
    channel_urls = {}
    for category, urls in source_urls_by_category.items():
        name = resolve_channel_name(category, existing_lookup)
        channel_urls[name] = urls

    counters = {name: 0 for name in channel_urls}
    output = []
    last_fancode_idx = None  # output index of the last FanCode-related line seen
    i = 0
    while i < len(lines):
        line = lines[i]
        output.append(line)

        if line.startswith("#EXTINF") and CHANNEL_PREFIX.lower() in line.lower():
            name = line.rsplit(",", 1)[-1].strip()
            urls = channel_urls.get(name)

            if urls is not None:
                index = counters[name]
                has_next = i + 1 < len(lines)
                next_is_url = has_next and lines[i + 1].strip().startswith("http")

                if next_is_url:
                    if index < len(urls):
                        output.append(urls[index])
                        if VERBOSE:
                            print(f"  [line {i+1}] {name} slot {index}: replaced URL")
                    else:
                        output.append(lines[i + 1])
                    i += 1
                else:
                    if index < len(urls):
                        output.append(urls[index])
                        if VERBOSE:
                            print(f"  [line {i+1}] {name} slot {index}: filled empty slot")
                        if has_next and lines[i + 1].strip() == "":
                            i += 1

                counters[name] += 1

            last_fancode_idx = len(output) - 1

        i += 1

    # Anything left over: more live matches than existing slots (append new
    # slots for that channel) or a category with no channel at all yet
    # (create it from scratch). Both cases are just "extra lines to insert".
    extra_lines = []
    for name, urls in channel_urls.items():
        used = counters.get(name, 0)
        for url in urls[used:]:
            extra_lines.append(f'#EXTINF:-1, tvg-logo="" group-title="Sports", {name}')
            extra_lines.append(url)
            if VERBOSE:
                print(f"  [new] {name}: added new slot")

    if extra_lines:
        insert_at = (last_fancode_idx + 1) if last_fancode_idx is not None else len(output)
        output = output[:insert_at] + extra_lines + output[insert_at:]

    PLAYLIST_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")


def main():
    print("Downloading FanCode source...")
    data = download_source()
    grouped = group_source_urls(data)
    for category, urls in grouped.items():
        print(f"  {category}: {len(urls)} live URL(s) found")
    print("Updating playlist...")
    update_playlist(grouped)
    print("Done!")


if __name__ == "__main__":
    main()

import csv
import json
import os
import sys
import warnings
from pathlib import Path
from time import sleep

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth


CSV_FILE = Path(os.getenv("CSV_FILE", "tracks.csv"))
PLAYLIST_NAME = os.getenv("PLAYLIST_NAME", "Imported from CSV")
PLAYLIST_ID = os.getenv("SPOTIFY_PLAYLIST_ID")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

CACHE_FILE = Path(os.getenv("CACHE_FILE", "spotify_search_cache.json"))
NOT_FOUND_FILE = Path(os.getenv("NOT_FOUND_FILE", "not_found.csv"))
ADD_BATCH_SIZE = 100
SEARCH_DELAY_SECONDS = 0.08
MAX_RETRIES = 6


def require_env(name, value):
    if not value:
        raise RuntimeError(f"Set {name} in the environment.")


def load_tracks(csv_file):
    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV is empty or has no header row.")

        field_map = {field.strip().lower(): field for field in reader.fieldnames}
        if not {"artist", "title"}.issubset(field_map):
            raise ValueError("CSV must have columns: artist,title")

        tracks = []
        for line_number, row in enumerate(reader, start=2):
            artist = (row.get(field_map["artist"]) or "").strip()
            title = (row.get(field_map["title"]) or "").strip()
            if artist and title:
                tracks.append({"artist": artist, "title": title})
            else:
                print(f"SKIP line {line_number}: missing artist or title")

    return tracks


def load_cache(cache_file):
    if not cache_file.exists():
        return {}

    with cache_file.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_cache(cache_file, cache):
    tmp_file = cache_file.with_suffix(".tmp")
    with tmp_file.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_file.replace(cache_file)


def cache_key(track):
    return f'{track["artist"]}\0{track["title"]}'


def spotify_call(func, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except SpotifyException as exc:
            if exc.http_status == 429:
                retry_after = int(exc.headers.get("Retry-After", "5"))
                print(f"RATE LIMIT: waiting {retry_after}s")
                sleep(retry_after)
                continue

            if exc.http_status and 500 <= exc.http_status < 600 and attempt < MAX_RETRIES:
                wait = min(2 ** attempt, 30)
                print(f"SPOTIFY {exc.http_status}: retry {attempt}/{MAX_RETRIES} in {wait}s")
                sleep(wait)
                continue

            raise
        except Exception:
            if attempt == MAX_RETRIES:
                raise

            wait = min(2 ** attempt, 30)
            print(f"ERROR: retry {attempt}/{MAX_RETRIES} in {wait}s")
            sleep(wait)

    raise RuntimeError("Spotify call failed after retries.")


def search_track(sp, artist, title):
    queries = [
        f'track:"{title}" artist:"{artist}"',
        f"{title} {artist}",
    ]

    for query in queries:
        result = spotify_call(sp.search, q=query, type="track", limit=1)
        items = result.get("tracks", {}).get("items", [])
        if items:
            return items[0]["uri"]

    return None


def write_not_found(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["artist", "title"])
        writer.writeheader()
        writer.writerows(rows)


def main():
    require_env("SPOTIFY_CLIENT_ID", CLIENT_ID)
    require_env("SPOTIFY_CLIENT_SECRET", CLIENT_SECRET)

    if not CSV_FILE.exists():
        raise FileNotFoundError(f"CSV file not found: {CSV_FILE}")

    tracks = load_tracks(CSV_FILE)
    cache = load_cache(CACHE_FILE)
    print(f"Loaded {len(tracks)} tracks from {CSV_FILE}")
    print(f"Loaded {len(cache)} cached search results from {CACHE_FILE}")

    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scope="playlist-modify-private playlist-modify-public",
        )
    )

    user_id = spotify_call(sp.current_user)["id"]
    track_uris = []
    not_found = []

    for index, track in enumerate(tracks, start=1):
        key = cache_key(track)
        cached_uri = cache.get(key)

        if key in cache:
            uri = cached_uri
            status = "CACHE OK" if uri else "CACHE NOT FOUND"
        else:
            uri = search_track(sp, track["artist"], track["title"])
            cache[key] = uri
            save_cache(CACHE_FILE, cache)
            status = "OK" if uri else "NOT FOUND"
            sleep(SEARCH_DELAY_SECONDS)

        if uri:
            track_uris.append(uri)
        else:
            not_found.append(track)

        print(f'{index}/{len(tracks)} {status}: {track["artist"]} - {track["title"]}')

    write_not_found(NOT_FOUND_FILE, not_found)

    if PLAYLIST_ID:
        playlist_id = PLAYLIST_ID
        playlist_url = f"https://open.spotify.com/playlist/{PLAYLIST_ID}"
        print(f"Using existing playlist: {playlist_url}")
    else:
        playlist = spotify_call(
            sp.user_playlist_create,
            user=user_id,
            name=PLAYLIST_NAME,
            public=False,
            description=f"Imported from {CSV_FILE}",
        )
        playlist_id = playlist["id"]
        playlist_url = playlist["external_urls"]["spotify"]
        print(f"Created playlist: {playlist_url}")

    for start in range(0, len(track_uris), ADD_BATCH_SIZE):
        batch = track_uris[start : start + ADD_BATCH_SIZE]
        spotify_call(sp.playlist_add_items, playlist_id, batch)
        print(f"Added {start + len(batch)}/{len(track_uris)}")

    print(f"\nDone: {len(track_uris)} tracks added.")
    print(f"Playlist: {playlist_url}")
    print(f"Not found: {len(not_found)}. See {NOT_FOUND_FILE}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user. Re-run the script to continue from the search cache.")
        sys.exit(130)

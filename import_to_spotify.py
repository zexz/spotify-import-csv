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


def load_dotenv(path):
    if not path.exists():
        return

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_dotenv(Path(".env"))

CSV_FILE = Path(os.getenv("CSV_FILE", "tracks.csv"))
ENV_FILE = Path(os.getenv("ENV_FILE", ".env"))

PLAYLIST_NAME = os.getenv("PLAYLIST_NAME", "Imported")
PLAYLIST_ID = os.getenv("SPOTIFY_PLAYLIST_ID")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

CACHE_FILE = Path(os.getenv("CACHE_FILE", "spotify_search_cache.json"))
STATE_FILE = Path(os.getenv("STATE_FILE", "spotify_import_state.json"))
NOT_FOUND_FILE = Path(os.getenv("NOT_FOUND_FILE", "not_found.csv"))
ADD_BATCH_SIZE = int(os.getenv("SPOTIFY_ADD_BATCH_SIZE", "100"))
SEARCH_DELAY_SECONDS = float(os.getenv("SPOTIFY_SEARCH_DELAY_SECONDS", "1.5"))
ADD_DELAY_SECONDS = float(os.getenv("SPOTIFY_ADD_DELAY_SECONDS", "1.0"))
START_LINE = int(os.getenv("START_LINE", "2"))
MAX_429_SLEEP_SECONDS = int(os.getenv("MAX_429_SLEEP_SECONDS", "300"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "6"))
SPOTIPY_INTERNAL_RETRIES = int(os.getenv("SPOTIPY_INTERNAL_RETRIES", "0"))


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
                tracks.append({"line": line_number, "artist": artist, "title": title})
            else:
                print(f"SKIP line {line_number}: missing artist or title")

    return tracks


def load_cache(cache_file):
    if not cache_file.exists():
        return {}

    with cache_file.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_state(state_file):
    if not state_file.exists():
        return {}

    with state_file.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_cache(cache_file, cache):
    tmp_file = cache_file.with_suffix(".tmp")
    with tmp_file.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_file.replace(cache_file)


def save_state(state_file, state):
    tmp_file = state_file.with_suffix(".tmp")
    with tmp_file.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_file.replace(state_file)


def save_env_value(path, key, value):
    line = f"{key}={value}"

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    updated = False
    for index, existing_line in enumerate(lines):
        stripped = existing_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in existing_line:
            continue

        existing_key = existing_line.split("=", 1)[0].strip()
        if existing_key == key:
            lines[index] = line
            updated = True
            break

    if not updated:
        lines.append(line)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cache_key(track):
    return f'{track["artist"]}\0{track["title"]}'


def spotify_call(func, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except SpotifyException as exc:
            if exc.http_status == 429:
                retry_after = int(exc.headers.get("Retry-After", "5"))
                if retry_after > MAX_429_SLEEP_SECONDS:
                    raise RuntimeError(
                        "Spotify returned a long rate limit: "
                        f"{retry_after}s. Stop now and resume later with START_LINE."
                    ) from exc

                wait = retry_after + 1
                print(f"RATE LIMIT: waiting {wait}s")
                sleep(wait)
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
        sleep(SEARCH_DELAY_SECONDS)
        items = result.get("tracks", {}).get("items", [])
        if items:
            return items[0]["uri"]

    return None


def write_not_found(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["line", "artist", "title"])
        writer.writeheader()
        writer.writerows(rows)


def get_playlist(sp):
    state = load_state(STATE_FILE)

    if PLAYLIST_ID:
        playlist_id = PLAYLIST_ID
        playlist_url = f"https://open.spotify.com/playlist/{PLAYLIST_ID}"
        print(f"Using playlist from .env: {playlist_url}")
    elif state.get("playlist_id"):
        playlist_id = state["playlist_id"]
        playlist_url = state.get(
            "playlist_url",
            f"https://open.spotify.com/playlist/{playlist_id}",
        )
        print(f"Using playlist from {STATE_FILE}: {playlist_url}")
    else:
        playlist = spotify_call(
            sp.current_user_playlist_create,
            name=PLAYLIST_NAME,
            public=False,
            description=f"Imported from {CSV_FILE}",
        )
        playlist_id = playlist["id"]
        playlist_url = playlist["external_urls"]["spotify"]
        print(f"Created playlist: {playlist_url}")
        save_env_value(ENV_FILE, "SPOTIFY_PLAYLIST_ID", playlist_id)
        print(f"Saved SPOTIFY_PLAYLIST_ID to {ENV_FILE}")

    state["playlist_id"] = playlist_id
    state["playlist_url"] = playlist_url
    state.setdefault("added_lines", [])
    save_state(STATE_FILE, state)

    return playlist_id, playlist_url, state


def add_pending_tracks(sp, playlist_id, pending_tracks, state, force=False):
    if not pending_tracks:
        return 0

    if not force and len(pending_tracks) < ADD_BATCH_SIZE:
        return 0

    total_added = 0
    while len(pending_tracks) >= ADD_BATCH_SIZE or (force and pending_tracks):
        batch_tracks = pending_tracks[:ADD_BATCH_SIZE]
        batch_uris = [track["uri"] for track in batch_tracks]
        spotify_call(sp.playlist_add_items, playlist_id, batch_uris)

        added_lines = state.setdefault("added_lines", [])
        added_lines.extend(track["line"] for track in batch_tracks)
        state["last_added_line"] = batch_tracks[-1]["line"]
        state["added_count"] = len(added_lines)
        save_state(STATE_FILE, state)

        del pending_tracks[: len(batch_tracks)]
        total_added += len(batch_tracks)
        print(
            f'ADDED {total_added} this flush, '
            f'{state["added_count"]} total. Last CSV line: {state["last_added_line"]}'
        )
        sleep(ADD_DELAY_SECONDS)

    return total_added


def main():
    require_env("SPOTIFY_CLIENT_ID", CLIENT_ID)
    require_env("SPOTIFY_CLIENT_SECRET", CLIENT_SECRET)

    if not CSV_FILE.exists():
        raise FileNotFoundError(f"CSV file not found: {CSV_FILE}")

    all_tracks = load_tracks(CSV_FILE)
    tracks = [track for track in all_tracks if track["line"] >= START_LINE]
    cache = load_cache(CACHE_FILE)
    not_found = []
    print(f"Loaded {len(all_tracks)} valid tracks from {CSV_FILE}")
    print(f"Starting from CSV line {START_LINE}: {len(tracks)} tracks to process")
    print(f"Loaded {len(cache)} cached search results from {CACHE_FILE}")
    print(f"Search delay: {SEARCH_DELAY_SECONDS}s between Spotify search requests")

    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scope="playlist-modify-private playlist-modify-public",
        ),
        retries=SPOTIPY_INTERNAL_RETRIES,
        status_retries=SPOTIPY_INTERNAL_RETRIES,
    )

    user = spotify_call(sp.current_user)
    print(f'Authorized as Spotify user: {user["id"]}')
    playlist_id, playlist_url, state = get_playlist(sp)
    added_lines = set(state.get("added_lines", []))
    pending_tracks = []

    print(f"Playlist: {playlist_url}")
    print(f"Already added according to state: {len(added_lines)} tracks")

    for index, track in enumerate(tracks, start=1):
        if track["line"] in added_lines:
            print(
                f'{index}/{len(tracks)} line {track["line"]} '
                f'ALREADY ADDED: {track["artist"]} - {track["title"]}'
            )
            continue

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

        if uri:
            pending_tracks.append({**track, "uri": uri})
        else:
            not_found.append(track)

        print(
            f'{index}/{len(tracks)} line {track["line"]} '
            f'{status}: {track["artist"]} - {track["title"]}'
        )

        add_pending_tracks(sp, playlist_id, pending_tracks, state)

    add_pending_tracks(sp, playlist_id, pending_tracks, state, force=True)
    write_not_found(NOT_FOUND_FILE, not_found)

    print(f"\nDone: {state.get('added_count', 0)} tracks added according to state.")
    print(f"Playlist: {playlist_url}")
    print(f"Not found: {len(not_found)}. See {NOT_FOUND_FILE}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user. Re-run the script to continue from the search cache.")
        sys.exit(130)

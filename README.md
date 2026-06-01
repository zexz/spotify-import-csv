# Spotify playlist import

Imports tracks from `tracks.csv` into a Spotify playlist.

The CSV must have these columns:

```csv
Artist,Title
"Birdy","Wings"
```

## 1. Create Spotify credentials

1. Open the Spotify Developer Dashboard:
   https://developer.spotify.com/dashboard
2. Log in with the Spotify account where you want to create the playlist.
3. Click **Create app**.
4. Fill in any app name and description, for example:
   - App name: `CSV playlist import`
   - App description: `Local script for importing my playlist`
5. In **Redirect URIs**, add exactly:

```text
http://127.0.0.1:8888/callback
```

6. For API usage, select **Web API** if Spotify asks which API/SDKs you plan to use.
7. Save/create the app.
8. Open the created app settings and copy:
   - **Client ID**
   - **Client Secret**

Spotify's own Web API docs describe this app flow here:
https://developer.spotify.com/documentation/web-api/concepts/apps

## 2. Install dependencies

```bash
python3 -m pip install -r requirements.txt
```

## 3. Run the import

Edit `.env` and fill in your Spotify credentials:

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
PLAYLIST_NAME=Imported
```

Then run:

```bash
python3 import_to_spotify.py
```

On the first run, Spotify will open an authorization page in the browser. Approve access for your own account. The script needs permission to create and modify playlists.

The script creates or selects the playlist at the beginning of the run. If `SPOTIFY_PLAYLIST_ID` is empty and there is no playlist saved in `spotify_import_state.json`, it creates a new playlist named `Imported`, saves its id to `.env`, and saves the same id to `spotify_import_state.json`.

Then it searches tracks and adds them incrementally in batches of 100. When a batch is added, you will see output like:

```text
Created playlist: https://open.spotify.com/playlist/...
Saved SPOTIFY_PLAYLIST_ID to .env
ADDED 100 this flush, 100 total. Last CSV line: 101
```

At that point the tracks are actually in the Spotify playlist.

## Delay between requests

The delay is configured in `.env`:

```env
SPOTIFY_SEARCH_DELAY_SECONDS=2.5
```

For a 5000+ track import, keep it slow. A lower value can trigger a long Spotify rate limit.

The script also waits between playlist add batches:

```env
SPOTIFY_ADD_BATCH_SIZE=100
SPOTIFY_ADD_DELAY_SECONDS=1.0
```

## Resume after interruption

The script writes search results to:

```text
spotify_search_cache.json
```

It also writes playlist/import progress to:

```text
spotify_import_state.json
```

If the script is interrupted, run it again. It will reuse cached search results and skip CSV lines already added to the playlist according to `spotify_import_state.json`.

Normally keep:

```env
START_LINE=2
```

The state file is what prevents duplicates after interruption.

To intentionally start from a specific CSV line:

```env
START_LINE=679
```

Then run `python3 import_to_spotify.py`.

The line number is the real line number in `tracks.csv`, including the header. The first track is line `2`.

Normally leave `START_LINE=2` and let `spotify_import_state.json` skip tracks that were already added. Use `START_LINE` only when you intentionally want to ignore earlier CSV lines.

If a playlist was already created and you want to continue adding into the same playlist, set:

```env
SPOTIFY_PLAYLIST_ID=spotify_playlist_id
```

Then run `python3 import_to_spotify.py`.

If `SPOTIFY_PLAYLIST_ID` is empty, the script will reuse the playlist saved in `spotify_import_state.json`. If there is no saved playlist, it creates a new one.

To force a fresh new playlist, clear `SPOTIFY_PLAYLIST_ID` in `.env` and remove `spotify_import_state.json`.

You can get the playlist id from a Spotify playlist URL:

```text
https://open.spotify.com/playlist/THIS_PART_IS_THE_ID
```

## Output files

- `spotify_search_cache.json` - cached Spotify search results.
- `spotify_import_state.json` - playlist id and CSV lines that were already added.
- `not_found.csv` - tracks that were not found on Spotify.

## If Spotify returns a 24 hour rate limit

If you see a message like:

```text
Your application has reached a rate/request limit. Retry will occur after: 86136 s
```

stop the import and wait until the limit expires. Then resume with:

```env
START_LINE=2
SPOTIFY_SEARCH_DELAY_SECONDS=2.5
```

Then run `python3 import_to_spotify.py`.

The script will reuse the playlist saved in `spotify_import_state.json` and skip lines already added before the rate limit.

`SPOTIPY_INTERNAL_RETRIES=0` keeps Spotipy from sleeping internally for the full `Retry-After`; the script handles 429 responses itself and stops when Spotify asks for a long wait.

If your `Client Secret` was pasted into a file, chat, issue, or terminal recording, rotate/regenerate it in the Spotify Developer Dashboard before continuing.

## Notes for this CSV

`tracks.csv` currently has 5133 data rows. Three rows have an empty artist field, so the script will skip them and attempt to import 5130 tracks.

Spotify allows adding playlist items in batches of 100, so the script adds tracks in chunks while it processes the CSV.

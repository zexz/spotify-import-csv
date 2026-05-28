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

Set your Spotify credentials in the terminal:

```bash
export SPOTIFY_CLIENT_ID="my_client_id"
export SPOTIFY_CLIENT_SECRET="my_client_secret"
export SPOTIFY_REDIRECT_URI="http://127.0.0.1:8888/callback"
export PLAYLIST_NAME="Imported from CSV"
```

Then run:

```bash
python3 import_to_spotify.py
```

On the first run, Spotify will open an authorization page in the browser. Approve access for your own account. The script needs permission to create and modify playlists.

## Resume after interruption

The script writes search results to:

```text
spotify_search_cache.json
```

If the script is interrupted, run it again. It will reuse cached search results instead of searching all tracks again.

If a playlist was already created and you want to continue adding into the same playlist, set:

```bash
export SPOTIFY_PLAYLIST_ID="spotify_playlist_id"
python3 import_to_spotify.py
```

You can get the playlist id from a Spotify playlist URL:

```text
https://open.spotify.com/playlist/THIS_PART_IS_THE_ID
```

## Output files

- `spotify_search_cache.json` - cached Spotify search results.
- `not_found.csv` - tracks that were not found on Spotify.

## Notes for this CSV

`tracks.csv` currently has 5133 data rows. Three rows have an empty artist field, so the script will skip them and attempt to import 5130 tracks.

Spotify allows adding playlist items in batches of 100, so the script adds tracks in chunks after the search phase.

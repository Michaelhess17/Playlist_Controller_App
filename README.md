# Wedding Music Controller

A simple Flask-based web interface to control music playback for a wedding.

## Status

[![Python CI](https://github.com/Michaelhess17/Playlist_Controller_App/actions/workflows/ci.yml/badge.svg)](https://github.com/Michaelhess17/Playlist_Controller_App/actions/workflows/ci.yml)
[![Ruff](https://github.com/Michaelhess17/Playlist_Controller_App/actions/workflows/pylint.yml/badge.svg)](https://github.com/Michaelhess17/Playlist_Controller_App/actions/workflows/pylint.yml)

## Features

*   List playlists (folders) from a specified music directory.
*   Play, pause, stop, skip tracks.
*   Control volume.
*   View current playback status.
*   Reorder playlists and songs within playlists (drag and drop).
*   Set default volume per playlist.
*   Set whether to auto-advance to next playlist.
*   Set loop per playlist.
*   Set transition mode between playlists.
    * Stop mode sets the music to stop once the playlist is over or the "Next Playlist" button is pressed
    * Complete mode sets the music to move to advance to the next playlist after the current song completes
    * Fade mode fades out the current song immediately and moves to the next playlist if the "Next Playlist" button is pressed
*   Seek within the current track.
*   Rename playlists and songs.
*   Display playlist runtimes.

## Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/Michaelhess17/Playlist_Controller_App.git
    cd Playlist_Controller_App
    ```
2.  **Install dependencies:**
    *   Ensure you have Python 3 installed.
    *   Ensure `ffmpeg` (for `ffprobe`) is installed and in your PATH.
    *   **Using Pixi (Recommended):** Install Pixi ([https://pixi.sh/latest/installation/](https://pixi.sh/latest/installation/)) and run:
        ```bash
        pixi install
        ```
    *   **Using Nix (for NixOS users):** If you have Nix installed, the included `flake.nix` provides the necessary environment. Enter it using:
        ```bash
        nix develop
        ```
        (You will then need to run `pixi install` inside the Nix shell).
3.  **Configure:**
    *   Edit `app.py` and set the `MUSIC_DIR` variable to your actual music directory.
4.  **Run the application:**
    *   If using Pixi: `pixi run start`
    *   If using Nix/manual Python: `python app.py`
5.  Access the controller in your web browser, typically at `http://localhost:5522` or `http://<your-server-ip>:5522`.

## Testing

Run the tests using Pixi:

```bash
pixi run test
```

## Linting

Run the linter using Pixi:

```bash
pixi run lint
```

WARNING: This project was heavily "vibe coded". I don't recommend using it as a template for anything, but there might be some interesting ideas in here for people, so I figured I would share.

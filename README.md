# Wedding Music Controller

A simple Flask-based web interface to control music playback for a wedding.

## Status

[![Python CI](https://github.com/Michaelhess17/Playlist_Controller_App/actions/workflows/ci.yml/badge.svg)](https://github.com/Michaelhess17/Playlist_Controller_App/actions/workflows/ci.yml)

## Features

*   List playlists (folders) from a specified music directory.
*   Play, pause, stop, skip tracks.
*   Control volume.
*   View current playback status.
*   Reorder playlists and songs within playlists (drag and drop).
*   Set default volume per playlist.
*   Set auto-advance to next playlist.
*   Set loop per playlist.
*   Set transition mode (fade, complete, stop) between playlists.
*   Seek within the current track.
*   Rename playlists and songs.
*   Display playlist runtimes.

## Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
    cd YOUR_REPOSITORY
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
        (You might still need to run `pixi install` inside the Nix shell if `flake.nix` doesn't manage Python packages directly via Pixi).
3.  **Configure:**
    *   Edit `app.py` and set the `MUSIC_DIR` variable to your actual music directory.
4.  **Run the application:**
    *   If using Pixi: `pixi run start` (assuming a `start` task in `pixi.toml` runs `python app.py`)
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

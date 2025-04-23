from flask import Flask, render_template, jsonify, request
import os
import pygame
import glob  # Added glob
import json  # Added for persistent data
import time  # Added for potential future fade-in timing
import logging
import subprocess  # Add subprocess import
import re

logging.basicConfig(level=logging.WARNING)  # Set to WARNING

app = Flask(__name__)

# Configure music directory (replace with your actual path)
MUSIC_DIR = "/home/michael/Documents/Wedding/Music/Normalized"  # Updated
SUPPORTED_FORMATS = ["*.mp3", "*.ogg", "*.wav"]  # Add more if needed
PERSISTENCE_FILE = "playlist_data.json"  # Added for persistence

# Initialize Pygame
try:
    pygame.init()
    pygame.mixer.init()
    pygame.event.set_allowed(None)  # Disable default event queueing
    pygame.event.set_allowed(pygame.QUIT)  # Allow quit event
    FADEOUT_COMPLETE_EVENT = pygame.USEREVENT + 1  # Custom event for fadeout
    pygame.event.set_allowed(FADEOUT_COMPLETE_EVENT)  # Allow custom event
    print("Pygame mixer initialized successfully.")
except pygame.error as e:
    print(f"Error initializing pygame: {e}")
    # Exit or handle appropriately if pygame fails
    exit()


# --- Load Persistent Data ---
persistent_data = {
    "playlist_order": [],
    # Details now store {"song_order": [], "default_volume": None,
    # "auto_advance": False, "loop": False, "transition_mode": "fade"}
    "playlist_details": {},
}

# Transition modes
# fade = immediate transition, complete = finish current song, stop = stop
TRANSITION_MODES = ["fade", "complete", "stop"]


def load_persistent_data():
    """Loads playlist order, song orders, volumes, settings from JSON."""
    global persistent_data
    if os.path.exists(PERSISTENCE_FILE):
        try:
            with open(PERSISTENCE_FILE, "r") as f:
                data = json.load(f)
                # Basic validation
                is_list = isinstance(data.get("playlist_order"), list)
                is_dict = isinstance(data.get("playlist_details"), dict)
                if is_list and is_dict:
                    persistent_data = data
                    # Ensure new keys have defaults in loaded data
                    details_map = persistent_data.get("playlist_details", {})
                    for pl_name, details in details_map.items():
                        if "default_volume" not in details:
                            details["default_volume"] = None
                        # Default to false if missing
                        if "auto_advance" not in details:
                            details["auto_advance"] = False
                        if "loop" not in details:  # Default to false
                            details["loop"] = False
                        # Default to fade if missing
                        if "transition_mode" not in details:
                            details["transition_mode"] = "fade"
                    # print(f"Loaded data from {PERSISTENCE_FILE}")  # Noisy
                else:
                    msg = f"Warning: Invalid format in {PERSISTENCE_FILE}."
                    print(f"{msg} Using defaults.")
                    # Save default structure if file is corrupt
                    save_persistent_data()
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading {PERSISTENCE_FILE}: {e}. Using defaults.")
            save_persistent_data()  # Save default structure if file is corrupt
    else:
        print(f"{PERSISTENCE_FILE} not found. Will create default structure.")
        # Let's wait, it will be populated as playlists are interacted with.


def save_persistent_data():
    """Saves the current playlist order, song orders, and volumes to JSON."""
    global persistent_data
    try:
        with open(PERSISTENCE_FILE, "w") as f:
            json.dump(persistent_data, f, indent=2)
        # print(f"Saved persistent data to {PERSISTENCE_FILE}") # Noisy
    except IOError as e:
        print(f"Error saving persistent data to {PERSISTENCE_FILE}: {e}")


# Load data on startup
load_persistent_data()


# --- Global Playback State ---
current_playlist_files = []
current_track_index = -1
is_paused = False
current_playlist_name = None
current_song_duration_sec = 0
seek_request_while_paused = None
transition_after_current_song = None
pending_fade_playlist = None
stop_after_current_song = False  # Initialize the flag here
# Store calculated runtimes {playlist_name: runtime_minutes}
playlist_runtimes = {}
# ---------------------------

# Add this global variable at the top with your other globals:
last_event_check_time = time.time()


def get_playlists():
    """Returns a list of directories (playlists) respecting saved order."""
    actual_playlists = set()
    if not os.path.isdir(MUSIC_DIR):
        print(f"Warning: Music directory not found: {MUSIC_DIR}")
        return []
    try:
        for item in os.listdir(MUSIC_DIR):
            item_path = os.path.join(MUSIC_DIR, item)
            if os.path.isdir(item_path):
                actual_playlists.add(item)
    except Exception as e:
        print(f"Error reading music directory: {e}")
        return []

    # Get playlists in saved order, filtering out any that no longer exist
    saved_order = persistent_data.get("playlist_order", [])
    ordered_playlists = [pl for pl in saved_order if pl in actual_playlists]

    # Find playlists that are in the directory but not in the saved order
    new_playlists = sorted(list(actual_playlists - set(ordered_playlists)))

    # Combine and update persistent data if new playlists were found
    final_playlist_list = ordered_playlists + new_playlists
    if new_playlists:
        print(f"Found new playlists: {new_playlists}. Appending to order.")
        persistent_data["playlist_order"] = final_playlist_list
        # Ensure details exist for new playlists
        for pl in new_playlists:
            if pl not in persistent_data.get("playlist_details", {}):
                persistent_data["playlist_details"][pl] = {
                    "song_order": [],
                    "default_volume": None,
                    "auto_advance": False,
                    "loop": False,
                    "transition_mode": "fade",
                }
        save_persistent_data()  # Save the updated order immediately

    # Ensure details dict is consistent (add missing playlists with defaults)
    current_details = persistent_data.get("playlist_details", {})
    updated_details = {}
    for pl in final_playlist_list:
        details = current_details.get(
            pl,
            {
                "song_order": [],
                "default_volume": None,
                "auto_advance": False,
                "loop": False,
                "transition_mode": "fade",
            },
        )
        # Ensure all keys exist even if playlist was loaded from old file
        details.setdefault("song_order", [])
        details.setdefault("default_volume", None)
        details.setdefault("auto_advance", False)
        details.setdefault("loop", False)
        details.setdefault("transition_mode", "fade")
        updated_details[pl] = details
    persistent_data["playlist_details"] = updated_details

    return final_playlist_list


# --- Runtime Calculation Helpers ---


def get_song_duration(filename):
    """Gets the duration of a song file using ffprobe."""
    # Ensure the file exists before probing
    if not os.path.isfile(filename):
        print(f"Warning: Cannot get duration. File not found: {filename}")
        return 0
    try:
        # Use ffprobe to get duration
        args = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            filename,
        ]
        popen = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = popen.communicate()
        if popen.returncode == 0 and stdout:
            # Extract duration using regex
            match = re.search(r"duration=([0-9.]+)", stdout.decode("utf-8"))
            return float(match.group(1)) if match else 0
        else:
            # Log ffprobe errors if any
            error_output = stderr.decode("utf-8").strip()
            basename = os.path.basename(filename)
            print(f"Error getting duration for {basename} with ffprobe.")
            print(f"Return code: {popen.returncode}. Error: {error_output}")
            # Fallback to pygame if ffprobe fails?
            try:
                sound = pygame.mixer.Sound(filename)
                duration = sound.get_length()
                del sound
                print(f"Using pygame fallback for {basename}: {duration:.2f}s")
                return duration
            except Exception as pygame_e:
                basename = os.path.basename(filename)
                print(f"Pygame fallback failed for {basename}: {pygame_e}")
                return 0  # Return 0 if duration cannot be determined
    except FileNotFoundError:
        print("Error: ffprobe not found. Ensure it's installed and in PATH.")
        return 0  # Return 0 if ffprobe isn't available
    except Exception as e:
        basename = os.path.basename(filename)
        print(f"Unexpected error getting duration for {basename}: {e}")
        return 0  # Return 0 on unexpected errors


def calculate_playlist_runtime(playlist_name, playlist_details):
    """Calculates the total runtime of a playlist in minutes."""
    songs = playlist_details.get("song_order", [])
    if not songs:
        return 0.0
    total_runtime_sec = 0
    print(f"Calculating runtime for '{playlist_name}'...")  # Debug print
    for song_path in songs:
        duration = get_song_duration(song_path)
        # print(f"  - {os.path.basename(song_path)}: {duration:.2f}s") # Debug
        total_runtime_sec += duration
    runtime_min = round(total_runtime_sec / 60, 1)
    # Debug print
    print(f"Total runtime for '{playlist_name}': {runtime_min} minutes")
    return runtime_min


# --- Function to Calculate All Runtimes Once ---


def calculate_and_store_all_runtimes():
    """Calculates runtimes for all playlists and stores them globally."""
    global playlist_runtimes, persistent_data
    print("Calculating initial runtimes for all playlists...")
    all_playlists = get_playlists()  # Make sure we have the list
    all_details = persistent_data.get("playlist_details", {})
    new_runtimes = {}
    for pl_name in all_playlists:
        details = all_details.get(pl_name)
        if details:  # Check if details exist for the playlist
            runtime = calculate_playlist_runtime(pl_name, details)
            new_runtimes[pl_name] = runtime
        else:
            msg = f"Warning: No details found for playlist '{pl_name}'"
            print(f"{msg} during initial runtime calculation.")
            new_runtimes[pl_name] = 0.0  # Store 0 if no details
    playlist_runtimes = new_runtimes
    print("Finished calculating initial runtimes.")


# --- End Runtime Calculation Helpers ---


def _get_ordered_songs_for_playlist(playlist_name):
    """Gets the list of song file paths for a playlist, respecting order."""
    global persistent_data
    playlist_path = os.path.join(MUSIC_DIR, playlist_name)
    if not os.path.isdir(playlist_path):
        return []  # Playlist folder doesn't exist

    # 1. Find all actual supported files in the directory
    actual_files = set()
    for fmt in SUPPORTED_FORMATS:
        try:
            # Use absolute paths for consistency
            glob_pattern = os.path.join(os.path.abspath(playlist_path), fmt)
            actual_files.update(glob.glob(glob_pattern, recursive=False))
        except Exception as e:
            print(f"Error globbing for {fmt} in {playlist_path}: {e}")

    # 2. Get the saved order for this playlist
    playlist_details_map = persistent_data.get("playlist_details", {})
    default_details = {"song_order": [], "default_volume": None, "auto_advance": False}
    playlist_details = playlist_details_map.get(playlist_name, default_details)
    saved_song_order = playlist_details.get("song_order", [])

    # 3. Create the ordered list, filtering out saved songs no longer exist
    ordered_songs = [song for song in saved_song_order if song in actual_files]

    # 4. Find songs that are in the directory but not in the saved order
    new_songs = sorted(list(actual_files - set(ordered_songs)))

    # 5. Combine and update persistent data if the order needs changing
    final_song_list = ordered_songs + new_songs
    if new_songs or len(final_song_list) != len(saved_song_order):
        print(f"Updating song order for playlist '{playlist_name}'.")
        # Ensure details entry exists and has all keys
        if playlist_name not in persistent_data.get("playlist_details", {}):
            persistent_data["playlist_details"][playlist_name] = {
                "song_order": [],
                "default_volume": None,
                "auto_advance": False,
            }
        else:  # Ensure existing entry has new keys
            details = persistent_data["playlist_details"][playlist_name]
            details.setdefault("default_volume", None)
            details.setdefault("auto_advance", False)

        persistent_data["playlist_details"][playlist_name]["song_order"] = (
            final_song_list
        )
        save_persistent_data()

    return final_song_list


def _play_track(index):
    """Helper function to load and play a specific track index."""
    global current_track_index, is_paused, current_song_duration_sec
    global current_playlist_files, pending_fade_playlist
    if 0 <= index < len(current_playlist_files):
        track_path = current_playlist_files[index]
        try:
            print(f"Loading track: {track_path}")
            # Get duration first (might be slightly inaccurate/slow)
            try:
                sound = pygame.mixer.Sound(track_path)
                current_song_duration_sec = sound.get_length()
                dur_str = f"{current_song_duration_sec:.2f}"
                print(f"Track duration: {dur_str} seconds")
                del sound  # Free up memory
            except Exception as e:
                basename = os.path.basename(track_path)
                print(f"Warn: Could not get duration for {basename}: {e}")
                current_song_duration_sec = 0  # Indicate unknown duration

            pygame.mixer.music.load(track_path)
            pygame.mixer.music.play()
            current_track_index = index
            is_paused = False
            track_num = index + 1
            total_tracks = len(current_playlist_files)
            basename = os.path.basename(track_path)
            print(f"Playing track {track_num}/{total_tracks}: {basename}")
            global seek_request_while_paused
            # Clear any pending seek when a new track starts
            seek_request_while_paused = None
            # Clear pending fade when a new track starts normally
            pending_fade_playlist = None
            # Clear end event if set previously (e.g., by fadeout)
            pygame.mixer.music.set_endevent()
            return True
        except pygame.error as e:
            print(f"Error loading/playing track {track_path}: {e}")
            # Attempt to play next track if current one fails?
            # For now, just stop.
            stop_music_internal()
            return False
    else:
        print("Track index out of bounds.")
        current_song_duration_sec = 0  # Reset duration
        stop_music_internal()  # Stop if index is invalid
        return False


def stop_music_internal():
    """Internal function to stop music and reset state."""
    global current_playlist_files, current_track_index, is_paused
    global current_playlist_name, current_song_duration_sec
    global seek_request_while_paused, transition_after_current_song
    global pending_fade_playlist
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()  # Free resources
        # Clear any pending end event
        pygame.mixer.music.set_endevent()
    except pygame.error as e:
        print(f"Error stopping music: {e}")
    current_playlist_files = []
    current_track_index = -1
    is_paused = False
    current_playlist_name = None
    current_song_duration_sec = 0  # Reset duration on stop
    seek_request_while_paused = None  # Clear pending seek on stop
    transition_after_current_song = None  # Clear any pending transition
    pending_fade_playlist = None  # Clear pending fade
    print("Playback stopped and state reset.")


def _load_and_play_playlist(playlist_name_to_load):
    """Helper function to load files for a playlist and play track 0."""
    global current_playlist_files, current_track_index, is_paused
    global current_playlist_name, current_song_duration_sec
    global persistent_data, pending_fade_playlist

    playlist_path = os.path.join(MUSIC_DIR, playlist_name_to_load)
    if not os.path.isdir(playlist_path):
        print(f"Error: Playlist folder not found: {playlist_name_to_load}")
        return False, f"Playlist folder not found: {playlist_name_to_load}"

    print(f"Loading playlist: {playlist_name_to_load}")
    stop_music_internal()  # Stop previous playback first

    # Get songs in the correct order
    ordered_songs = _get_ordered_songs_for_playlist(playlist_name_to_load)

    if not ordered_songs:
        msg = f"No supported audio files found in {playlist_path}"
        print(msg)
        return False, msg

    current_playlist_files = ordered_songs
    current_playlist_name = playlist_name_to_load
    track_count = len(current_playlist_files)
    print(f"Loaded {track_count} tracks for '{playlist_name_to_load}'.")
    # Clear pending fade when loading a new playlist normally
    pending_fade_playlist = None

    # Apply default volume if set
    playlist_details = persistent_data.get("playlist_details", {}).get(
        playlist_name_to_load, {}
    )
    default_volume = playlist_details.get("default_volume")
    if default_volume is not None and 0 <= default_volume <= 100:
        try:
            volume_float = default_volume / 100.0
            pygame.mixer.music.set_volume(volume_float)
            vol_msg = f"Applied default volume for '{playlist_name_to_load}'"
            print(f"{vol_msg}: {default_volume}%")
        except pygame.error as e:
            print(f"Error applying default volume {default_volume}%: {e}")
        except Exception as e:  # Catch potential non-pygame errors
            msg = f"Unexpected error applying default volume {default_volume}%"
            print(f"{msg}: {e}")

    if _play_track(0):  # Start playing the first track
        return True, f"Playing playlist {playlist_name_to_load}"
    else:
        # _play_track already called stop_music_internal on error
        return False, f"Failed to start playback for {playlist_name_to_load}"


@app.route("/")
def index():
    """Serves the main HTML page."""
    playlists = get_playlists()
    # Check environment variable
    show_debug_info = os.getenv("SHOW_DEBUG_INFO", "False").lower() == "true"
    current_track = "Track Name"  # Replace with actual track info
    current_playlist = "Playlist Name"  # Replace with actual playlist info
    volume = 50  # Replace with actual volume info

    return render_template(
        "index.html",
        playlists=playlists,
        show_debug_info=show_debug_info,
        current_track=current_track,
        current_playlist=current_playlist,
        volume=volume,
    )


@app.route("/status")
def get_status():
    """Returns the current player status using pygame."""
    global current_track_index, is_paused, current_playlist_name
    global current_playlist_files, current_song_duration_sec
    global seek_request_while_paused, persistent_data
    global FADEOUT_COMPLETE_EVENT, pending_fade_playlist
    global stop_after_current_song, transition_after_current_song
    global last_event_check_time

    # Check if we haven't processed events in too long (likely stuck)
    current_time = time.time()
    if current_time - last_event_check_time > 5:  # If > 5s without events
        print("WARNING: Event system appears stuck, reinitializing mixer")
        try:
            # Try to recover the pygame mixer
            pygame.mixer.quit()
            pygame.mixer.init()
            pygame.event.set_allowed(None)
            pygame.event.set_allowed(pygame.QUIT)
            pygame.event.set_allowed(FADEOUT_COMPLETE_EVENT)

            # If we were playing something, try to recover
            track_count = len(current_playlist_files)
            if 0 <= current_track_index < track_count:
                current_track = current_playlist_files[current_track_index]
                pygame.mixer.music.load(current_track)
                if not is_paused:
                    pygame.mixer.music.play()
        except Exception as e:
            print(f"Error during mixer recovery: {e}")

    # Update the timestamp
    last_event_check_time = current_time

    # --- Check for Custom Pygame Events (like fadeout complete) ---
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            print("QUIT event detected.")
        elif event.type == FADEOUT_COMPLETE_EVENT:
            print("Fadeout complete event received.")
            pygame.mixer.music.set_endevent()  # Clear the event trigger
            if pending_fade_playlist:
                msg = f"Loading '{pending_fade_playlist}' after fadeout."
                print(msg)
                _load_and_play_playlist(pending_fade_playlist)
            else:
                msg = "Fadeout done, but no pending playlist found. Stopping."
                print(msg)
                stop_music_internal()  # Ensure clean state

    # --- Determine Player State ---
    state = "stopped"
    current_song_filename = None
    current_position_sec = 0
    current_playlist_songs_filenames = []
    # Copy to modify safely
    all_playlist_details = persistent_data.get("playlist_details", {}).copy()

    try:
        is_busy = pygame.mixer.music.get_busy()

        if is_busy:
            if is_paused:
                state = "paused"
            else:
                state = "playing"
        elif current_track_index != -1 and not is_paused:
            # Music was playing but isn't busy now -> song finished
            if stop_after_current_song:
                msg = "Stopping after current song completes."
                print(msg)
                stop_music_internal()  # Call internal stop to reset state
                stop_after_current_song = False  # Reset the flag
            elif transition_after_current_song:
                msg = f"Transitioning to '{transition_after_current_song}'"
                print(f"{msg} after current song completes.")
                # Load the next playlist
                _load_and_play_playlist(transition_after_current_song)
                transition_after_current_song = None  # Reset flag
            else:
                # Normal song completion logic
                print("Processing normal song completion.")
                next_index = current_track_index + 1
                track_count = len(current_playlist_files)
                if 0 <= next_index < track_count:
                    if _play_track(next_index):
                        pass  # Handled by _play_track
                    else:
                        stop_music_internal()
                else:
                    print("End of playlist reached.")
                    # Handle end of playlist logic
                    details_map = persistent_data.get("playlist_details", {})
                    pl_details = details_map.get(current_playlist_name, {})
                    should_loop = pl_details.get("loop", False)
                    should_auto_adv = pl_details.get("auto_advance", False)

                    if should_loop:
                        pl_name = current_playlist_name
                        print(f"Looping playlist '{pl_name}' on completion.")
                        _play_track(0)
                    elif should_auto_adv:
                        pl_name = current_playlist_name
                        print("Auto-advancing on completion.")
                        # Re-fetch in case order changed
                        all_playlists_now = get_playlists()
                        if current_playlist_name and all_playlists_now:
                            try:
                                current_idx = all_playlists_now.index(
                                    current_playlist_name
                                )
                                next_idx = (current_idx + 1) % len(all_playlists_now)
                                next_playlist = all_playlists_now[next_idx]
                                print(f"Auto-advancing to: {next_playlist}")
                                _load_and_play_playlist(next_playlist)
                            except ValueError:
                                pl_name = current_playlist_name
                                msg = f"Cannot find '{pl_name}' in list."
                                print(f"{msg} Stopping.")
                                stop_music_internal()
                            except Exception as e:
                                print(f"Error during auto-advance: {e}")
                                stop_music_internal()
                        else:
                            msg = "Cannot auto-advance: No current playlist"
                            print(f"{msg} or no playlists found. Stopping.")
                            stop_music_internal()
                    else:
                        # Neither loop nor auto-advance is enabled
                        msg = "End of playlist, no loop/auto-advance."
                        print(f"{msg} Stopping.")
                        stop_music_internal()

        else:
            if is_paused:
                state = "paused"
            else:
                state = "stopped"
                if current_playlist_name is None and current_track_index != -1:
                    stop_music_internal()

        # Get current position if playing or paused
        if state in ("playing", "paused"):
            current_position_sec = pygame.mixer.music.get_pos() / 1000.0
            if current_position_sec < 0:
                current_position_sec = 0

        track_count = len(current_playlist_files)
        if 0 <= current_track_index < track_count:
            fname = current_playlist_files[current_track_index]
            current_song_filename = os.path.basename(fname)
            current_playlist_songs_filenames = [
                os.path.basename(f) for f in current_playlist_files
            ]

        # Add stored runtimes to the details to be sent
        all_playlists = get_playlists()  # Ensure we have the latest list
        for pl_name in all_playlists:
            if pl_name in all_playlist_details:
                details = all_playlist_details[pl_name]
                # Get stored runtime, default to 0.0 if not found
                details["runtime_minutes"] = playlist_runtimes.get(pl_name, 0.0)
            # No need for an else here

        status = {
            "state": state,
            "current_playlist": current_playlist_name,
            "current_song": current_song_filename,
            "volume": round(pygame.mixer.music.get_volume() * 100),
            "playlists": all_playlists,
            "playlist_details": all_playlist_details,
            "current_playlist_songs": current_playlist_songs_filenames,
            "current_position_sec": current_position_sec,
            "song_duration_sec": current_song_duration_sec,
        }
    except pygame.error as e:
        print(f"Pygame error in get_status: {e}")
        stop_music_internal()  # Reset state on error
        status = {
            "state": "error",
            "current_playlist": None,
            "current_song": None,
            "volume": 0,
            "playlists": get_playlists(),  # Get fresh list on error?
            "error_message": str(e),
            "playlist_details": all_playlist_details,  # Send details anyway
            "current_playlist_songs": [],
            "current_position_sec": 0,
            "song_duration_sec": 0,
        }

    return jsonify(status)


@app.route("/play", methods=["POST"])
def play_music():
    """Loads a playlist (folder) and starts playing the first track."""
    data = request.get_json()
    playlist_name_req = data.get("playlist")

    if not playlist_name_req:
        msg = "Playlist name not provided"
        return jsonify({"status": "error", "message": msg}), 400

    success, message = _load_and_play_playlist(playlist_name_req)

    if success:
        return jsonify({"status": "success", "message": message})
    else:
        # Check if the error was 404 specifically
        if "not found" in message:
            return jsonify({"status": "error", "message": message}), 404
        else:
            return jsonify({"status": "error", "message": message}), 500


@app.route("/stop", methods=["POST"])
def stop_music():
    """Stops playback and clears the queue."""
    print("Received request to stop music")
    stop_music_internal()
    return jsonify({"status": "success", "message": "Playback stopped"})


@app.route("/pause", methods=["POST"])
def pause_resume_music():
    """Pauses or resumes playback."""
    global is_paused, seek_request_while_paused, current_track_index
    global current_playlist_files, current_song_duration_sec
    print("Received request to pause/resume music")
    message = ""
    try:
        # Prioritize checking our internal state for unpausing
        if is_paused:
            # Apply pending seek BEFORE unpausing
            if seek_request_while_paused is not None:
                seek_pos = seek_request_while_paused
                seek_str = f"{seek_pos:.2f}s"
                print(f"Applying pending seek to {seek_str} before unpausing.")
                try:
                    # Ensure seeking within bounds
                    if (
                        current_song_duration_sec > 0
                        and seek_pos > current_song_duration_sec
                    ):
                        seek_request_while_paused = current_song_duration_sec - 0.1

                    # Try the safer method for seeking (reload and play)
                    track_count = len(current_playlist_files)
                    if 0 <= current_track_index < track_count:
                        current_track = current_playlist_files[current_track_index]
                        pygame.mixer.music.load(current_track)
                        pygame.mixer.music.play(0, seek_request_while_paused)
                        is_paused = False  # We've now resumed
                        seek_request_while_paused = None  # Clear pending
                        seek_str = f"{seek_pos:.2f}s"
                        message = f"Playback resumed at position {seek_str}"
                        print(message)
                        return jsonify({"status": "success", "message": message})
                except pygame.error as e:
                    print(f"Error applying pending seek: {e}")
                    seek_request_while_paused = None  # Clear pending
                except Exception as e:
                    print(f"Unexpected error during seek on unpause: {e}")
                    seek_request_while_paused = None  # Clear pending
                    # Fall through to regular unpause

            # Regular unpause if no seek pending or seek failed
            try:
                pygame.mixer.music.unpause()
                is_paused = False
                message = "Playback resumed"
                print(message)
            except pygame.error as e:
                print(f"Error unpausing: {e}")
                # If unpausing fails, try to reload/play current track
                track_count = len(current_playlist_files)
                if 0 <= current_track_index < track_count:
                    try:
                        _play_track(current_track_index)
                        message = "Playback resumed (reloaded track)"
                    except Exception as reload_e:
                        print(f"Error reloading track: {reload_e}")
                        msg = f"Failed to resume playback: {reload_e}"
                        return jsonify({"status": "error", "message": msg}), 500
                else:
                    message = "Cannot resume: no track loaded"
                    return jsonify({"status": "error", "message": message}), 400
        # Only try to pause if we are not paused and music is playing
        elif pygame.mixer.music.get_busy():
            pygame.mixer.music.pause()
            is_paused = True
            message = "Playback paused"
            print(message)
        else:
            # Wasn't paused, and wasn't busy - can't do anything
            message = "Cannot pause/resume: Music not playing or stopped."
            print(message)
        return jsonify({"status": "success", "message": message})
    except pygame.error as e:
        # Handle potential errors if pause/unpause fails unexpectedly
        message = f"Error during pause/resume: {e}"
        print(message)
        return jsonify({"status": "error", "message": message}), 500


@app.route("/next", methods=["POST"])
def next_track():
    """Skips to the next track in the current playlist."""
    global current_track_index, current_playlist_files, current_playlist_name
    global persistent_data
    print("Received request to skip track")

    if current_track_index == -1 or not current_playlist_files:
        return jsonify({"status": "error", "message": "No playlist loaded"}), 400

    next_index = current_track_index + 1
    track_count = len(current_playlist_files)
    if 0 <= next_index < track_count:
        if _play_track(next_index):
            return jsonify({"status": "success", "message": "Skipped to next track"})
        else:
            msg = "Failed to play next track"
            return jsonify({"status": "error", "message": msg}), 500
    else:
        # Last track finished, apply end-of-playlist logic
        print("Next track requested at end of playlist.")
        details_map = persistent_data.get("playlist_details", {})
        pl_details = details_map.get(current_playlist_name, {})
        should_loop = pl_details.get("loop", False)
        should_auto_adv = pl_details.get("auto_advance", False)

        if should_loop:
            pl_name = current_playlist_name
            print(f"Looping playlist '{pl_name}' on next track request.")
            if _play_track(0):
                msg = "Looped to start of playlist"
                return jsonify({"status": "success", "message": msg})
            else:
                msg = "Failed to loop playlist"
                return jsonify({"status": "error", "message": msg}), 500
        elif should_auto_adv:
            pl_name = current_playlist_name
            print(f"Auto-advancing on next track from '{pl_name}'.")
            all_playlists = get_playlists()  # Re-fetch
            if current_playlist_name and all_playlists:
                try:
                    current_idx = all_playlists.index(current_playlist_name)
                    next_idx = (current_idx + 1) % len(all_playlists)
                    next_playlist = all_playlists[next_idx]
                    print(f"Auto-advancing to: {next_playlist}")
                    # Use the refactored load/play function
                    success, message = _load_and_play_playlist(next_playlist)
                    if success:
                        msg = f"Auto-advanced to playlist: {next_playlist}"
                        return jsonify({"status": "success", "message": msg})
                    else:
                        # stop_music_internal called on failure
                        msg = f"Failed to auto-advance: {message}"
                        return jsonify({"status": "error", "message": msg}), 500
                except ValueError:
                    pl_name = current_playlist_name
                    msg = f"Cannot find '{pl_name}' in list. Stopping."
                    print(msg)
                    stop_music_internal()
                    return jsonify(
                        {"status": "success", "message": "Playlist not found, stopped"}
                    )
                except Exception as e:
                    print(f"Error during auto-advance: {e}")
                    stop_music_internal()
                    msg = f"Error during auto-advance: {e}"
                    return jsonify({"status": "error", "message": msg}), 500
            else:
                msg = "Cannot auto-advance: No current playlist or none found."
                print(f"{msg} Stopping.")
                stop_music_internal()
                return jsonify(
                    {"status": "success", "message": "Cannot auto-advance, stopped"}
                )

        # Neither loop nor auto-advance is enabled
        print("End of playlist reached, no loop/auto-advance. Stopping.")
        stop_music_internal()
        msg = "End of playlist reached, playback stopped"
        return jsonify({"status": "success", "message": msg})


@app.route("/volume", methods=["POST"])
def set_volume():
    """Sets the playback volume."""
    data = request.get_json()
    try:
        volume_percent = int(data.get("volume"))
        if 0 <= volume_percent <= 100:
            volume_float = volume_percent / 100.0
            pygame.mixer.music.set_volume(volume_float)
            print(f"Volume set to {volume_percent}%")
            msg = f"Volume set to {volume_percent}%"
            return jsonify({"status": "success", "message": msg})
        else:
            msg = "Volume must be between 0 and 100"
            return jsonify({"status": "error", "message": msg}), 400
    except (TypeError, ValueError):
        msg = "Invalid volume value provided"
        return jsonify({"status": "error", "message": msg}), 400
    except pygame.error as e:
        print(f"Error setting volume: {e}")
        return jsonify({"status": "error", "message": f"Error: {e}"}), 500


@app.route("/reorder", methods=["POST"])
def reorder_playlists():
    """Handles reordering of playlists and saves the new order."""
    global persistent_data
    data = request.get_json()
    new_order = data.get("order")

    if not isinstance(new_order, list):
        msg = "Invalid order data provided"
        return jsonify({"status": "error", "message": msg}), 400

    print(f"Received request to reorder playlists: {new_order}")

    # Basic validation: Ensure all provided names are actual playlists
    current_playlists = get_playlists()  # Gets the combined list
    if not all(pl_name in current_playlists for pl_name in new_order):
        # Compare against get_playlists result.
        print("Warning: New order contains unknown/duplicate names. Ignoring.")
        # Maybe just filter the new_order?
        valid_new_order = [pl for pl in new_order if pl in current_playlists]
        if len(valid_new_order) != len(set(valid_new_order)):
            msg = "New order contains duplicate playlist names"
            return jsonify({"status": "error", "message": msg}), 400
        if len(valid_new_order) != len(current_playlists):
            # Handle case where playlists were missing from drag?
            # Reconstruct full order: valid_new_order + missing
            missing = [pl for pl in current_playlists if pl not in valid_new_order]
            persistent_data["playlist_order"] = valid_new_order + missing
            order = persistent_data["playlist_order"]
            print(f"Reordered (with missing appended): {order}")

        else:
            persistent_data["playlist_order"] = valid_new_order
            print(f"Reordered playlists: {valid_new_order}")

    else:
        # Check for duplicates in submitted order
        if len(new_order) != len(set(new_order)):
            msg = "New order contains duplicate playlist names"
            return jsonify({"status": "error", "message": msg}), 400
        # Check if all current playlists are accounted for
        if set(new_order) != set(current_playlists):
            # Missing playlists from submitted order. Reconstruct.
            missing = [pl for pl in current_playlists if pl not in new_order]
            persistent_data["playlist_order"] = new_order + missing  # Append
            order = persistent_data["playlist_order"]
            print(f"Reordered (with missing appended): {order}")

        else:
            persistent_data["playlist_order"] = new_order
            print(f"Reordered playlists: {new_order}")

    save_persistent_data()
    new_saved_order = persistent_data["playlist_order"]
    return jsonify(
        {
            "status": "success",
            "message": "Playlist order updated",
            "new_order": new_saved_order,
        }
    )


@app.route("/reorder_songs", methods=["POST"])
def reorder_songs():
    """Handles reordering of songs within a specific playlist and saves."""
    global persistent_data, current_playlist_name, current_playlist_files
    global current_track_index
    data = request.get_json()
    playlist_name = data.get("playlist_name")
    # Expecting list of full file paths in the new order
    new_song_paths = data.get("new_song_order")

    if not playlist_name or not isinstance(new_song_paths, list):
        msg = "Missing playlist name or invalid song order data"
        return jsonify({"status": "error", "message": msg}), 400

    if playlist_name not in persistent_data.get("playlist_details", {}):
        msg = f'Playlist "{playlist_name}" not found in persistent data'
        return jsonify({"status": "error", "message": msg}), 404

    print(f"Received request to reorder songs for playlist: {playlist_name}")

    # Validate paths? Check they exist and belong to the playlist?
    # For now, trust the client sends correct full paths.
    # Basic check: Ensure no duplicates
    if len(new_song_paths) != len(set(new_song_paths)):
        msg = "New song order contains duplicates"
        return jsonify({"status": "error", "message": msg}), 400

    # Update the order
    persistent_data["playlist_details"][playlist_name]["song_order"] = new_song_paths
    save_persistent_data()

    # If this playlist is currently playing, update the live queue
    if playlist_name == current_playlist_name:
        print("Updating live playlist queue due to reordering.")
        # Find the currently playing song's *new* index
        current_song_path = None
        track_count = len(current_playlist_files)
        if 0 <= current_track_index < track_count:
            current_song_path = current_playlist_files[current_track_index]

        current_playlist_files = new_song_paths  # Update the live list

        if current_song_path and current_song_path in new_song_paths:
            try:
                new_index = new_song_paths.index(current_song_path)
                current_track_index = new_index
                print(f"Current track is now at index {current_track_index}")
            except ValueError:
                # Should not happen if validation is okay
                print("Warn: Current song not found in new order. Resetting.")
                # Reset to 0 for now.
                if len(current_playlist_files) > 0:
                    _play_track(0)
                else:
                    stop_music_internal()

        elif len(current_playlist_files) > 0:
            # If no song was playing or current song removed, restart playlist?
            print("Restarting playlist from index 0 after reorder.")
            _play_track(0)
        else:
            # Playlist is now empty after reorder?
            print("Playlist is empty after reorder. Stopping.")
            stop_music_internal()

    msg = f"Song order updated for {playlist_name}"
    return jsonify({"status": "success", "message": msg})


@app.route("/set_playlist_volume", methods=["POST"])
def set_playlist_volume():
    """Sets and saves the default volume for a specific playlist."""
    global persistent_data, current_playlist_name
    data = request.get_json()
    playlist_name = data.get("playlist_name")
    volume = data.get("volume")  # Expecting 0-100 or null

    if not playlist_name:  # Volume can be None to unset it
        msg = "Missing playlist name"
        return jsonify({"status": "error", "message": msg}), 400

    volume_int = None
    if volume is not None:
        try:
            volume_int = int(volume)
            if not (0 <= volume_int <= 100):
                raise ValueError("Volume out of range")
        except (TypeError, ValueError):
            msg = "Invalid volume value (must be 0-100)"
            return jsonify({"status": "error", "message": msg}), 400

    # Ensure the playlist exists in our details
    details_map = persistent_data.get("playlist_details", {})
    if playlist_name not in details_map:
        # If playlist exists on disk but not in details yet, try adding it.
        if playlist_name in get_playlists():  # get_playlists populates
            # Check again after get_playlists possibly added it
            if playlist_name not in persistent_data["playlist_details"]:
                # This case should be rare
                msg = f"Error: Playlist '{playlist_name}' found but details not populated."
                print(msg)
                err_msg = f"Internal error populating details for {playlist_name}"
                return jsonify({"status": "error", "message": err_msg}), 500
        else:
            msg = f'Playlist "{playlist_name}" not found'
            return jsonify({"status": "error", "message": msg}), 404

    # Now sure playlist_name key exists in persistent_data["playlist_details"]
    print(
        f"Setting default volume for '{playlist_name}' to {volume_int if volume_int is not None else 'unset'}"
    )
    persistent_data["playlist_details"][playlist_name]["default_volume"] = volume_int
    # Ensure other keys exist if added implicitly via get_playlists
    details = persistent_data["playlist_details"][playlist_name]
    details.setdefault("song_order", [])
    details.setdefault("auto_advance", False)
    save_persistent_data()

    # Apply volume immediately if this playlist is currently playing?
    if playlist_name == current_playlist_name and volume_int is not None:
        try:
            volume_float = volume_int / 100.0
            pygame.mixer.music.set_volume(volume_float)
            print(f"Applied volume {volume_int}% to current playback.")
        except pygame.error as e:
            print(f"Error applying volume {volume_int}% immediately: {e}")

    msg = f"Default volume for {playlist_name} set to {volume_int if volume_int is not None else 'unset'}"
    return jsonify({"status": "success", "message": msg})


@app.route("/set_playlist_auto_advance", methods=["POST"])
def set_playlist_auto_advance():
    """Sets and saves the auto-advance setting for a specific playlist."""
    global persistent_data
    data = request.get_json()
    playlist_name = data.get("playlist_name")
    enabled = data.get("enabled")

    if not playlist_name or enabled is None or not isinstance(enabled, bool):
        msg = "Missing playlist name or invalid enabled value"
        return jsonify({"status": "error", "message": msg}), 400

    # Ensure the playlist exists in our details
    details_map = persistent_data.get("playlist_details", {})
    if playlist_name not in details_map:
        # If playlist exists on disk but not in details yet, try adding it.
        if playlist_name in get_playlists():  # get_playlists populates
            # Check again after get_playlists possibly added it
            if playlist_name not in persistent_data["playlist_details"]:
                # This case should be rare
                msg = f"Error: Playlist '{playlist_name}' found but details not populated."
                print(msg)
                err_msg = f"Internal error populating details for {playlist_name}"
                return jsonify({"status": "error", "message": err_msg}), 500
        else:
            msg = f'Playlist "{playlist_name}" not found'
            return jsonify({"status": "error", "message": msg}), 404

    # Now sure playlist_name key exists in persistent_data["playlist_details"]
    print(f"Setting auto-advance for playlist '{playlist_name}' to {enabled}")
    persistent_data["playlist_details"][playlist_name]["auto_advance"] = enabled
    # Ensure other keys exist if added implicitly via get_playlists
    details = persistent_data["playlist_details"][playlist_name]
    details.setdefault("song_order", [])
    details.setdefault("default_volume", None)
    details.setdefault("loop", False)
    details.setdefault("transition_mode", "fade")
    save_persistent_data()

    msg = f"Auto-advance for {playlist_name} set to {enabled}"
    # Keep response key for potential frontend use
    return jsonify(
        {"status": "success", "message": msg, "auto_advance_enabled": enabled}
    )


@app.route("/set_playlist_loop", methods=["POST"])
def set_playlist_loop():
    """Sets and saves the loop setting for a specific playlist."""
    global persistent_data
    data = request.get_json()
    playlist_name = data.get("playlist_name")
    enabled = data.get("enabled")

    if not playlist_name or enabled is None or not isinstance(enabled, bool):
        msg = "Missing playlist name or invalid enabled value"
        return jsonify({"status": "error", "message": msg}), 400

    # Ensure the playlist exists in our details
    details_map = persistent_data.get("playlist_details", {})
    if playlist_name not in details_map:
        if playlist_name in get_playlists():  # Try to add it
            if playlist_name not in persistent_data["playlist_details"]:
                msg = f"Error: Playlist '{playlist_name}' found but details not populated."
                print(msg)
                err_msg = f"Internal error populating details for {playlist_name}"
                return jsonify({"status": "error", "message": err_msg}), 500
        else:
            msg = f'Playlist "{playlist_name}" not found'
            return jsonify({"status": "error", "message": msg}), 404

    # Update the loop setting
    print(f"Setting loop for playlist '{playlist_name}' to {enabled}")
    persistent_data["playlist_details"][playlist_name]["loop"] = enabled
    # Ensure other keys exist
    details = persistent_data["playlist_details"][playlist_name]
    details.setdefault("song_order", [])
    details.setdefault("default_volume", None)
    details.setdefault("auto_advance", False)
    details.setdefault("transition_mode", "fade")
    save_persistent_data()

    msg = f"Loop for {playlist_name} set to {enabled}"
    return jsonify({"status": "success", "message": msg, "loop_enabled": enabled})


@app.route("/set_playlist_transition_mode", methods=["POST"])
def set_playlist_transition_mode():
    """Sets and saves the transition mode for a specific playlist."""
    global persistent_data
    data = request.get_json()
    playlist_name = data.get("playlist_name")
    mode = data.get("mode")

    if not playlist_name or not mode:
        msg = "Missing playlist name or transition mode"
        return jsonify({"status": "error", "message": msg}), 400

    # Validate transition mode
    if mode not in TRANSITION_MODES:
        valid_modes = ", ".join(TRANSITION_MODES)
        msg = f"Invalid transition mode. Must be one of: {valid_modes}"
        return jsonify({"status": "error", "message": msg}), 400

    # Ensure the playlist exists in our details
    details_map = persistent_data.get("playlist_details", {})
    if playlist_name not in details_map:
        if playlist_name in get_playlists():  # Try to add it
            if playlist_name not in persistent_data["playlist_details"]:
                msg = f"Error: Playlist '{playlist_name}' found but details not populated."
                print(msg)
                err_msg = f"Internal error populating details for {playlist_name}"
                return jsonify({"status": "error", "message": err_msg}), 500
        else:
            msg = f'Playlist "{playlist_name}" not found'
            return jsonify({"status": "error", "message": msg}), 404

    # Update the transition mode
    print(f"Setting transition mode for playlist '{playlist_name}' to '{mode}'")
    persistent_data["playlist_details"][playlist_name]["transition_mode"] = mode
    # Ensure other keys exist
    details = persistent_data["playlist_details"][playlist_name]
    details.setdefault("song_order", [])
    details.setdefault("default_volume", None)
    details.setdefault("auto_advance", False)
    details.setdefault("loop", False)
    save_persistent_data()

    msg = f"Transition mode for {playlist_name} set to {mode}"
    return jsonify({"status": "success", "message": msg, "transition_mode": mode})


@app.route("/next_playlist", methods=["POST"])
def next_playlist():
    """Triggers transition to next playlist based on current's mode."""
    global persistent_data, current_playlist_name, FADEOUT_COMPLETE_EVENT
    global pending_fade_playlist, transition_after_current_song
    global stop_after_current_song

    if not current_playlist_name:
        msg = "No playlist is currently active"
        return jsonify({"status": "error", "message": msg}), 400

    # Get the current playlist's details
    details_map = persistent_data.get("playlist_details", {})
    playlist_details = details_map.get(current_playlist_name, {})
    transition_mode = playlist_details.get("transition_mode", "fade")

    # Get the next playlist
    all_playlists = get_playlists()
    if not all_playlists:
        msg = "No playlists available"
        return jsonify({"status": "error", "message": msg}), 400

    try:
        current_idx = all_playlists.index(current_playlist_name)
        next_idx = (current_idx + 1) % len(all_playlists)
        next_playlist_name = all_playlists[next_idx]
    except ValueError:
        msg = "Current playlist not found in playlist list"
        return jsonify({"status": "error", "message": msg}), 500

    # Handle transition based on mode
    if transition_mode == "fade":
        # Initiate fadeout, store pending playlist, set end event
        can_fade = (
            pygame.mixer.music.get_busy()
            and not pending_fade_playlist
            and not transition_after_current_song
        )
        if can_fade:
            msg = f"Initiating fadeout (5s) to transition to '{next_playlist_name}'..."
            print(msg)
            pending_fade_playlist = next_playlist_name
            pygame.mixer.music.set_endevent(FADEOUT_COMPLETE_EVENT)
            pygame.mixer.music.fadeout(5000)  # 5000 ms = 5 seconds
            return jsonify(
                {
                    "status": "success",
                    "message": f"Fading out to {next_playlist_name}",
                    "next_playlist": next_playlist_name,
                }
            )
        elif pending_fade_playlist:
            msg = f"Already fading out to {pending_fade_playlist}. Wait."
            return jsonify({"status": "error", "message": msg}), 400
        elif transition_after_current_song:
            msg = f"Waiting for song for {transition_after_current_song}."
            return jsonify({"status": "error", "message": msg}), 400
        else:
            # Not playing, just switch immediately
            msg = f"Music not playing, switching directly to '{next_playlist_name}'"
            print(f"{msg} (fade mode).")
            success, message = _load_and_play_playlist(next_playlist_name)
            if success:
                return jsonify(
                    {
                        "status": "success",
                        "message": f"Transitioned to {next_playlist_name}",
                        "next_playlist": next_playlist_name,
                    }
                )
            else:
                msg = f"Failed to transition: {message}"
                return jsonify({"status": "error", "message": msg}), 500

    elif transition_mode == "complete":
        # Mark that we should transition after the current song
        # Prevent setting if already fading or set
        if pending_fade_playlist:
            msg = f'Cannot set "complete" while fading to {pending_fade_playlist}.'
            return jsonify({"status": "error", "message": msg}), 400
        if transition_after_current_song:
            msg = f"Already waiting for {transition_after_current_song}."
            return jsonify({"status": "error", "message": msg}), 400

        msg = f"Setting transition to '{next_playlist_name}' after current song."
        print(msg)
        transition_after_current_song = next_playlist_name
        # Clear any previously set end event just in case
        pygame.mixer.music.set_endevent()
        return jsonify(
            {
                "status": "success",
                "message": f"Will transition to {next_playlist_name}",
                "next_playlist": next_playlist_name,
            }
        )

    elif transition_mode == "stop":
        # Set flag to stop after the current song finishes
        print("Setting to stop after current song completes (mode 'stop').")
        transition_after_current_song = None  # Clear any previous transitions
        pending_fade_playlist = None  # Clear any pending fade transitions
        stop_after_current_song = True  # Set the flag
        pygame.mixer.music.set_endevent()  # Clear any end events
        msg = "Will stop playback after current song completes"
        return jsonify({"status": "success", "message": msg})

    else:
        msg = f"Unknown transition mode: {transition_mode}"
        return jsonify({"status": "error", "message": msg}), 500


@app.route("/seek", methods=["POST"])
def seek_music():
    """Seeks to a specific position in the current track."""
    global current_track_index, is_paused, seek_request_while_paused
    global current_playlist_files, current_song_duration_sec
    global last_event_check_time

    if current_track_index == -1:
        return jsonify({"status": "error", "message": "No track loaded"}), 400

    data = request.get_json()
    try:
        seek_time_sec = float(data.get("position"))
    except (TypeError, ValueError):
        msg = "Invalid seek position provided"
        return jsonify({"status": "error", "message": msg}), 400

    if seek_time_sec < 0:
        seek_time_sec = 0

    # Ensure seeking within bounds
    if current_song_duration_sec > 0 and seek_time_sec > current_song_duration_sec:
        seek_time_sec = current_song_duration_sec - 0.1  # Seek near end

    # If paused, store the request instead of seeking immediately
    if is_paused:
        seek_request_while_paused = seek_time_sec
        seek_str = f"{seek_time_sec:.2f}"
        print(f"Playback paused. Storing seek request to {seek_str} seconds.")
        # Return success, the seek will happen on unpause
        msg = f"Seek to {seek_str}s stored (will apply on resume)"
        return jsonify({"status": "success", "message": msg})

    # The only reliable solution seems to be reinitializing pygame mixer
    try:
        seek_str = f"{seek_time_sec:.2f}"
        print(f"Seeking to {seek_str} seconds")

        track_count = len(current_playlist_files)
        if 0 <= current_track_index < track_count:
            # Get the current track path
            current_track = current_playlist_files[current_track_index]

            # Save current volume
            try:
                current_volume = pygame.mixer.music.get_volume()
            except pygame.error:
                current_volume = 1.0

            # IMPORTANT: Complete reinitialization sequence
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
            pygame.mixer.quit()
            pygame.mixer.init()
            pygame.event.set_allowed(None)
            pygame.event.set_allowed(pygame.QUIT)
            pygame.event.set_allowed(FADEOUT_COMPLETE_EVENT)

            # Now load and play from position
            pygame.mixer.music.load(current_track)
            pygame.mixer.music.play(0, seek_time_sec)
            pygame.mixer.music.set_volume(current_volume)

            seek_request_while_paused = None  # Clear any old pending seek
            last_event_check_time = time.time()  # Reset event check time
            msg = f"Seeked to {seek_str}s"
            return jsonify({"status": "success", "message": msg})
        else:
            msg = "Current track index is invalid"
            return jsonify({"status": "error", "message": msg}), 400

    except Exception as e:
        print(f"Error during seek operation: {e}")
        # If seeking fails, completely reset pygame
        try:
            print("Attempting complete pygame reset after seek failure")
            pygame.mixer.quit()
            pygame.mixer.init()
            pygame.event.set_allowed(None)
            pygame.event.set_allowed(pygame.QUIT)
            pygame.event.set_allowed(FADEOUT_COMPLETE_EVENT)

            # Try to restart playback
            track_count = len(current_playlist_files)
            if 0 <= current_track_index < track_count:
                pygame.mixer.music.load(current_playlist_files[current_track_index])
                pygame.mixer.music.play()
                msg = f"Seeking failed, restarted track from beginning: {e}"
                return jsonify({"status": "error", "message": msg}), 400
            else:
                stop_music_internal()
                msg = "Seeking failed, playback stopped"
                return jsonify({"status": "error", "message": msg}), 500
        except Exception as recovery_e:
            print(f"Complete recovery failed: {recovery_e}")
            stop_music_internal()
            msg = "Seeking and recovery failed. Playback stopped."
            return jsonify({"status": "error", "message": msg}), 500


@app.route("/rename_playlist", methods=["POST"])
def rename_playlist():
    """Renames a playlist folder and updates all references."""
    global persistent_data, current_playlist_name
    data = request.get_json()
    old_name = data.get("old_name")
    new_name = data.get("new_name")

    if not old_name or not new_name:
        msg = "Missing old or new playlist name"
        return jsonify({"status": "error", "message": msg}), 400

    # Validate new name (no slashes, not empty, etc.)
    if "/" in new_name or "\\" in new_name or new_name.strip() == "":
        msg = "Invalid new playlist name"
        return jsonify({"status": "error", "message": msg}), 400

    # Check if source exists
    old_path = os.path.join(MUSIC_DIR, old_name)
    if not os.path.isdir(old_path):
        msg = f"Playlist folder not found: {old_name}"
        return jsonify({"status": "error", "message": msg}), 404

    # Check if destination already exists
    new_path = os.path.join(MUSIC_DIR, new_name)
    if os.path.exists(new_path):
        msg = f"A playlist named {new_name} already exists"
        return jsonify({"status": "error", "message": msg}), 400

    try:
        # Rename the folder
        os.rename(old_path, new_path)
        print(f"Renamed playlist folder from '{old_name}' to '{new_name}'")

        # Update playlist order
        if old_name in persistent_data.get("playlist_order", []):
            playlist_order = persistent_data["playlist_order"]
            index = playlist_order.index(old_name)
            playlist_order[index] = new_name

        # Update playlist details
        if old_name in persistent_data.get("playlist_details", {}):
            details = persistent_data["playlist_details"].pop(old_name)
            persistent_data["playlist_details"][new_name] = details

            # Update song paths in the details
            if "song_order" in details:
                updated_paths = []
                for path in details["song_order"]:
                    if path.startswith(old_path):
                        # Replace old path with new path
                        new_song_path = path.replace(old_path, new_path, 1)
                        updated_paths.append(new_song_path)
                    else:
                        updated_paths.append(path)
                details["song_order"] = updated_paths

        # Update current playlist name if it was renamed
        if current_playlist_name == old_name:
            current_playlist_name = new_name
            # Update current playlist files paths
            global current_playlist_files
            current_playlist_files = [
                path.replace(old_path, new_path, 1)
                if path.startswith(old_path)
                else path
                for path in current_playlist_files
            ]

        # Save changes
        save_persistent_data()

        return jsonify(
            {
                "status": "success",
                "message": f"Renamed playlist from {old_name} to {new_name}",
                "new_name": new_name,
            }
        )

    except OSError as e:
        msg = f"Error renaming playlist: {str(e)}"
        return jsonify({"status": "error", "message": msg}), 500


@app.route("/rename_song", methods=["POST"])
def rename_song():
    """Renames a song file and updates all references."""
    global persistent_data, current_playlist_files, current_track_index
    global playlist_runtimes  # Add playlist_runtimes
    data = request.get_json()
    playlist_name = data.get("playlist_name")
    old_name = data.get("old_name")  # Can be basename or full path
    new_name = data.get("new_name")  # Should be just the new basename

    if not playlist_name or not old_name or not new_name:
        msg = "Missing required parameters"
        return jsonify({"status": "error", "message": msg}), 400

    # Validate new name (no slashes, not empty, etc.)
    if "/" in new_name or "\\" in new_name or new_name.strip() == "":
        msg = "Invalid new song name"
        return jsonify({"status": "error", "message": msg}), 400

    # Determine if old_name is a basename or full path
    if os.path.sep in old_name:
        # It's a full path
        old_path = old_name
        old_basename = os.path.basename(old_name)
    else:
        # It's just a basename
        old_basename = old_name
        old_path = os.path.join(MUSIC_DIR, playlist_name, old_basename)

    # Construct the new path
    new_path = os.path.join(MUSIC_DIR, playlist_name, new_name)

    # Check if source exists
    if not os.path.isfile(old_path):
        msg = f"Song file not found: {old_basename}"
        return jsonify({"status": "error", "message": msg}), 404

    # Check if destination already exists
    if os.path.exists(new_path):
        msg = f"A song named {new_name} already exists in this playlist"
        return jsonify({"status": "error", "message": msg}), 400

    try:
        # Rename the file
        os.rename(old_path, new_path)
        msg = f"Renamed song from '{old_basename}' to '{new_name}'"
        print(f"{msg} in playlist '{playlist_name}'")

        # Update song paths in playlist details
        if playlist_name in persistent_data.get("playlist_details", {}):
            details = persistent_data["playlist_details"][playlist_name]
            if "song_order" in details:
                # Find and update the path in song_order
                for i, path in enumerate(details["song_order"]):
                    basename_match = os.path.basename(path) == old_basename
                    if path == old_path or basename_match:
                        details["song_order"][i] = new_path
                        break

        # Update current playlist files if this playlist is active
        if playlist_name == current_playlist_name:
            for i, path in enumerate(current_playlist_files):
                basename_match = os.path.basename(path) == old_basename
                if path == old_path or basename_match:
                    current_playlist_files[i] = new_path
                    break

        # Save changes
        save_persistent_data()

        # Recalculate runtime for the affected playlist after rename
        details = persistent_data["playlist_details"][playlist_name]
        new_runtime = calculate_playlist_runtime(playlist_name, details)
        playlist_runtimes[playlist_name] = new_runtime
        runtime_str = f"{playlist_runtimes[playlist_name]}"
        msg = f"Recalculated runtime for '{playlist_name}' after song rename"
        print(f"{msg}: {runtime_str} min")

        return jsonify(
            {
                "status": "success",
                "message": f"Renamed song from {old_basename} to {new_name}",
                "new_name": new_name,
            }
        )

    except OSError as e:
        msg = f"Error renaming song: {str(e)}"
        return jsonify({"status": "error", "message": msg}), 500


if __name__ == "__main__":
    # Use 0.0.0.0 to make it accessible on your network
    # Turn off debug mode for production use or if causing issues
    calculate_and_store_all_runtimes()  # Calculate runtimes once on startup
    app.run(debug=False, host="0.0.0.0", port=5522)

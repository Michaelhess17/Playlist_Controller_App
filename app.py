from flask import Flask, render_template, jsonify, request
import os
import pygame
import glob # Added glob
import json # Added for persistent data
import time # Added for potential future fade-in timing
import logging
import subprocess # Add subprocess import

logging.basicConfig(level=logging.WARNING)  # Set to WARNING to suppress debug/info messages

app = Flask(__name__)

# Configure music directory (replace with your actual path)
MUSIC_DIR = "/home/michael/Documents/Wedding/Music/Normalized" # Updated to normalized
SUPPORTED_FORMATS = ["*.mp3", "*.ogg", "*.wav"] # Add more if needed
PERSISTENCE_FILE = "playlist_data.json" # Added for persistence

# Initialize Pygame
try:
    pygame.init()
    pygame.mixer.init()
    pygame.event.set_allowed(None) # Disable default event queueing
    pygame.event.set_allowed(pygame.QUIT) # Allow quit event
    FADEOUT_COMPLETE_EVENT = pygame.USEREVENT + 1 # Custom event for fadeout
    pygame.event.set_allowed(FADEOUT_COMPLETE_EVENT) # Allow our custom event
    print("Pygame mixer initialized successfully.")
except pygame.error as e:
    print(f"Error initializing pygame: {e}")
    # Exit or handle appropriately if pygame fails
    exit()

# --- Load Persistent Data ---
persistent_data = {
    "playlist_order": [],
    "playlist_details": {} # Details now store {"song_order": [], "default_volume": None, "auto_advance": False, "loop": False, "transition_mode": "fade"}
}

# Transition modes
TRANSITION_MODES = ["fade", "complete", "stop"] # fade = immediate transition, complete = finish current song, stop = stop playback

def load_persistent_data():
    """Loads playlist order, song orders, volumes, and auto-advance settings from JSON."""
    global persistent_data
    if os.path.exists(PERSISTENCE_FILE):
        try:
            with open(PERSISTENCE_FILE, 'r') as f:
                data = json.load(f)
                # Basic validation
                if isinstance(data.get("playlist_order"), list) and isinstance(data.get("playlist_details"), dict):
                     persistent_data = data
                     # Ensure new keys have defaults in loaded data
                     for pl_name, details in persistent_data.get("playlist_details", {}).items():
                         if "default_volume" not in details:
                             details["default_volume"] = None
                         if "auto_advance" not in details:
                             details["auto_advance"] = False # Default to false if missing
                         if "loop" not in details:
                             details["loop"] = False # Default to false if missing
                         if "transition_mode" not in details:
                             details["transition_mode"] = "fade" # Default to fade if missing
                     # print(f"Loaded persistent data from {PERSISTENCE_FILE}")  # Comment this out
                else:
                    print(f"Warning: Invalid format in {PERSISTENCE_FILE}. Using defaults.")
                    save_persistent_data() # Save default structure if file is corrupt
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading {PERSISTENCE_FILE}: {e}. Using defaults.")
            save_persistent_data() # Save default structure if file is corrupt
    else:
        print(f"{PERSISTENCE_FILE} not found. Will create with default structure.")
        # Initial scan to populate if file doesn't exist? Or wait for user interaction?
        # Let's wait, it will be populated as playlists are interacted with.

def save_persistent_data():
    """Saves the current playlist order, song orders, and volumes to JSON."""
    global persistent_data
    try:
        with open(PERSISTENCE_FILE, 'w') as f:
            json.dump(persistent_data, f, indent=2)
        # print(f"Saved persistent data to {PERSISTENCE_FILE}") # Can be noisy
    except IOError as e:
        print(f"Error saving persistent data to {PERSISTENCE_FILE}: {e}")

# Load data on startup
load_persistent_data()
# --- End Persistent Data ---

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
playlist_runtimes = {} # Store calculated runtimes {playlist_name: runtime_minutes}
# ---------------------------

# Add this global variable at the top with your other globals:
last_event_check_time = time.time()

def get_playlists():
    """Returns a list of directories (playlists) respecting the saved order."""
    global persistent_data
    actual_playlists = set()
    if not os.path.isdir(MUSIC_DIR):
        print(f"Warning: Music directory not found: {MUSIC_DIR}")
        return []
    try:
        for item in os.listdir(MUSIC_DIR):
            if os.path.isdir(os.path.join(MUSIC_DIR, item)):
                actual_playlists.add(item)
    except Exception as e:
        print(f"Error reading music directory: {e}")
        return []

    # Get playlists in saved order, filtering out any that no longer exist
    ordered_playlists = [pl for pl in persistent_data.get("playlist_order", []) if pl in actual_playlists]

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
                     "transition_mode": "fade"
                 }
        save_persistent_data() # Save the updated order immediately

    # Ensure details dict is consistent (add missing playlists with defaults)
    current_details = persistent_data.get("playlist_details", {})
    updated_details = {}
    for pl in final_playlist_list:
        details = current_details.get(pl, {
            "song_order": [],
            "default_volume": None,
            "auto_advance": False,
            "loop": False,
            "transition_mode": "fade"
        })
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
        args = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filename]
        popen = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = popen.communicate()
        if popen.returncode == 0 and stdout:
             return float(stdout.strip())
        else:
            # Log ffprobe errors if any
            error_output = stderr.decode('utf-8').strip()
            print(f"Error getting duration for {os.path.basename(filename)} with ffprobe. Return code: {popen.returncode}. Error: {error_output}")
            # Fallback to pygame if ffprobe fails?
            try:
                sound = pygame.mixer.Sound(filename)
                duration = sound.get_length()
                del sound
                print(f"Using pygame duration fallback for {os.path.basename(filename)}: {duration:.2f}s")
                return duration
            except Exception as pygame_e:
                print(f"Pygame fallback failed for {os.path.basename(filename)}: {pygame_e}")
                return 0 # Return 0 if duration cannot be determined
    except FileNotFoundError:
        print("Error: ffprobe command not found. Please ensure ffprobe is installed and in your PATH.")
        return 0 # Return 0 if ffprobe isn't available
    except Exception as e:
        print(f"Unexpected error getting duration for {os.path.basename(filename)}: {e}")
        return 0 # Return 0 on unexpected errors


def calculate_playlist_runtime(playlist_name, playlist_details):
    """Calculates the total runtime of a playlist in minutes."""
    songs = playlist_details.get("song_order", [])
    if not songs:
        return 0.0
    total_runtime_sec = 0
    print(f"Calculating runtime for '{playlist_name}'...") # Debug print
    for song_path in songs:
        duration = get_song_duration(song_path)
        # print(f"  - {os.path.basename(song_path)}: {duration:.2f}s") # Debug print per song
        total_runtime_sec += duration
    runtime_min = round(total_runtime_sec / 60, 1)
    print(f"Total runtime for '{playlist_name}': {runtime_min} minutes") # Debug print
    return runtime_min

# --- Function to Calculate All Runtimes Once ---
def calculate_and_store_all_runtimes():
    """Calculates runtimes for all playlists and stores them globally."""
    global playlist_runtimes, persistent_data
    print("Calculating initial runtimes for all playlists...")
    all_playlists = get_playlists() # Make sure we have the list
    all_details = persistent_data.get("playlist_details", {})
    new_runtimes = {}
    for pl_name in all_playlists:
        details = all_details.get(pl_name)
        if details: # Check if details exist for the playlist
            runtime = calculate_playlist_runtime(pl_name, details)
            new_runtimes[pl_name] = runtime
        else:
             print(f"Warning: No details found for playlist '{pl_name}' during initial runtime calculation.")
             new_runtimes[pl_name] = 0.0 # Store 0 if no details
    playlist_runtimes = new_runtimes
    print("Finished calculating initial runtimes.")

# --- End Runtime Calculation Helpers ---

def _get_ordered_songs_for_playlist(playlist_name):
    """Gets the list of song file paths for a playlist, respecting saved order."""
    global persistent_data
    playlist_path = os.path.join(MUSIC_DIR, playlist_name)
    if not os.path.isdir(playlist_path):
        return [] # Playlist folder doesn't exist

    # 1. Find all actual supported files in the directory
    actual_files = set()
    for fmt in SUPPORTED_FORMATS:
        try:
            # Use absolute paths for consistency
            actual_files.update(glob.glob(os.path.join(os.path.abspath(playlist_path), fmt), recursive=False))
        except Exception as e:
             print(f"Error globbing for {fmt} in {playlist_path}: {e}")

    # 2. Get the saved order for this playlist
    playlist_details = persistent_data.get("playlist_details", {}).get(playlist_name, {"song_order": [], "default_volume": None, "auto_advance": False}) # Added default
    saved_song_order = playlist_details.get("song_order", [])

    # 3. Create the ordered list, filtering out saved songs that no longer exist
    ordered_songs = [song for song in saved_song_order if song in actual_files]

    # 4. Find songs that are in the directory but not in the saved order
    new_songs = sorted(list(actual_files - set(ordered_songs)))

    # 5. Combine and update persistent data if the order needs changing
    final_song_list = ordered_songs + new_songs
    if new_songs or len(final_song_list) != len(saved_song_order):
        print(f"Updating song order for playlist '{playlist_name}'.")
        # Ensure details entry exists and has all keys
        if playlist_name not in persistent_data.get("playlist_details", {}):
             persistent_data["playlist_details"][playlist_name] = {"song_order": [], "default_volume": None, "auto_advance": False}
        else: # Ensure existing entry has new keys
            persistent_data["playlist_details"][playlist_name].setdefault("default_volume", None)
            persistent_data["playlist_details"][playlist_name].setdefault("auto_advance", False)

        persistent_data["playlist_details"][playlist_name]["song_order"] = final_song_list
        save_persistent_data()

    return final_song_list

def _play_track(index):
    """Helper function to load and play a specific track index."""
    global current_track_index, is_paused, current_song_duration_sec, current_playlist_files, pending_fade_playlist
    if 0 <= index < len(current_playlist_files):
        track_path = current_playlist_files[index]
        try:
            print(f"Loading track: {track_path}")
            # Get duration first using Sound object (might be slightly inaccurate/slow)
            try:
                sound = pygame.mixer.Sound(track_path)
                current_song_duration_sec = sound.get_length()
                print(f"Track duration: {current_song_duration_sec:.2f} seconds")
                del sound # Free up memory
            except Exception as e:
                print(f"Warning: Could not determine track duration for {track_path}: {e}")
                current_song_duration_sec = 0 # Indicate unknown duration

            pygame.mixer.music.load(track_path)
            pygame.mixer.music.play()
            current_track_index = index
            is_paused = False
            print(f"Playing track {index + 1}/{len(current_playlist_files)}: {os.path.basename(track_path)}")
            global seek_request_while_paused
            seek_request_while_paused = None # Clear any pending seek when a new track starts
            pending_fade_playlist = None # Clear pending fade when a new track starts normally
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
        current_song_duration_sec = 0 # Reset duration
        stop_music_internal() # Stop if index is invalid
        return False

def stop_music_internal():
    """Internal function to stop music and reset state."""
    global current_playlist_files, current_track_index, is_paused, current_playlist_name, current_song_duration_sec, seek_request_while_paused, transition_after_current_song, pending_fade_playlist
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.unload() # Free resources
        # Clear any pending end event
        pygame.mixer.music.set_endevent()
    except pygame.error as e:
        print(f"Error stopping music: {e}")
    current_playlist_files = []
    current_track_index = -1
    is_paused = False
    current_playlist_name = None
    current_song_duration_sec = 0 # Reset duration on stop
    seek_request_while_paused = None # Clear pending seek on stop
    transition_after_current_song = None # Clear any pending transition
    pending_fade_playlist = None # Clear pending fade
    print("Playback stopped and state reset.")

def _load_and_play_playlist(playlist_name_to_load):
    """Helper function to load files for a playlist and start playing track 0."""
    global current_playlist_files, current_track_index, is_paused, current_playlist_name, current_song_duration_sec, persistent_data, pending_fade_playlist

    playlist_path = os.path.join(MUSIC_DIR, playlist_name_to_load)
    if not os.path.isdir(playlist_path):
        print(f"Error: Playlist folder not found: {playlist_name_to_load}")
        return False, f"Playlist folder not found: {playlist_name_to_load}"

    print(f"Loading playlist: {playlist_name_to_load}")
    stop_music_internal() # Stop previous playback first

    # Get songs in the correct order
    ordered_songs = _get_ordered_songs_for_playlist(playlist_name_to_load)

    if not ordered_songs:
        msg = f"No supported audio files found or accessible in {playlist_path}"
        print(msg)
        return False, msg

    current_playlist_files = ordered_songs
    current_playlist_name = playlist_name_to_load
    print(f"Loaded {len(current_playlist_files)} tracks for '{playlist_name_to_load}'.")
    pending_fade_playlist = None # Clear pending fade when loading a new playlist normally

    # Apply default volume if set
    playlist_details = persistent_data.get("playlist_details", {}).get(playlist_name_to_load, {})
    default_volume = playlist_details.get("default_volume")
    if default_volume is not None and 0 <= default_volume <= 100:
        try:
            volume_float = default_volume / 100.0
            pygame.mixer.music.set_volume(volume_float)
            print(f"Applied default volume for '{playlist_name_to_load}': {default_volume}%")
        except pygame.error as e:
            print(f"Error applying default volume {default_volume}%: {e}")
        except Exception as e: # Catch potential non-pygame errors
             print(f"Unexpected error applying default volume {default_volume}%: {e}")

    if _play_track(0): # Start playing the first track
        return True, f"Playing playlist {playlist_name_to_load}"
    else:
        # _play_track already called stop_music_internal on error
        return False, f"Failed to start playback for {playlist_name_to_load}"

@app.route('/')
def index():
    """Serves the main HTML page."""
    playlists = get_playlists()
    show_debug_info = os.getenv('SHOW_DEBUG_INFO', 'False').lower() == 'true'  # Check environment variable
    current_track = "Track Name"  # Replace with actual track info
    current_playlist = "Playlist Name"  # Replace with actual playlist info
    volume = 50  # Replace with actual volume info

    return render_template('index.html', playlists=playlists, show_debug_info=show_debug_info,
                           current_track=current_track, current_playlist=current_playlist, volume=volume)

@app.route('/status')
def get_status():
    """Returns the current player status using pygame."""
    global current_track_index, is_paused, current_playlist_name, current_playlist_files, current_song_duration_sec, seek_request_while_paused, persistent_data, FADEOUT_COMPLETE_EVENT, pending_fade_playlist, stop_after_current_song, transition_after_current_song, last_event_check_time

    # Check if we haven't processed events in too long (likely stuck)
    current_time = time.time()
    if current_time - last_event_check_time > 5:  # If more than 5 seconds without events
        print("WARNING: Event system appears to be stuck, reinitializing pygame mixer")
        try:
            # Try to recover the pygame mixer
            pygame.mixer.quit()
            pygame.mixer.init()
            pygame.event.set_allowed(None)
            pygame.event.set_allowed(pygame.QUIT)
            pygame.event.set_allowed(FADEOUT_COMPLETE_EVENT)
            
            # If we were playing something, try to recover
            if current_track_index >= 0 and current_track_index < len(current_playlist_files):
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
                print(f"Loading playlist '{pending_fade_playlist}' after fadeout.")
                _load_and_play_playlist(pending_fade_playlist)
            else:
                print("Fadeout complete, but no pending playlist found. Stopping.")
                stop_music_internal()  # Ensure clean state if something went wrong

    # --- Determine Player State ---
    state = 'stopped'
    current_song_filename = None
    current_position_sec = 0
    current_playlist_songs_filenames = []
    all_playlist_details = persistent_data.get("playlist_details", {}).copy() # Copy to modify safely

    try:
        is_busy = pygame.mixer.music.get_busy()

        if is_busy:
            if is_paused:
                state = 'paused'
            else:
                state = 'playing'
        elif current_track_index != -1 and not is_paused:
            # Music was playing but isn't busy now -> song finished
            if stop_after_current_song:
                print("Stopping playback after current song completes due to stop transition.")
                stop_music_internal()  # Call stop_music_internal to reset state
                stop_after_current_song = False  # Reset the flag
            elif transition_after_current_song:
                print(f"Transitioning to next playlist '{transition_after_current_song}' after current song completes.")
                # Load the next playlist
                _load_and_play_playlist(transition_after_current_song)
                transition_after_current_song = None  # Reset the flag after transition
            else:
                # Normal song completion logic
                print("Processing normal song completion.")
                next_index = current_track_index + 1
                if 0 <= next_index < len(current_playlist_files):
                    if _play_track(next_index):
                        pass  # Handled by _play_track
                    else:
                        stop_music_internal()
                else:
                    print("End of playlist reached.")
                    # Handle end of playlist logic
                    playlist_details = persistent_data.get("playlist_details", {}).get(current_playlist_name, {})
                    should_loop = playlist_details.get("loop", False)
                    should_auto_advance = playlist_details.get("auto_advance", False)
                    
                    if should_loop:
                        print(f"Looping playlist '{current_playlist_name}' on song completion.")
                        _play_track(0)
                    elif should_auto_advance:
                        print(f"Auto-advancing on song completion from '{current_playlist_name}'.")
                        all_playlists = get_playlists()  # Re-fetch in case order changed
                        if current_playlist_name and all_playlists:
                            try:
                                current_idx = all_playlists.index(current_playlist_name)
                                next_playlist_idx = (current_idx + 1) % len(all_playlists)
                                next_playlist = all_playlists[next_playlist_idx]
                                print(f"Auto-advancing to: {next_playlist}")
                                _load_and_play_playlist(next_playlist)
                            except ValueError:
                                print(f"Could not find current playlist '{current_playlist_name}' in list for auto-advance. Stopping.")
                                stop_music_internal()
                            except Exception as e:
                                print(f"Error during auto-advance: {e}")
                                stop_music_internal()
                        else:
                            print("Cannot auto-advance: No current playlist or no playlists found. Stopping.")
                            stop_music_internal()
                    else:
                        # Neither loop nor auto-advance is enabled
                        print("End of playlist reached, no loop/auto-advance. Stopping playback.")
                        stop_music_internal()

        else:
            if is_paused:
                state = 'paused'
            else:
                state = 'stopped'
                if current_playlist_name is None and current_track_index != -1:
                    stop_music_internal()

        # Get current position if playing or paused
        if state == 'playing' or state == 'paused':
            current_position_sec = pygame.mixer.music.get_pos() / 1000.0
            if current_position_sec < 0:
                current_position_sec = 0

        if current_track_index != -1 and current_track_index < len(current_playlist_files):
            current_song_filename = os.path.basename(current_playlist_files[current_track_index])
            current_playlist_songs_filenames = [os.path.basename(f) for f in current_playlist_files]

        # Add stored runtimes to the details to be sent
        all_playlists = get_playlists() # Ensure we have the latest list
        for pl_name in all_playlists:
             if pl_name in all_playlist_details:
                  details = all_playlist_details[pl_name]
                  # Get stored runtime, default to 0.0 if not found (e.g., new playlist)
                  details['runtime_minutes'] = playlist_runtimes.get(pl_name, 0.0)
             # No need for an else here, if details don't exist, runtime won't be added.

        status = {
            'state': state,
            'current_playlist': current_playlist_name,
            'current_song': current_song_filename,
            'volume': round(pygame.mixer.music.get_volume() * 100),
            'playlists': all_playlists,
            'playlist_details': all_playlist_details,
            'current_playlist_songs': current_playlist_songs_filenames,
            'current_position_sec': current_position_sec,
            'song_duration_sec': current_song_duration_sec
        }
    except pygame.error as e:
        print(f"Pygame error in get_status: {e}")
        stop_music_internal()  # Reset state on error
        status = {
            'state': 'error',
            'current_playlist': None,
            'current_song': None,
            'volume': 0,
            # 'playlists': get_playlists(),
            'playlists': all_playlists, # Use list calculated earlier
            'error_message': str(e),
            'playlist_details': all_playlist_details,
            'current_playlist_songs': [],
            'current_position_sec': 0,
            'song_duration_sec': 0
        }

    return jsonify(status)

@app.route('/play', methods=['POST'])
def play_music():
    """Loads a playlist (folder) and starts playing the first track."""
    # Now uses the helper function
    data = request.get_json()
    playlist_name_req = data.get('playlist')

    if not playlist_name_req:
        return jsonify({'status': 'error', 'message': 'Playlist name not provided'}), 400

    success, message = _load_and_play_playlist(playlist_name_req)

    if success:
        return jsonify({'status': 'success', 'message': message})
    else:
        # Check if the error was 404 specifically
        if "not found" in message:
            return jsonify({'status': 'error', 'message': message}), 404
        else:
            return jsonify({'status': 'error', 'message': message}), 500

@app.route('/stop', methods=['POST'])
def stop_music():
    """Stops playback and clears the queue."""
    print("Received request to stop music")
    stop_music_internal()
    return jsonify({'status': 'success', 'message': 'Playback stopped'})

@app.route('/pause', methods=['POST'])
def pause_resume_music():
    """Pauses or resumes playback."""
    global is_paused, seek_request_while_paused, current_track_index, current_playlist_files
    print("Received request to pause/resume music")
    message = ""
    try:
        # Prioritize checking our internal state for unpausing
        if is_paused:
            # Apply pending seek BEFORE unpausing
            if seek_request_while_paused is not None:
                print(f"Applying pending seek to {seek_request_while_paused:.2f}s before unpausing.")
                try:
                    # Ensure seeking within bounds
                    if current_song_duration_sec > 0 and seek_request_while_paused > current_song_duration_sec:
                         seek_request_while_paused = current_song_duration_sec - 0.1
                    
                    # Try the safer method for seeking (reload and play)
                    if current_track_index >= 0 and current_track_index < len(current_playlist_files):
                        current_track = current_playlist_files[current_track_index]
                        pygame.mixer.music.load(current_track)
                        pygame.mixer.music.play(0, seek_request_while_paused)
                        is_paused = False  # We've now resumed with the new position
                        seek_request_while_paused = None  # Clear pending seek
                        message = f"Playback resumed at position {seek_request_while_paused:.2f}s"
                        print(message)
                        return jsonify({'status': 'success', 'message': message})
                except pygame.error as e:
                    print(f"Error applying pending seek: {e}")
                    # Fall through to regular unpause
                    seek_request_while_paused = None  # Clear pending seek
                except Exception as e:
                    print(f"Unexpected error during seek on unpause: {e}")
                    seek_request_while_paused = None  # Clear pending seek
                    # Fall through to regular unpause

            # Regular unpause if no seek pending or seek failed
            try:
                pygame.mixer.music.unpause()
                is_paused = False
                message = "Playback resumed"
                print(message)
            except pygame.error as e:
                print(f"Error unpausing: {e}")
                # If unpausing fails, try to reload and play the current track
                if current_track_index >= 0 and current_track_index < len(current_playlist_files):
                    try:
                        _play_track(current_track_index)
                        message = "Playback resumed (had to reload track)"
                    except Exception as reload_e:
                        print(f"Error reloading track: {reload_e}")
                        message = f"Failed to resume playback: {reload_e}"
                        return jsonify({'status': 'error', 'message': message}), 500
                else:
                    message = "Cannot resume: no track loaded"
                    return jsonify({'status': 'error', 'message': message}), 400
        # Only try to pause if we are not paused and music is playing
        elif pygame.mixer.music.get_busy():
            pygame.mixer.music.pause()
            is_paused = True
            message = "Playback paused"
            print(message)
        else:
            # Wasn't paused, and wasn't busy - can't do anything
            message = "Cannot pause/resume: Music not playing or already stopped."
            print(message)
        return jsonify({'status': 'success', 'message': message})
    except pygame.error as e:
        # Handle potential errors if pause/unpause fails unexpectedly
        message = f"Error during pause/resume: {e}"
        print(message)
        # Consider if state reset is needed on error, maybe not.
        return jsonify({'status': 'error', 'message': message}), 500

@app.route('/next', methods=['POST'])
def next_track():
    """Skips to the next track in the current playlist."""
    global current_track_index, current_playlist_files, current_playlist_name, persistent_data
    print("Received request to skip track")

    if current_track_index == -1 or not current_playlist_files:
         return jsonify({'status': 'error', 'message': 'No playlist loaded'}), 400

    next_index = current_track_index + 1
    if 0 <= next_index < len(current_playlist_files):
        if _play_track(next_index):
            return jsonify({'status': 'success', 'message': 'Skipped to next track'})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to play next track'}), 500
    else:
        # Last track finished, apply end-of-playlist logic (loop/auto-advance/stop)
        print("Next track requested at end of playlist.")
        playlist_details = persistent_data.get("playlist_details", {}).get(current_playlist_name, {})
        should_loop = playlist_details.get("loop", False)
        should_auto_advance = playlist_details.get("auto_advance", False)

        if should_loop:
            print(f"Looping playlist '{current_playlist_name}' on next track request.")
            if _play_track(0):
                return jsonify({'status': 'success', 'message': 'Looped to start of playlist'})
            else:
                return jsonify({'status': 'error', 'message': 'Failed to loop playlist'}), 500
        elif should_auto_advance:
            print(f"Auto-advancing on next track request from '{current_playlist_name}'.")
            all_playlists = get_playlists() # Re-fetch in case order changed
            if current_playlist_name and all_playlists:
                try:
                    current_idx = all_playlists.index(current_playlist_name)
                    next_playlist_idx = (current_idx + 1) % len(all_playlists)
                    next_playlist = all_playlists[next_playlist_idx]
                    print(f"Auto-advancing to: {next_playlist}")
                    # Use the refactored load/play function
                    success, message = _load_and_play_playlist(next_playlist)
                    if success:
                        return jsonify({'status': 'success', 'message': f'Auto-advanced to playlist: {next_playlist}'})
                    else:
                         # _load_and_play_playlist calls stop_music_internal on failure
                        return jsonify({'status': 'error', 'message': f'Failed to auto-advance: {message}'}), 500
                except ValueError:
                    print(f"Could not find current playlist '{current_playlist_name}' in list for auto-advance. Stopping.")
                    stop_music_internal()
                    return jsonify({'status': 'success', 'message': 'Current playlist not found, playback stopped'})
                except Exception as e:
                     print(f"Error during auto-advance: {e}")
                     stop_music_internal()
                     return jsonify({'status': 'error', 'message': f'Error during auto-advance: {e}'}), 500
            else:
                print("Cannot auto-advance: No current playlist or no playlists found. Stopping.")
                stop_music_internal()
                return jsonify({'status': 'success', 'message': 'Cannot auto-advance, playback stopped'})

        # Neither loop nor auto-advance is enabled
        print("End of playlist reached, no loop/auto-advance. Stopping playback.")
        stop_music_internal()
        return jsonify({'status': 'success', 'message': 'End of playlist reached, playback stopped'})

@app.route('/volume', methods=['POST'])
def set_volume():
    """Sets the playback volume."""
    data = request.get_json()
    try:
        volume_percent = int(data.get('volume'))
        if 0 <= volume_percent <= 100:
            volume_float = volume_percent / 100.0
            pygame.mixer.music.set_volume(volume_float)
            print(f"Volume set to {volume_percent}%")
            return jsonify({'status': 'success', 'message': f'Volume set to {volume_percent}%'})
        else:
            return jsonify({'status': 'error', 'message': 'Volume must be between 0 and 100'}), 400
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid volume value provided'}), 400
    except pygame.error as e:
        print(f"Error setting volume: {e}")
        return jsonify({'status': 'error', 'message': f'Error setting volume: {e}'}), 500

@app.route('/reorder', methods=['POST'])
def reorder_playlists():
    """Handles reordering of playlists and saves the new order."""
    global persistent_data
    data = request.get_json()
    new_order = data.get('order')

    if not isinstance(new_order, list):
         return jsonify({'status': 'error', 'message': 'Invalid order data provided'}), 400

    print(f"Received request to reorder playlists: {new_order}")

    # Basic validation: Ensure all provided names are actual playlists
    current_playlists = get_playlists() # Gets the combined list
    if not all(pl_name in current_playlists for pl_name in new_order):
         # This check might be too strict if get_playlists() hasn't caught up yet?
         # Or could compare against os.listdir directly? For now, compare against get_playlists result.
         print("Warning: New order contains unknown or duplicate playlist names. Ignoring.")
         # Maybe just filter the new_order?
         valid_new_order = [pl for pl in new_order if pl in current_playlists]
         if len(valid_new_order) != len(set(valid_new_order)):
              return jsonify({'status': 'error', 'message': 'New order contains duplicate playlist names'}), 400
         if len(valid_new_order) != len(current_playlists):
             # Handle case where some playlists were missing from the drag operation?
             # Reconstruct full order: valid_new_order + missing items
             missing_playlists = [pl for pl in current_playlists if pl not in valid_new_order]
             persistent_data["playlist_order"] = valid_new_order + missing_playlists
             print(f"Reordered playlists (with missing appended): {persistent_data['playlist_order']}")

         else:
             persistent_data["playlist_order"] = valid_new_order
             print(f"Reordered playlists: {valid_new_order}")

    else:
        # Check for duplicates in submitted order
        if len(new_order) != len(set(new_order)):
             return jsonify({'status': 'error', 'message': 'New order contains duplicate playlist names'}), 400
        # Check if all current playlists are accounted for
        if set(new_order) != set(current_playlists):
             # This case means some playlists were missing from the submitted order. Reconstruct.
             missing_playlists = [pl for pl in current_playlists if pl not in new_order]
             persistent_data["playlist_order"] = new_order + missing_playlists # Append missing
             print(f"Reordered playlists (with missing appended): {persistent_data['playlist_order']}")

        else:
             persistent_data["playlist_order"] = new_order
             print(f"Reordered playlists: {new_order}")

    save_persistent_data()
    return jsonify({'status': 'success', 'message': 'Playlist order updated', 'new_order': persistent_data["playlist_order"]})

@app.route('/reorder_songs', methods=['POST'])
def reorder_songs():
    """Handles reordering of songs within a specific playlist and saves."""
    global persistent_data
    data = request.get_json()
    playlist_name = data.get('playlist_name')
    # Expecting list of full file paths in the new order
    new_song_paths = data.get('new_song_order')

    if not playlist_name or not isinstance(new_song_paths, list):
        return jsonify({'status': 'error', 'message': 'Missing playlist name or invalid song order data'}), 400

    if playlist_name not in persistent_data.get("playlist_details", {}):
        return jsonify({'status': 'error', 'message': f'Playlist "{playlist_name}" not found in persistent data'}), 404

    print(f"Received request to reorder songs for playlist: {playlist_name}")

    # Validate paths? Check they exist and belong to the playlist?
    # For now, trust the client sends correct full paths based on what it received.
    # Basic check: Ensure no duplicates
    if len(new_song_paths) != len(set(new_song_paths)):
         return jsonify({'status': 'error', 'message': 'New song order contains duplicates'}), 400

    # Update the order
    persistent_data["playlist_details"][playlist_name]["song_order"] = new_song_paths
    save_persistent_data()

     # If this playlist is currently playing, update the live queue
    global current_playlist_name, current_playlist_files, current_track_index
    if playlist_name == current_playlist_name:
        print("Updating live playlist queue due to reordering.")
        # Find the currently playing song's *new* index
        current_song_path = None
        if 0 <= current_track_index < len(current_playlist_files):
             current_song_path = current_playlist_files[current_track_index]

        current_playlist_files = new_song_paths # Update the live list

        if current_song_path and current_song_path in new_song_paths:
            try:
                current_track_index = new_song_paths.index(current_song_path)
                print(f"Current track is now at index {current_track_index}")
            except ValueError:
                 # Should not happen if validation is okay, but handle defensively
                 print("Warning: Current song path not found in new order after reorder. Resetting index.")
                 # Maybe stop playback or go to index 0? Let's reset to 0 for now.
                 if len(current_playlist_files) > 0:
                     _play_track(0)
                 else:
                     stop_music_internal()

        elif len(current_playlist_files) > 0:
             # If no song was playing or current song was removed/not found, restart playlist?
             print("Restarting playlist from index 0 after reorder.")
             _play_track(0)
        else:
             # Playlist is now empty after reorder?
             print("Playlist is empty after reorder. Stopping.")
             stop_music_internal()

    return jsonify({'status': 'success', 'message': f'Song order updated for {playlist_name}'})

@app.route('/set_playlist_volume', methods=['POST'])
def set_playlist_volume():
    """Sets and saves the default volume for a specific playlist."""
    global persistent_data
    data = request.get_json()
    playlist_name = data.get('playlist_name')
    volume = data.get('volume') # Expecting 0-100 or null

    if not playlist_name: # Volume can be None to unset it
         return jsonify({'status': 'error', 'message': 'Missing playlist name'}), 400

    volume_int = None
    if volume is not None:
        try:
            volume_int = int(volume)
            if not (0 <= volume_int <= 100):
                raise ValueError("Volume out of range")
        except (TypeError, ValueError):
             return jsonify({'status': 'error', 'message': 'Invalid volume value (must be 0-100)'}), 400

    # Ensure the playlist exists in our details (crucial before accessing sub-keys)
    if playlist_name not in persistent_data.get("playlist_details", {}):
        # If playlist exists on disk but not in details yet, try adding it.
        if playlist_name in get_playlists(): # get_playlists ensures details are populated
             # Check again after get_playlists possibly added it
             if playlist_name not in persistent_data["playlist_details"]:
                  # This case should be rare if get_playlists works correctly
                  print(f"Error: Playlist '{playlist_name}' found but details not populated.")
                  return jsonify({'status': 'error', 'message': f'Internal error populating details for {playlist_name}'}), 500
        else:
             return jsonify({'status': 'error', 'message': f'Playlist "{playlist_name}" not found'}), 404

    # Now we are sure playlist_name key exists in persistent_data["playlist_details"]
    print(f"Setting default volume for playlist '{playlist_name}' to {volume_int if volume_int is not None else 'unset'}")
    persistent_data["playlist_details"][playlist_name]["default_volume"] = volume_int
    # Ensure other keys exist if we just added the playlist entry implicitly via get_playlists
    persistent_data["playlist_details"][playlist_name].setdefault("song_order", [])
    persistent_data["playlist_details"][playlist_name].setdefault("auto_advance", False)
    save_persistent_data()

    # Apply volume immediately if this playlist is currently playing?
    global current_playlist_name
    if playlist_name == current_playlist_name and volume_int is not None:
         try:
             volume_float = volume_int / 100.0
             pygame.mixer.music.set_volume(volume_float)
             print(f"Applied volume {volume_int}% to current playback.")
         except pygame.error as e:
             print(f"Error applying volume {volume_int}% immediately: {e}")

    return jsonify({'status': 'success', 'message': f'Default volume for {playlist_name} set to {volume_int if volume_int is not None else "unset"}'})

@app.route('/set_playlist_auto_advance', methods=['POST'])
def set_playlist_auto_advance():
    """Sets and saves the auto-advance setting for a specific playlist."""
    global persistent_data
    data = request.get_json()
    playlist_name = data.get('playlist_name')
    enabled = data.get('enabled')

    if not playlist_name or enabled is None or not isinstance(enabled, bool):
        return jsonify({'status': 'error', 'message': 'Missing playlist name or invalid enabled value'}), 400

    # Ensure the playlist exists in our details (crucial before accessing sub-keys)
    if playlist_name not in persistent_data.get("playlist_details", {}):
        # If playlist exists on disk but not in details yet, try adding it.
        if playlist_name in get_playlists(): # get_playlists ensures details are populated
            # Check again after get_playlists possibly added it
             if playlist_name not in persistent_data["playlist_details"]:
                  # This case should be rare if get_playlists works correctly
                  print(f"Error: Playlist '{playlist_name}' found but details not populated.")
                  return jsonify({'status': 'error', 'message': f'Internal error populating details for {playlist_name}'}), 500
        else:
             return jsonify({'status': 'error', 'message': f'Playlist "{playlist_name}" not found'}), 404

    # Now we are sure playlist_name key exists in persistent_data["playlist_details"]
    print(f"Setting auto-advance for playlist '{playlist_name}' to {enabled}")
    persistent_data["playlist_details"][playlist_name]["auto_advance"] = enabled
    # Ensure other keys exist if we just added the playlist entry implicitly via get_playlists
    persistent_data["playlist_details"][playlist_name].setdefault("song_order", [])
    persistent_data["playlist_details"][playlist_name].setdefault("default_volume", None)
    persistent_data["playlist_details"][playlist_name].setdefault("loop", False)
    persistent_data["playlist_details"][playlist_name].setdefault("transition_mode", "fade")
    save_persistent_data()

    return jsonify({'status': 'success', 'message': f'Auto-advance for {playlist_name} set to {enabled}', 'auto_advance_enabled': enabled}) # Keep response key for potential frontend use

@app.route('/set_playlist_loop', methods=['POST'])
def set_playlist_loop():
    """Sets and saves the loop setting for a specific playlist."""
    global persistent_data
    data = request.get_json()
    playlist_name = data.get('playlist_name')
    enabled = data.get('enabled')

    if not playlist_name or enabled is None or not isinstance(enabled, bool):
        return jsonify({'status': 'error', 'message': 'Missing playlist name or invalid enabled value'}), 400

    # Ensure the playlist exists in our details
    if playlist_name not in persistent_data.get("playlist_details", {}):
        if playlist_name in get_playlists(): # Try to add it
             if playlist_name not in persistent_data["playlist_details"]:
                  print(f"Error: Playlist '{playlist_name}' found but details not populated.")
                  return jsonify({'status': 'error', 'message': f'Internal error populating details for {playlist_name}'}), 500
        else:
             return jsonify({'status': 'error', 'message': f'Playlist "{playlist_name}" not found'}), 404

    # Update the loop setting
    print(f"Setting loop for playlist '{playlist_name}' to {enabled}")
    persistent_data["playlist_details"][playlist_name]["loop"] = enabled
    # Ensure other keys exist
    persistent_data["playlist_details"][playlist_name].setdefault("song_order", [])
    persistent_data["playlist_details"][playlist_name].setdefault("default_volume", None)
    persistent_data["playlist_details"][playlist_name].setdefault("auto_advance", False)
    persistent_data["playlist_details"][playlist_name].setdefault("transition_mode", "fade")
    save_persistent_data()

    return jsonify({'status': 'success', 'message': f'Loop for {playlist_name} set to {enabled}', 'loop_enabled': enabled})

@app.route('/set_playlist_transition_mode', methods=['POST'])
def set_playlist_transition_mode():
    """Sets and saves the transition mode for a specific playlist."""
    global persistent_data
    data = request.get_json()
    playlist_name = data.get('playlist_name')
    mode = data.get('mode')

    if not playlist_name or not mode:
        return jsonify({'status': 'error', 'message': 'Missing playlist name or transition mode'}), 400

    # Validate transition mode
    if mode not in TRANSITION_MODES:
        return jsonify({'status': 'error', 'message': f'Invalid transition mode. Must be one of: {", ".join(TRANSITION_MODES)}'}), 400

    # Ensure the playlist exists in our details
    if playlist_name not in persistent_data.get("playlist_details", {}):
        if playlist_name in get_playlists(): # Try to add it
             if playlist_name not in persistent_data["playlist_details"]:
                  print(f"Error: Playlist '{playlist_name}' found but details not populated.")
                  return jsonify({'status': 'error', 'message': f'Internal error populating details for {playlist_name}'}), 500
        else:
             return jsonify({'status': 'error', 'message': f'Playlist "{playlist_name}" not found'}), 404

    # Update the transition mode
    print(f"Setting transition mode for playlist '{playlist_name}' to '{mode}'")
    persistent_data["playlist_details"][playlist_name]["transition_mode"] = mode
    # Ensure other keys exist
    persistent_data["playlist_details"][playlist_name].setdefault("song_order", [])
    persistent_data["playlist_details"][playlist_name].setdefault("default_volume", None)
    persistent_data["playlist_details"][playlist_name].setdefault("auto_advance", False)
    persistent_data["playlist_details"][playlist_name].setdefault("loop", False)
    save_persistent_data()

    return jsonify({'status': 'success', 'message': f'Transition mode for {playlist_name} set to {mode}', 'transition_mode': mode})

@app.route('/next_playlist', methods=['POST'])
def next_playlist():
    """Triggers a transition to the next playlist based on the current playlist's transition mode."""
    global persistent_data, current_playlist_name, current_track_index, is_paused, FADEOUT_COMPLETE_EVENT, pending_fade_playlist, transition_after_current_song, stop_after_current_song

    if not current_playlist_name:
        return jsonify({'status': 'error', 'message': 'No playlist is currently active'}), 400

    # Get the current playlist's details
    playlist_details = persistent_data.get("playlist_details", {}).get(current_playlist_name, {})
    transition_mode = playlist_details.get("transition_mode", "fade")

    # Get the next playlist
    all_playlists = get_playlists()
    if not all_playlists:
        return jsonify({'status': 'error', 'message': 'No playlists available'}), 400

    try:
        current_idx = all_playlists.index(current_playlist_name)
        next_idx = (current_idx + 1) % len(all_playlists)
        next_playlist_name = all_playlists[next_idx]
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Current playlist not found in playlist list'}), 500

    # Handle transition based on mode
    if transition_mode == "fade":
        # Initiate fadeout, store pending playlist, set end event
        if pygame.mixer.music.get_busy() and not pending_fade_playlist and not transition_after_current_song:
            print(f"Initiating fadeout (5s) to transition to '{next_playlist_name}'...")
            pending_fade_playlist = next_playlist_name
            pygame.mixer.music.set_endevent(FADEOUT_COMPLETE_EVENT)
            pygame.mixer.music.fadeout(5000) # 5000 milliseconds = 5 seconds
            return jsonify({'status': 'success', 'message': f'Fading out. Will transition to {next_playlist_name}', 'next_playlist': next_playlist_name})
        elif pending_fade_playlist:
             return jsonify({'status': 'error', 'message': f'Already fading out to {pending_fade_playlist}. Please wait.'}), 400
        elif transition_after_current_song:
              return jsonify({'status': 'error', 'message': f'Already waiting for song to complete for playlist {transition_after_current_song}. Cannot start fade.'}), 400
        else:
             # Not playing, just switch immediately
             print(f"Music not playing, switching directly to '{next_playlist_name}' (fade mode).")
             success, message = _load_and_play_playlist(next_playlist_name)
             if success:
                return jsonify({'status': 'success', 'message': f'Transitioned to playlist: {next_playlist_name}', 'next_playlist': next_playlist_name})
             else:
                return jsonify({'status': 'error', 'message': f'Failed to transition: {message}'}), 500

    elif transition_mode == "complete":
        # Mark that we should transition after the current song
        # Prevent setting if already fading
        if pending_fade_playlist:
            return jsonify({'status': 'error', 'message': f'Cannot set transition mode "complete" while fading out to {pending_fade_playlist}.'}), 400
        # Prevent setting if already set
        if transition_after_current_song:
             return jsonify({'status': 'error', 'message': f'Already waiting for song to complete for playlist {transition_after_current_song}.'}), 400

        print(f"Setting transition to '{next_playlist_name}' after current song completes.")
        transition_after_current_song = next_playlist_name
        # Clear any previously set end event just in case
        pygame.mixer.music.set_endevent()
        return jsonify({'status': 'success', 'message': f'Will transition to {next_playlist_name} after current song completes', 'next_playlist': next_playlist_name})

    elif transition_mode == "stop":
        # Set the flag to indicate that we should stop after the current song finishes
        print("Setting to stop playback after current song completes due to transition mode 'stop'.")
        transition_after_current_song = None  # Clear any previous transitions
        pending_fade_playlist = None  # Clear any pending fade transitions
        stop_after_current_song = True  # Set the flag to indicate stopping after the current song
        pygame.mixer.music.set_endevent()  # Clear any end events
        return jsonify({'status': 'success', 'message': 'Will stop playback after current song completes'})

    else:
        return jsonify({'status': 'error', 'message': f'Unknown transition mode: {transition_mode}'}), 500

@app.route('/seek', methods=['POST'])
def seek_music():
    """Seeks to a specific position in the current track."""
    global current_track_index, is_paused, seek_request_while_paused, current_playlist_files, current_playlist_name
    
    if current_track_index == -1:
        return jsonify({'status': 'error', 'message': 'No track loaded'}), 400

    data = request.get_json()
    try:
        seek_time_sec = float(data.get('position'))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid seek position provided'}), 400

    if seek_time_sec < 0:
        seek_time_sec = 0

    # Ensure seeking within bounds
    if current_song_duration_sec > 0 and seek_time_sec > current_song_duration_sec:
         seek_time_sec = current_song_duration_sec - 0.1 # Seek near the end

    # If paused, store the request instead of seeking immediately
    if is_paused:
        seek_request_while_paused = seek_time_sec
        print(f"Playback paused. Storing seek request to {seek_time_sec:.2f} seconds.")
        # Return success, the seek will happen on unpause
        return jsonify({'status': 'success', 'message': f'Seek request to {seek_time_sec:.2f}s stored (will apply on resume)'})

    # The only reliable solution is to completely reinitialize pygame mixer
    try:
        print(f"Seeking to {seek_time_sec:.2f} seconds")
        
        if 0 <= current_track_index < len(current_playlist_files):
            # Get the current track path
            current_track = current_playlist_files[current_track_index]
            
            # Save current volume
            try:
                current_volume = pygame.mixer.music.get_volume()
            except pygame.error:
                current_volume = 1.0
            
            # IMPORTANT: Complete reinitialization sequence to fix event handling
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
            global last_event_check_time
            last_event_check_time = time.time()  # Reset the event check time
            return jsonify({'status': 'success', 'message': f'Seeked to {seek_time_sec:.2f}s'})
        else:
            return jsonify({'status': 'error', 'message': 'Current track index is invalid'}), 400

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
            if 0 <= current_track_index < len(current_playlist_files):
                pygame.mixer.music.load(current_playlist_files[current_track_index])
                pygame.mixer.music.play()
                return jsonify({'status': 'error', 'message': f'Seeking failed, restarted track from beginning: {e}'}), 400
            else:
                stop_music_internal()
                return jsonify({'status': 'error', 'message': 'Seeking failed, playback stopped'}), 500
        except Exception as recovery_e:
            print(f"Complete recovery failed: {recovery_e}")
            stop_music_internal()
            return jsonify({'status': 'error', 'message': 'Seeking and recovery failed. Playback stopped.'}), 500

@app.route('/rename_playlist', methods=['POST'])
def rename_playlist():
    """Renames a playlist folder and updates all references."""
    global persistent_data, current_playlist_name
    data = request.get_json()
    old_name = data.get('old_name')
    new_name = data.get('new_name')

    if not old_name or not new_name:
        return jsonify({'status': 'error', 'message': 'Missing old or new playlist name'}), 400

    # Validate new name (no slashes, not empty, etc.)
    if '/' in new_name or '\\' in new_name or new_name.strip() == '':
        return jsonify({'status': 'error', 'message': 'Invalid new playlist name'}), 400

    # Check if source exists
    old_path = os.path.join(MUSIC_DIR, old_name)
    if not os.path.isdir(old_path):
        return jsonify({'status': 'error', 'message': f'Playlist folder not found: {old_name}'}), 404

    # Check if destination already exists
    new_path = os.path.join(MUSIC_DIR, new_name)
    if os.path.exists(new_path):
        return jsonify({'status': 'error', 'message': f'A playlist named {new_name} already exists'}), 400

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
                        updated_paths.append(path.replace(old_path, new_path, 1))
                    else:
                        updated_paths.append(path)
                details["song_order"] = updated_paths

        # Update current playlist name if it was renamed
        if current_playlist_name == old_name:
            current_playlist_name = new_name
            # Update current playlist files paths
            global current_playlist_files
            current_playlist_files = [path.replace(old_path, new_path, 1) if path.startswith(old_path) else path
                                    for path in current_playlist_files]

        # Save changes
        save_persistent_data()

        return jsonify({
            'status': 'success',
            'message': f'Renamed playlist from {old_name} to {new_name}',
            'new_name': new_name
        })

    except OSError as e:
        return jsonify({'status': 'error', 'message': f'Error renaming playlist: {str(e)}'}), 500

@app.route('/rename_song', methods=['POST'])
def rename_song():
    """Renames a song file and updates all references."""
    global persistent_data, current_playlist_files, current_track_index, playlist_runtimes # Add playlist_runtimes
    data = request.get_json()
    playlist_name = data.get('playlist_name')
    old_name = data.get('old_name')  # Can be basename or full path
    new_name = data.get('new_name')  # Should be just the new basename

    if not playlist_name or not old_name or not new_name:
        return jsonify({'status': 'error', 'message': 'Missing required parameters'}), 400

    # Validate new name (no slashes, not empty, etc.)
    if '/' in new_name or '\\' in new_name or new_name.strip() == '':
        return jsonify({'status': 'error', 'message': 'Invalid new song name'}), 400

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
        return jsonify({'status': 'error', 'message': f'Song file not found: {old_basename}'}), 404

    # Check if destination already exists
    if os.path.exists(new_path):
        return jsonify({'status': 'error', 'message': f'A song named {new_name} already exists in this playlist'}), 400

    try:
        # Rename the file
        os.rename(old_path, new_path)
        print(f"Renamed song from '{old_basename}' to '{new_name}' in playlist '{playlist_name}'")

        # Update song paths in playlist details
        if playlist_name in persistent_data.get("playlist_details", {}):
            details = persistent_data["playlist_details"][playlist_name]
            if "song_order" in details:
                # Find and update the path in song_order
                for i, path in enumerate(details["song_order"]):
                    if path == old_path or os.path.basename(path) == old_basename:
                        details["song_order"][i] = new_path
                        break

        # Update current playlist files if this playlist is active
        if playlist_name == current_playlist_name:
            for i, path in enumerate(current_playlist_files):
                if path == old_path or os.path.basename(path) == old_basename:
                    current_playlist_files[i] = new_path
                    break

        # Save changes
        save_persistent_data()

        # Recalculate runtime for the affected playlist after rename
        if playlist_name in persistent_data.get("playlist_details", {}):
            details = persistent_data["playlist_details"][playlist_name]
            playlist_runtimes[playlist_name] = calculate_playlist_runtime(playlist_name, details)
            print(f"Recalculated runtime for '{playlist_name}' after song rename: {playlist_runtimes[playlist_name]} min")

        return jsonify({
            'status': 'success',
            'message': f'Renamed song from {old_basename} to {new_name}',
            'new_name': new_name
        })

    except OSError as e:
        return jsonify({'status': 'error', 'message': f'Error renaming song: {str(e)}'}), 500

if __name__ == '__main__':
    # Use 0.0.0.0 to make it accessible on your network
    # Turn off debug mode for potential production use or if causing issues
    calculate_and_store_all_runtimes() # Calculate runtimes once on startup
    app.run(debug=False, host='0.0.0.0', port=5522)

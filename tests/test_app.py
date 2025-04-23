import pytest
import os
import sys
from unittest.mock import patch, MagicMock
try:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # Import the Flask app instance and functions to test
    from app import (
        app as flask_app, get_playlists, calculate_playlist_runtime,
        _get_ordered_songs_for_playlist, get_song_duration)
except IndexError:
    pass

# --- Fixtures ---


@pytest.fixture
def app():
    """Create and configure a new app instance for each test."""
    # Configure app for testing
    flask_app.config.update({
        "TESTING": True,
    })
    # TODO: Consider setting MUSIC_DIR to a temporary test directory
    # TODO: Consider mocking pygame.init() and other setup if needed
    yield flask_app


@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """A test runner for the app's Click commands (if any)."""
    return app.test_cli_runner()


@pytest.fixture(autouse=True)
def mock_pygame(mocker):
    """Auto-used fixture to mock pygame initialization and mixer.
       Prevents tests from needing actual audio drivers/hardware.
    """
    mocker.patch('pygame.init', return_value=None)
    mock_mixer = mocker.patch('pygame.mixer', MagicMock())
    # Mock specific mixer functions if needed, e.g.:
    # mock_mixer.music.get_busy.return_value = False
    # mock_mixer.music.get_pos.return_value = 0
    # ... etc.
    return mock_mixer


@pytest.fixture
def mock_save_data(mocker):
    """Fixture to mock the save_persistent_data function."""
    mocker.patch('app.save_persistent_data', return_value=None)

# --- Test Functions ---


# Test Basic Route
def test_index_route(client):
    """Test the main index route returns successfully."""
    response = client.get('/')
    assert response.status_code == 200
    assert b"<h1>Music Controller</h1>" in response.data


# Test get_playlists (using pyfakefs for filesystem mocking)
def test_get_playlists(fs, mock_save_data):
    """Test discovery of playlists from a fake filesystem."""
    # Set up a fake MUSIC_DIR and playlist folders
    fake_music_dir = "/fakemusic"
    fs.create_dir(os.path.join(fake_music_dir, "Playlist A"))
    fs.create_dir(os.path.join(fake_music_dir, "Playlist C"))
    fs.create_dir(os.path.join(fake_music_dir, "Playlist B"))
    fs.create_file(os.path.join(fake_music_dir, "not_a_playlist.txt"))

    # Patch the MUSIC_DIR constant within the app module for this test
    with patch('app.MUSIC_DIR', fake_music_dir):
        # Reset persistent_data for a clean test
        clean_data = {"playlist_order": [], "playlist_details": {}}
        with patch('app.persistent_data', clean_data):
            playlists = get_playlists()
            # Should be sorted alphabetically initially
            assert playlists == ["Playlist A", "Playlist B", "Playlist C"]

            # Test with pre-existing order
            pre_ordered_data = {
                "playlist_order": ["Playlist C", "Playlist A"],
                "playlist_details": {
                    "Playlist C": {"song_order": []},
                    "Playlist A": {"song_order": []}
                }
            }
            with patch('app.persistent_data', pre_ordered_data):
                # Playlist B is new, should be appended
                playlists_reordered = get_playlists()
                assert playlists_reordered == ["Playlist C", "Playlist A", "Playlist B"]


# Test _get_ordered_songs_for_playlist (using pyfakefs)
def test_get_ordered_songs_for_playlist(fs, mock_save_data):
    """Test discovery and ordering of songs within a playlist."""
    fake_music_dir = "/fakemusic"
    playlist_name = "My Hits"
    playlist_path = os.path.join(fake_music_dir, playlist_name)
    fs.create_file(os.path.join(playlist_path, "song1.mp3"))
    fs.create_file(os.path.join(playlist_path, "song3.ogg"))
    fs.create_file(os.path.join(playlist_path, "song2.wav"))
    fs.create_file(os.path.join(playlist_path, "other.txt"))

    # Full paths expected in persistent data
    song1_path = os.path.join(playlist_path, "song1.mp3")
    song2_path = os.path.join(playlist_path, "song2.wav")
    song3_path = os.path.join(playlist_path, "song3.ogg")

    with patch('app.MUSIC_DIR', fake_music_dir):
        # Test initial discovery (alphabetical)
        initial_data = {
            "playlist_order": [playlist_name],
            "playlist_details": {
                playlist_name: {"song_order": [],
                                "default_volume": None,
                                "auto_advance": False}
            }
        }
        with patch('app.persistent_data', initial_data):
            songs = _get_ordered_songs_for_playlist(playlist_name)
            assert songs == [song1_path, song2_path, song3_path]  # Sorted alphabetically

        # Test with pre-ordered songs
        ordered_data = {
            "playlist_order": [playlist_name],
            "playlist_details": {
                playlist_name: {
                    "song_order": [song3_path, song1_path],
                    "default_volume": None,
                    "auto_advance": False
                }
            }
        }
        with patch('app.persistent_data', ordered_data):
            songs_reordered = _get_ordered_songs_for_playlist(playlist_name)
            # song2 is new, should be added alphabetically after existing order
            assert songs_reordered == [song3_path, song1_path, song2_path]


# Test calculate_playlist_runtime (mocking get_song_duration)
def test_calculate_playlist_runtime(mocker):
    """Test runtime calculation by mocking the duration of individual songs."""
    playlist_name = "Test Playlist"
    playlist_details = {
        "song_order": ["/path/song1.mp3", "/path/song2.mp3", "/path/song3.mp3"]
    }

    # Mock app.get_song_duration to return specific values
    mock_get_duration = mocker.patch('app.get_song_duration')
    # Define return values for each call
    mock_get_duration.side_effect = [120.0, 180.5, 60.0]  # Durations (s)

    expected_runtime_sec = 120.0 + 180.5 + 60.0
    expected_runtime_min = round(expected_runtime_sec / 60, 1)
    runtime = calculate_playlist_runtime(playlist_name, playlist_details)

    assert runtime == expected_runtime_min
    # Verify mock was called correctly
    assert mock_get_duration.call_count == 3
    mock_get_duration.assert_any_call("/path/song1.mp3")
    mock_get_duration.assert_any_call("/path/song2.mp3")
    mock_get_duration.assert_any_call("/path/song3.mp3")


# Test get_song_duration (mocking subprocess.Popen)
def test_get_song_duration_success(mocker, fs):
    """Test successful duration retrieval using ffprobe mock."""
    fake_song = "/fakemusic/test.mp3"
    fs.create_file(fake_song)

    mock_popen = MagicMock()
    mock_popen.returncode = 0
    # Simulate ffprobe output
    ffprobe_output = b'[FORMAT]\nduration=123.4560000\n[/FORMAT]\n'
    mock_popen.communicate.return_value = (ffprobe_output, b'')

    mocker.patch('os.path.isfile', return_value=True)  # Ensure file check passes
    mock_subprocess_popen = mocker.patch('subprocess.Popen', return_value=mock_popen)

    print(fake_song)
    duration = get_song_duration(fake_song)

    assert duration == 123.456
    mock_subprocess_popen.assert_called_once()
    # Can add more specific checks on the args passed to Popen if needed


def test_get_song_duration_ffprobe_fail_pygame_fallback(mocker, fs):
    """Test ffprobe failing and falling back to pygame duration."""
    fake_song = "/fakemusic/test.ogg"
    fs.create_file(fake_song)

    # Mock ffprobe failure
    mock_popen_fail = MagicMock()
    mock_popen_fail.returncode = 1
    mock_popen_fail.communicate.return_value = (b'', b'Error')
    mocker.patch('subprocess.Popen', return_value=mock_popen_fail)

    # Mock pygame.mixer.Sound
    mock_sound = MagicMock()
    mock_sound.get_length.return_value = 60.5
    mock_mixer = mocker.patch('pygame.mixer')  # Mock the module
    mock_mixer.Sound.return_value = mock_sound

    mocker.patch('os.path.isfile', return_value=True)

    duration = get_song_duration(fake_song)

    assert duration == 60.5
    mock_mixer.Sound.assert_called_once_with(fake_song)

# --- Placeholder Tests for Functions Requiring More Mocking ---

# TODO: Test _play_track - Requires mocking pygame.mixer.music.load/play/set_endevent, Sound
# TODO: Test stop_music_internal - Requires mocking pygame.mixer.music.stop/unload/set_endevent
# TODO: Test _load_and_play_playlist - Requires mocking _get_ordered_songs..., _play_track, set_volume
# TODO: Test /status endpoint - Requires mocking pygame.mixer.music states, get_playlists, etc.
# TODO: Test /play, /stop, /pause, /next endpoints - Mock internal functions like _load_and_play..., stop_music..., _play_track and pygame state
# TODO: Test /volume, /seek - Mock pygame.mixer.music.set_volume/play
# TODO: Test reorder/set_* routes - Mock persistent_data, save_persistent_data, potentially current playback state updates
# TODO: Test rename routes - Mock os.rename, persistent_data, save_persistent_data, current playback state


# Example of how to test an endpoint needing state:
def test_stop_endpoint(client, mock_pygame):
    """Test the /stop endpoint."""
    # Optional: Set up a mock state where music is 'playing'
    # mock_pygame.music.get_busy.return_value = True

    response = client.post('/stop')
    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'success'

    # Assert that the stop function was called on the mock
    mock_pygame.music.stop.assert_called_once()
    mock_pygame.music.unload.assert_called_once()

Flask app playlist controller using PyGame. Made for my wedding! Stores the playlist order and song order in a JSON file, allows drag and drop reordering, playback control, automatic transitions between playlists with settings to control whether to auto-advance, to loop, or to advance after the current playlist ends. Allows for playlist-specific default volumes.

If working on NixOS, the provided flake.nix will create a FHS environment with the necessary dependencies and the pixi.toml will create a pixi environment:

- `nix develop` to enter the FHS environment
- `pixi install` to enter the pixi environment
- `python app.py` to run the app

WARNING: This project was heavily "vibe coded". I don't recommend using it as a template for anything, but there might be some interesting ideas in here for people, so I figured I would share.

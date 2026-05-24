# SkyViper Drone FPV Streamer
This small python program is a CLI that provides a livefeed that is sufficient for most FPV purposes. Afterwards, the feed can be opened in the browser.

### Note on Vibe-Coding
The program was initially vibe-coded by OpenAI's Codex because I had it look through the reconstructed Java sources from the Android app to figure out how it worked. However, it will not stay completely vibecoded and I will be updating it like all my other human-written software.

# Requirements
- Python 3.7 or later
- If an `xr872` family drone (that includes all SkyViper Vistas, cheaper drones made by SkyViper that have cameras, etc), a browser capable of opening a HTTP live video feed
- Otherwise, FFmpeg in the PATH is required to view

# Usage (no installation required; this is portable software)
1. Clone the repo
2. Switch your computer's WiFi to the drone-hosted WiFi AP (drone must be on)
3. Run `main.py` with the drone's IP passed with the `--ip` flag
4. If the script opens `ffplay`, you are done. Otherwise, follow the instructions to open the port on `localhost` in your browser to view.
5. Profit (you can now fly the drone as if you were doing such with the mobile app

# License
All past, present, and future versions of this software is licensed under the GNU GPLv3. See the `LICENSE` file for more details.

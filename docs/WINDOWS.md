# Filmocity on Windows and macOS

Filmocity is developed and tested on Linux; the code paths that differ per platform are deliberately few and are listed here so a first run on your workstation is quick to debug.

## Install & launch
- **Windows**: double-click `Install Filmocity.bat` once, then `Filmocity.bat`. Requires Python 3.10+ from python.org with *Add python.exe to PATH* ticked. FFmpeg is downloaded from the BtbN GitHub builds (`ffmpeg-master-latest-win64-gpl.zip`) into `Filmocity\bin\`; if the download is blocked, put `ffmpeg.exe` and `ffprobe.exe` in that folder yourself.
- **macOS**: `Install Filmocity.command` (right-click → Open the first time), then `Filmocity.command`. Homebrew FFmpeg is used if present, otherwise static builds from evermeet.cx are downloaded; Gatekeeper may ask you to allow them in System Settings → Privacy & Security.
- `Filmocity (no console).vbs` launches without a console window once the installer has run.
- Sharing on a LAN: `python3 launcher/bootstrap.py run --host 0.0.0.0 --token yourtoken` and open `http://<machine>:8787/?token=yourtoken` from another computer.
- Data lives in `%USERPROFILE%\filmocity_data` / `~/filmocity_data` (change with `--data`).

## What is platform-specific in the code
| Concern | Behaviour |
|---|---|
| Fonts | DejaVu Sans / Sans Bold / Mono Bold are **bundled** in `assets/fonts` and used by default, so titles, captions, generators and the Timecode effect render identically everywhere. Choosing a system font uses fontconfig (`fc-match`) where available; on Windows the family name is matched against `C:\Windows\Fonts` directly. The Fonts list in the Graphics panel is empty without fontconfig — type a family name. |
| Paths inside FFmpeg filters | Every path that enters a filter graph (text files, fonts, LUTs, stabilizer data) goes through `ffpath()`: forward slashes and escaped drive colons, so `C:\…` works. |
| Media Browser roots | Home folder plus drive letters C:–G: on Windows; `/` elsewhere. |
| Hardware encoders | Detected from the FFmpeg build at start (`/api/encoders`): NVENC (NVIDIA), QSV (Intel), AMF isn't probed yet, VideoToolbox (macOS), VAAPI (Linux). Software x264/x265 always work. |
| Auto-captions / transcript | `pip install faster-whisper` inside `Filmocity\.venv` (or run the installer with `--with-whisper`). First use downloads the model. |
| Warp Stabilizer | Needs an FFmpeg build with libvidstab — the BtbN gpl builds and Homebrew FFmpeg have it. |
| Browser | Chrome or Edge recommended (H.264 playback in the monitors). Safari works; Firefox plays only WebM/AV1 sources. |

## Known differences to expect
- Windows Defender may scan the 130 MB FFmpeg download; the first launch can take a minute longer.
- Path lengths: keep the Filmocity folder and data folder short (e.g. `C:\Filmocity`, `C:\filmocity_data`) — FFmpeg filter strings with very long paths are fragile.
- If a render fails on Windows, open the Export dialog's **Show FFmpeg command** and run it in a terminal — the error will name the filter.

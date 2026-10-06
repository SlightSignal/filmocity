# GETTING STARTED

## YOUR FIRST AD IN TEN MINUTES

Start with a finished sample, then make a short cut from your footage. Allow extra time for the first installation and rendering; the ten-minute path starts once Filmocity is running.

### 1. Install once, then launch

For an already installed native Windows build, open its Filmocity shortcut. Source setup is a separate path: use the approved source handoff and **standard, GIL-enabled CPython 3.13.16 Windows x64** for the pinned RC environment. The verified source repository is [Filmocity on GitHub](https://github.com/SlightSignal/filmocity); use its reviewed tools-only source. Source publication and native binary distribution have separate scopes. See [release readiness](RELEASE_READINESS.md) and [build/setup instructions](../packaging/README.md).

For source use, supply **FFmpeg and ffprobe** from a reviewed distribution in the source folder's `bin/` directory or on PATH, keeping its licenses and corresponding-source information. Setup no longer downloads rolling FFmpeg builds. Then run **Install Filmocity.bat** deliberately in a fresh source environment: it selects the standard 3.13 series, requires patch 16 or newer/x64, and installs the hash-locked Windows Python dependencies. Keep older environments and receipts as historical evidence. Open **Filmocity.bat** afterward. The source launcher opens the local browser editor; the native package uses its own window. Use the address reported by that launch rather than assuming port 8787, and retain the intended project library. No Filmocity account is needed. Keep a source launcher's window open while editing.

Open **Help → System Check** if something is missing. Automatic transcription is optional. Its source-only `--with-whisper` install is outside the reviewed dependency locks; start without it for the pinned environment.

### 2. See a finished cut

Choose **File → New Sample Project**. Filmocity generates the sample footage locally and builds a finished vertical sequence with transitions, titles, captions, and a music bed.

Click Play in the Program Monitor. Click a clip in the timeline and look at **Effect Controls**. The layout should feel familiar: source and effect controls above the Project panel, the Program Monitor beside them, and tracks below. You can inspect and edit the sample's individual clips.

### 3. Bring in your footage

Choose **File → New Project** for your own ad. In **Info**, fill in the **Brief**: objective, platform, audience, and constraints. Set your colors and font in **Brand kit**.

Drop your footage into Filmocity or import it through **Media Browser**. Add a music track. Thumbnails, waveforms, and playback proxies are prepared in the background.

### 4. Build the first assembly

Ctrl-click your shots in the order you want them used, then choose **File → New Reel from Footage**. Pick the music, enter your hook and call to action, and choose a look and caption preset.

The recipe assembles a hook, shots cut to the music, captions, and an end card. Its output is ordinary editable clips, so the next pass happens directly on the timeline.

### 5. Change the words and timing

Select the **Type tool**, then click text in the Program Monitor to rewrite it. Press Enter to commit.

To shorten a section, select its clip and type a new **Duration** in **Effect Controls**. Use Shift+Enter if you want following clips to ripple with the change. You can also drag a clip's head or tail in the timeline. Double-click a caption block to edit its words.

### 6. Review an agent's suggestions

In the header's **Agent** menu, choose **proposals only**. This controls a connected agent; it does not launch one. Give your agent the bundled `docs/AGENT_API.md` and `docs/PLAYBOOK.md`, and ask it to read your brief and propose a pacing pass with a reason for each change.

Open **Proposals**. Suggested edits appear as dashed ghosts on the timeline. Read the reason and click **Preview** before deciding. Choose a reason code—such as **pacing**, **story**, or **brand**—add a note when helpful, then **Accept** or **Reject** each suggestion.

Those decisions train Filmocity's local advisor. **Info → Learned preferences → Retrain** updates it; this does not retrain the connected AI model. Undo also covers agent edits.

### 7. Watch, export, check

Press **Enter** to render the preview and watch the exact result. Export with a platform preset, such as **Reels**.

When rendering finishes, read the **QA line** in **Render Queue**. Check loudness, true peak, duration, file size, and any flags from the built-in platform checks. Address relevant flags, watch the exported file, and deliver it.

# Scribble — Spec v0.5

A macOS menu bar utility: press a hotkey, talk, point and draw on screen, and get an
agent-ready bundle (annotated images + timestamped transcript with grounded references to
every drawing) on the clipboard. Fully local, free, no API keys.

Sections 1–10 describe the macOS app. Windows and Linux have a port with the same flows and
the same bundle; §11 says how it is built and where it differs.

## 1. Goals

- One hotkey to start, one to finish. No dialogs in the hot path.
- Two modes:
  - **Snapshot** — freeze the screen, draw on it while talking. Multi-shot: hide the
    overlay, navigate, grab the next shot, keep talking — all in one capture.
  - **Walkthrough** — record the screen while you keep using the computer; draw on top
    whenever you want (hold-to-draw or toggle).
- Every pointing mark (circle, arrow, box, highlight, pin) is numbered, burned into the
  image, *and* referenced in the transcript at the moment it was drawn, with its location
  in image pixel coordinates. Pen drawings are burned in and referenced, but not numbered.
- Output pastes usefully into any agent: Claude Code (terminal), claude.ai / ChatGPT web,
  Cursor / IDE chats.
- Transcription: WhisperKit (MIT, on-device, Apple Silicon optimized), every language of
  the multilingual Whisper models or auto-detect, word-level timestamps, custom vocabulary
  prompt.
- Bundle on the clipboard within ~2–4 s after finishing (model kept warm).

Non-goals: cloud anything, editing past captures, translating the UI or transcripts.

## 2. User flows

### 2.1 Snapshot (default `⌃⌥⌘S`)
1. Hotkey → screenshot of the display under the cursor (native resolution); mic starts.
2. Full-screen overlay shows the frozen frame; floating toolbar at the top (timer, mic
   level, tools, colors, undo, next-shot, finish, cancel).
3. Draw while talking. Keys: `P` pen · `A` arrow · `R` rectangle · `O` ellipse ·
   `H` highlighter · `N` pin · `1–6` colors · `⌘Z` undo · `⌫` delete last.
4. `Tab` → **next shot**: overlay hides, audio keeps recording, HUD says "navigate, then
   press ⌃⌥⌘S for the next shot". Hotkey → new screenshot, overlay returns.
5. `Enter` (or hotkey ⌃⌥⌘F) → finish. `Esc` → cancel & discard.
6. Focus returns to the app that was frontmost before capture so you can paste immediately.

### 2.2 Walkthrough (default `⌃⌥⌘R`)
1. Hotkey → records the display under the cursor + mic. HUD at top: rec dot, timer, mic
   level, draw-mode state, tools/colors, finish, cancel.
2. Transparent overlay above everything, click-through by default.
3. **Draw mode**: hold `⌃⌥` (configurable; polled via `CGEventSource.flagsState`, no
   permission needed) or toggle with `⌃⌥⌘D`. While on, the overlay takes the mouse.
4. Strokes fade after 3 s (configurable), pins after 5 s — laser-pointer feel.
5. Overlay is *in* the capture (annotations burned into frames); HUD/toasts are excluded
   via `SCContentFilter(excludingWindows:)` + `sharingType = .none`.
6. Same hotkey (or `⌃⌥⌘F`) → stop. Auto-stop at max length (default 10 min).

### 2.3 Dictation (default `⌃⌥⌘T`)
Voice only: HUD with timer and mic level; the same hotkey (or `⌃⌥⌘F`) stops. The transcript
goes to the clipboard as plain text; `audio.m4a` and `transcript.txt` stay in a capture
folder (pruned like captures, not listed under Recent captures).

### 2.4 Multiple displays
All connected displays take part (setting "Capture all displays", default on):
- **Snapshot** freezes every display at once; draw on any of them (the overlay under the
  pointer takes focus on click). Frames = the display the cursor was on + every display that
  was annotated, per round.
- **Walkthrough** records every display in parallel (one ScreenCaptureKit stream + HEVC file
  each) with a click-through overlay on each. The cursor's display over time is the user's
  focus: optional keyframes only come from the display the cursor was on (±1 s), a stay of
  ≥ 2 s on another display adds a "display switch" frame, start/end frames come from the
  first/last display. Annotations are always kept wherever they were drawn. The HUD follows
  the cursor. Frames are numbered chronologically across displays and labelled with the
  display name in `capture.md`; `events.json` lists each display's canvas size.

### 2.5 After finishing
HUD toast: "Transcribing…" → "Copied ✓ 9 frames · 3 annotations · 142 words". Menu bar
→ *Recent captures* → copy again in another format / reveal in Finder.

## 3. Annotations

| Tool | Description in transcript |
|---|---|
| Pen (freehand) | **drawing over** region; never reinterpreted as a shape, no number (see below) |
| Arrow | **arrow** from (x,y) **pointing at** (x,y) |
| Rectangle | **box around** (x1,y1)–(x2,y2) |
| Ellipse | **circled** region (x1,y1)–(x2,y2) |
| Highlighter | **highlighted** region |
| Pin | **pin** at (x,y) |

Each pointing mark gets a sequential number `#n` per capture, drawn as a badge next to it
(pins *are* the badge). In the transcript it appears as
`[#3 circled (412,230)–(640,310) in frame 2]`, inserted at the word being spoken when
drawing started. All coordinates are in the output image's pixel space, top-left origin.

Pen strokes are drawing (sketches, handwriting, multi-stroke marks): no badge, no number,
no shape guessing. Consecutive strokes on the same frame that start ≤ 1.5 s after the
previous one ends form one burst, referenced once at its start as
`[drawing (4 strokes) over (x1,y1)–(x2,y2) in frame 1]`; after a longer pause the next
stroke starts a new reference. A pen tap leaves a dot.

## 4. Walkthrough → keyframe storyboard

Agents don't take video, so a walkthrough becomes an adaptive set of keyframes.

**Live analysis** (ScreenCaptureKit stream, up to 10 fps, frames only on change): each
frame → 64×40 luminance thumbnail; mean-abs-diff vs previous.

**Candidates** (session time):
- **Settle** after an activity burst (consecutive changing frames) — scored by net change
  between before/after thumbnails; long bursts (scrolling, video) emit every 4 s.
- **Annotation burst end** (+150 ms, so the stroke is rendered) — *always kept*; bursts
  merge annotations drawn ≤ 1.5 s apart without a screen change, so one frame shows all.
- **Click** (global mouse monitor, no permission needed) → next settle or +500 ms.
- **Speech segment start**, low weight; **first** and **last** frame always.
- **Periodic fallback** every 15 s of no other candidate.

**Selection**: annotation frames first, then by score with ≥ 1.2 s spacing; an optional
frame is dropped if it looks identical to its chronological neighbours (so returning to an
earlier view is kept), and the end frame is dropped if it shows nothing new. Budget:
`max(6, duration / 5 s)`, capped by the *max frames* setting (default 30), shared across
displays.

**Detail crops**: on large displays (output < 0.8 px per point, e.g. a 3008 pt wide 4K/6K
screen at 1568 px) frames with annotations get an extra native-resolution crop around them. Frames are extracted from the recorded HEVC file
post-hoc with `AVAssetImageGenerator` (zero tolerance), downscaled to 1568 px long edge.

## 5. Bundle

`~/.scribble/captures/2026-11-02_14-03-12/` (no spaces in paths so terminals and
agents handle them; auto-pruned after 14 days)

```
capture.md         # what the agent reads
frame_01.png …     # annotated keyframes / shots
audio.m4a
recording.mp4      # walkthrough: video + mic audio (muxed in background);
                   # recording_displayN.mp4 per display when several were recorded
events.json        # full timeline: words, annotations (+geometry), clicks, frames
```

`capture.md`:

```markdown
# Screen capture · walkthrough · 0:47 · 2026-11-02 14:03

The user recorded their screen while talking. Numbered marks (#1, #2, …) in the images are
the user's own annotations; each appears in the transcript where it was drawn. Coordinates
are pixels in the referenced image (origin top-left).

Frames (1568×1018 px):
- frame 1 · 00:00 · /Users/…/frame_01.png
- frame 2 · 00:06 · /Users/…/frame_02.png

## Transcript

**[00:00 · frame 1]** So I'm on the settings page and when I click save [click at (812,440)]

**[00:06 · frame 2]** this error shows up [#1 circled (400,220)–(620,300)] even though…
```

## 6. Clipboard

Default **capture.md only** — one item: `public.utf8-plain-text` = `capture.md` (absolute
image paths) plus `public.file-url` of `capture.md`. Terminals paste the text; Electron/web
chats attach the `.md`. Agents with file access (Claude Code) read the frame paths from it,
so the images don't have to travel as attachments.

**capture.md + images** (for chats without file access) — the same item 0, then items 1…N:
`public.file-url` + `public.png` per image → browsers/Electron accept them as attachments.
Electron/web chats (Claude desktop, claude.ai, Cursor) drop the text when images are on the
pasteboard, which is why item 0 also carries the `.md` file.

*Copy as…* (menu, per recent capture): both of the above, **Text + paths**, **Images + text**
(images first), **Images only**, **Composite** (single tall image: frames + transcript
rendered beneath, for targets that take one image).

## 7. Architecture

Swift 6 toolchain (Swift 5 language mode), SwiftPM, macOS 15+, `LSUIElement` agent app,
assembled into a signed `.app` by `scripts/build-app.sh`.

- `ScribbleCore` (pure, unit-tested): models, shape classifier, keyframe selector,
  transcript interleaver, markdown composer, geometry mapping.
- `Scribble` (app): hotkeys (Carbon `RegisterEventHotKey`), `SessionController`
  state machine, `ScreenCapturer` (SCScreenshotManager + SCStream → AVAssetWriter HEVC
  + live thumbnails), `AudioRecorder` (AVAudioEngine → 16 kHz mono + m4a), overlay
  (`NSPanel` + custom `NSView`, shared `AnnotationRenderer` for on-screen and offline
  rendering), HUD (SwiftUI in `NSPanel`), `Transcriber` (WhisperKit, preloaded at launch,
  model in `~/.scribble/models`), `LiveTranscriber` (decodes WhisperKit's ≤30 s
  pieces while recording, see below), `BundleWriter`, `ClipboardWriter`, settings window,
  onboarding/permissions window, launch-at-login (`SMAppService`).

Clock: everything is in host time (`mach_absolute_time` seconds — same base as
`CACurrentMediaTime`, SCStream PTS, `AVAudioTime.hostTime`, `NSEvent.timestamp`) minus
session start.

### Transcribing while recording

WhisperKit transcribes audio longer than 30 s by cutting it into pieces (`VADAudioChunker`:
each piece ends in the middle of the longest silence in the second half of the 30 s after its
start) and decoding the pieces independently. A cut therefore depends only on audio that has
already been recorded once 30 s have passed since the previous cut. `LiveTranscriber` polls
the recorder, makes these cuts with the same chunker, decodes each piece with the options of
the one-pass path and shifts its times by the piece's offset. When the capture ends it
decodes what is left (nothing if under a second remains, as the chunker does) and converts
all results together. If nothing was cut (a capture under 30 s) or a piece fails, the whole
recording is transcribed in one pass instead.

The result equals the one-pass transcript. With temperature retries switched off the two
were identical word for word and time for time on 19 real captures of 17 s to 8.5 min
(`--eval-live --deterministic`, and with `--realtime` for the class itself, fed at 8× speed).
With retries on, Whisper is not deterministic, and the two differ no more than two one-pass
runs do.

WhisperKit's first-token check (`firstTokenLogProbThreshold`) is off. It abandons a piece
whose first token is unlikely, such as a quiet or hesitant start, and retries with sampling.
On real captures a piece that tripped it came back empty in about half the runs: up to 30 s
of speech missing in one run and present in the next. Noise and silence still give no text
without it; the no-speech, compression-ratio and average-log-probability checks remain.

## 8. Permissions

| Permission | For | If denied |
|---|---|---|
| Screen Recording | both modes | can't capture → onboarding window explains + opens Settings |
| Microphone | voice | capture continues without transcript |

No Accessibility / Input Monitoring needed (hotkeys via Carbon, modifier polling via
`CGEventSource`, mouse-down global monitor).

Signed with the stable Apple Development identity so TCC grants survive rebuilds.

## 9. Settings

Hotkeys (recorder) · transcription language (default English; Auto-detect or any of the
100 Whisper languages) · Whisper model (default
`openai_whisper-large-v3-v20240930_turbo_632MB`, alternatives small / base, multilingual, and
small.en / base.en, English only) · compute (Auto / GPU / Neural Engine) · custom vocabulary · max frames · image long edge ·
max walkthrough length · fade duration · draw-mode modifier · keep clean originals ·
default clipboard format · retention days · sounds · launch at login.

**Language and model.** The setting applies to Snapshot, Walkthrough, Dictation and the dev
CLI, which reads the app's settings even as a bare binary. An English-only model with any
other selection, including Auto-detect, still transcribes but warns: inline in Settings, and
as the capture's note (toast and an italic line in `capture.md`). Cantonese is decoded as
Chinese on every model: Whisper's own token gave an empty transcript, and Auto-detect does the
same. Auto-detect is Whisper's own, per audio chunk, without temperature retries: a retry
detects the language again and can flip it.

**Unspaced scripts.** Whisper's word timing splits Chinese, Japanese, Thai and similar
scripts into fragments without spaces. They are joined without a separator, combining marks
stay on their character, and a mark reference is set off with spaces.

**Foreign terms.** Whisper may respell words from another language (`Save` → `сейф`). Listing
them in the custom vocabulary keeps them in most cases; it is a bias, not a guarantee.
`PatchedSegmentSeeker` keeps word timings right with a prompt: WhisperKit 1.1.1 reads
alignment rows offset by the prompt length. It also resumes a window cut off at the 224-token
limit where the text stopped; WhisperKit would skip the rest. With a prompt Whisper may skip segment
timestamps, so the first word of a sentence can swallow the pause before it, and a mark drawn
in that pause moves one word on.

## 10. Risks

- Mixed pasteboards are interpreted differently per app → *Copy as…* formats.
- First Neural Engine compile of the large encoder takes ~10 min on an M1 Pro (cached
  afterwards, invalidated when the binary changes). **Auto** compute loads a GPU-encoder build
  first (~15 s cold, ~3 s warm; ~0.25× real time) and swaps in the Neural Engine build
  (~0.08× real time) once it has compiled in the background.
- Overlay vs. fullscreen apps / Stage Manager → `canJoinAllSpaces` + `fullScreenAuxiliary`.
- Displays connected/disconnected mid-capture are not handled (the display list is fixed at
  start).
- Recordings can contain secrets → local only, auto-pruned.
- Speech in a second language can vanish when one language dominates a 30 s window: Whisper
  may jump its first timestamp past it, with Auto-detect too. WhisperKit 1.1.1 ignores
  `maxInitialTimestamp`, the option meant to prevent this.
- Auto-detect can differ from a fixed language around silence, where Whisper may also invent
  a phrase ("Thank you.", "Продолжение следует…").

## 11. Windows and Linux

A second implementation in `python/` (Python 3.9+, Qt 6 through PySide6), not a shared
codebase: the macOS app is AppKit, ScreenCaptureKit and Core ML glue around a small core, and
none of that glue exists elsewhere. What is shared is the behaviour. `scribble.core` is
a line-by-line port of `ScribbleCore`, its tests are the Swift tests, and a parity test
feeds a capture's `events.json` from the macOS app through the port and expects the same
`capture.md`, byte for byte.

| | macOS | Windows / Linux |
|---|---|---|
| UI | AppKit + SwiftUI | Qt widgets (`ui/`) |
| Screen | ScreenCaptureKit | `mss`: GDI on Windows, X11 `GetImage` on Linux |
| Recording | HEVC via AVAssetWriter | H.264 via PyAV, real timestamps (variable frame rate) |
| Audio | AVAudioEngine | PortAudio (`sounddevice`), AAC via PyAV |
| Transcription | WhisperKit (Core ML) | faster-whisper (CTranslate2): CPU, or CUDA |
| Pieces of long audio | WhisperKit's `VADAudioChunker` | `core/chunks.py` |
| Hotkeys | Carbon `RegisterEventHotKey` | `RegisterHotKey` / X11 passive key grabs |
| Rendering | Core Graphics | QPainter (same geometry, shadows approximated) |
| Settings | UserDefaults | `~/.scribble/settings.json` |

Everything the app needs from the OS beyond Qt sits behind `platform.Platform`: global
hotkeys, pointer and modifier state while other apps have focus, click-through, keeping a
window out of screen capture, handing keyboard focus back, launch at login.

Differences that follow from the platforms:

- **Shortcuts** default to `Ctrl+Alt+Shift` + `S` / `W` / `T` / `F` / `D` (Walkthrough is `W`:
  GNOME reserves `Ctrl+Alt+Shift+R`). A shortcut another program holds is reported in the
  tray menu and in Settings instead of silently not working.
- **Triggers.** Every action is also a command (`scribble snapshot`, `walkthrough`,
  `dictation`, `finish`, `toggle-draw`, `cancel`) that the running instance executes, so a
  desktop's own shortcut settings can drive the app.
- **Clipboard.** One item with several formats instead of several items: the markdown as
  text plus a file list (`CF_HDROP`; `text/uri-list` and `x-special/gnome-copied-files`).
- **Clicks and hold-to-draw** come from polling pointer and modifier state 60 times a second
  (`GetAsyncKeyState`, `XQueryPointer`): no keyboard or mouse hooks.
- **Recording.** A thread per display grabs about ten frames a second; a frame identical to
  the previous one is skipped, the others are encoded with their capture time and sampled as
  thumbnails. Screen grabs do not contain the pointer, so it is drawn into the frames.
- **Audio.** PortAudio delivers the microphone in blocks of a tenth of a second. What a
  driver reports about timing is only used where it is plausible: buffer times are kept
  inside the recording's lifetime, and gaps longer than five seconds are not filled with
  silence. Sound cues are WAV files played by the system, not through PortAudio.
- **The toolbar in recordings.** Windows hides it from capture (`WDA_EXCLUDEFROMCAPTURE`).
  X11 cannot, so it is visible in walkthrough keyframes and `capture.md` says what it is.
- **No compositor (some X11 setups).** A translucent overlay would be opaque, so the
  Walkthrough overlay is cut to the shape of the marks and grabs the pointer while drawing.
- **Transcription model.** Auto picks Large v3 Turbo on a working NVIDIA GPU. On the CPU it
  picks Small: the English-only one while the language setting is English, the multilingual
  one otherwise (each is downloaded when first needed). Small, Base and their English-only
  variants can also be chosen; language setting, warning and vocabulary hint work as in §9.
  CUDA is probed in a child process first: a broken CUDA setup aborts the process it is
  loaded in instead of raising.
- **Transcribing while recording.** Same design as on macOS, with its own cuts.
  faster-whisper has no chunker to reuse, so `core/chunks.py` cuts a recording into pieces
  of at most 30 s, each ending in the middle of the quietest 0.3 s in the second half of the
  30 s after its start, and `Transcriber.transcribe` decodes every piece on its own
  (`convert` drops what Whisper repeats in a piece's padding, as on macOS). A cut depends
  only on audio already recorded, so `live.LiveTranscriber` makes the same cuts on a worker
  thread during the recording, reading the microphone through `AudioRecorder.samples`
  (resampled as one stream, so what it reads is the beginning of the final audio), and
  decodes each piece once it is complete. Finishing decodes the rest. One piece is decoded
  at a time; on a CPU too slow to keep up, the pieces wait and finishing catches up. With
  temperature retries off, the transcript is identical to transcribing the finished
  recording in one go (`--eval-live --deterministic`, with `--realtime` for the class
  itself; a CI test does the same with a real model). Recordings up to 30 s are one piece
  and decoded exactly as before.
- **Wayland** does not let an app watch the keyboard, the pointer or the screen. The app runs
  through XWayland in a reduced mode: Snapshot (screenshot from `grim`, the desktop portal,
  `spectacle` or `gnome-screenshot`) and Dictation, started from the tray or from desktop
  shortcuts bound to the triggers. No Walkthrough, no hold-to-draw.

Tests: `pytest` runs the core, the pipeline and the whole capture state machine headless
(fake screen and microphone, real overlays). `pytest --desktop` drives a real session with
synthetic input; CI runs it on Windows and under Xvfb, with and without a compositor.

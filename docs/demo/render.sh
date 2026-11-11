#!/bin/zsh
# Regenerates the README demo capture: renders screen.html as a @2x screenshot, speaks
# narration.txt, and runs the real Snapshot pipeline (WhisperKit + renderer) with marks.json.
set -euo pipefail
cd "${0:A:h}"
root=../..
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=1512,982 --screenshot=$PWD/screen.png "file://$PWD/screen.html" 2>/dev/null
say -v Samantha -r 185 -o narration.aiff -f narration.txt
(cd $root && swift build -c release >/dev/null)
# Fixed settings: the CLI otherwise reads the app's.
$root/.build/release/VoiceAnnotate --demo-snapshot narration.aiff screen.png marks.json \
  --language en --model openai_whisper-large-v3-v20240930_turbo_632MB --vocabulary ""
# Copy the newest capture's frame into the README images and render the hero. The hero's
# transcript text is written into hero.html by hand: update it if the narration changes.
latest=$(ls -td ~/.voice-annotate/captures/*/ | head -1)
cp "$latest/frame_01.png" ../images/snapshot-frame.png
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --allow-file-access-from-files \
  --force-device-scale-factor=2 --window-size=1600,700 --screenshot=$PWD/../images/hero.png "file://$PWD/hero.html" 2>/dev/null
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=1260,470 --screenshot=$PWD/../images/pipeline.png "file://$PWD/pipeline.html" 2>/dev/null
rm -f screen.png narration.aiff

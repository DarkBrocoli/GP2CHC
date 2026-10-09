# GP2CHC

*[Version française](README.fr.md)*

Turns the **drum track** of a Guitar Pro tab into a **Clone Hero** chart (`notes.mid` + `song.ini`): pro drums, Expert difficulty, synced to the actual recording.

**Drums only.** Guitar, bass and vocals are not converted.

**It does not replace a charter.** The result always needs a pass in Moonscraper (fixing notes, checking playability), but the bulk of the work is done in seconds.

## Download

Windows: download `GP2CHC-windows.zip` from the [Releases](../../releases) page, unzip it anywhere and run `GP2CHC.exe`. Nothing to install and no internet connection needed: Python, numpy, ffmpeg, Demucs, PyTorch and the separation model are bundled. `gp2chc-cli.exe` is the command-line version.

The executable is not signed, so Windows SmartScreen may warn on first launch: "More info" → "Run anyway". You can also build it yourself (see [Building](#building-the-windows-executable)) or run from source.

## Features

- **Pro drums chart on Expert**: toms and cymbals told apart (tom markers), ghost notes and accents, sections, repeats and alternate endings unrolled, tempo and time signature changes.
- **Automatic sync to the song**, from the drum stems (`drums_*.ogg/opus`) or from a full mix, whose drums are isolated with [Demucs](https://github.com/adefossez/demucs):
  - finds where the drums start, even late in the song;
  - aligns the structure bar by bar over the whole song, inserting empty bars where the recording has more than the tab and removing tab bars the recording does not play;
  - one tempo and one time signature per bar (or one tempo per beat as an option), so the grid follows the drummer.
- **Drum stem from a full mix**: when only a full mix is given, the isolated drums are saved as `drums.opus` and the song file keeps everything else (mix minus drums), so Clone Hero can mute the drums on missed notes without playing them twice. The original full mix is kept in `gp2chc_original_audio/full_mix/`, and later conversions reuse `drums.opus` directly (no new separation).
- **3 s of silence at the start** of the chart and of the song's audio files, so the first notes don't catch you off guard. The original audio is kept in `gp2chc_original_audio/`, and running again never adds the silence twice.
- **Playability rules**: never more than 2 pads at once (the kick comes on top); the pedal hi-hat (yellow cymbal by default) is dropped when it falls with a tom, or with the snare during a fill.
- **song.ini** generated or merged with an existing one (`song_length` and `preview_start_time` follow the added silence).
- **English and French** interface, following the system language by default.

## Using it

1. Run `GP2CHC.exe` (or `python -m gp2chc` from source, or double-click `GP2CHC.pyw`).
2. Choose the Guitar Pro tab.
3. Click **Song folder...** and pick the song's Clone Hero folder: drum stems and `song.ini` are detected, and the chart is written there. Or choose the audio yourself: **Drums only** or **Full mix**.
4. Click **Convert**. The log shows where the first drum note was placed, the tempo range, bars inserted or removed, and how many notes land on an audio onset.

If the start found is wrong (stick count-in, noise before the song...), enter the time of the first drum note in **First drum note at (s)** and convert again.

Other tabs: **Advanced** (Guitar Pro instrument → lane mapping, tempo per beat, extra `song.ini` lines), **Modules** (numpy / ffmpeg / Demucs status when running from source), **About**.

## Command line

```
gp2chc-cli.exe tab.gp --audio drums_1.ogg drums_2.ogg drums_3.ogg -o "My Song"
python -m gp2chc tab.gp --mix song.ogg
python -m gp2chc tab.gp --list-tracks
```

| Option | Effect |
|--------|--------|
| `--audio F1 F2...` | drum-only audio used to sync the chart |
| `--mix F1 F2...` | full mix: drums are isolated with Demucs, then used to sync |
| `--drums-start S` | force the time (s, silence included) of the first drum note |
| `--lead-in S` | silence added at the start of the chart and audio (default 3, `0` restores the original audio) |
| `--per-beat` | one tempo per beat instead of one per bar |
| `--track N` | drum track by number or name (default: first drum track) |
| `--max-hands N` | max pads at once, kick not counted (default 2, 0 = unlimited) |
| `--map file.json` | override the instrument → lane mapping, e.g. `{"56": "yellow-cymbal", "44": "none"}` |
| `--ini-template FILE` / `--set key=value` | reuse an existing `song.ini` / force a value |
| `--offset-ms N` | manual offset when no audio is given |
| `--no-dynamics` | no ghost notes / accents |
| `--lang fr\|en\|auto` | message language |

## Default mapping

| Guitar Pro (GM) | Clone Hero |
|-----------------|------------|
| Kick (35, 36) | kick |
| Snare, side stick (38, 37, 40) | red |
| Hi-hat closed / open / pedal (42, 46, 44) | yellow cymbal |
| High toms (50, 48) | yellow tom |
| Mid / low toms (47, 45) | blue tom |
| Floor toms (43, 41) | green tom |
| Ride, ride bell (51, 59, 53) | blue cymbal |
| Crashes, splash, china (49, 57, 55, 52) | green cymbal |

Other instruments (cowbell, tambourine...) are ignored and listed in the log.

## Accuracy

Measured on a Rock Band multitrack used during development, with an onset detector independent from the one used for syncing: median gap of 3.7 ms between a note and the matching audio onset, 93 % of notes within 20 ms (one tempo per bar; 2.6 ms with one tempo per beat). The structure alignment was checked by projecting the tab's vocal line with the same tempo map onto the vocal stem (0.07 s off at the first verse), and on simulated cases (drums delayed by 50 s, intro lengthened by 12 bars, 2 bars removed or duplicated in the tab): 98 to 100 % of notes land where expected. Real-world results depend on how closely the tab follows the recording.

## Limitations

- Drums only.
- Expert only: no Easy / Medium / Hard.
- No star power, no drum fills / activation lanes, no rolls, no double kick (Expert+).
- Grace notes (flams) are ignored.
- Guitar Pro 7/8 (`.gp`) is tested; Guitar Pro 6 (`.gpx`) is implemented from the format description but untested on real files. For `.gp5` and older, re-save as `.gp` in Guitar Pro.
- Syncing tolerates extra or missing bars, but not sections played in a different order than in the tab.
- Rock Band folders: `song.opus` does not contain the drums, so it cannot be used for syncing.

## How it was made

GP2CHC was developed with the help of **Claude Code** (Anthropic's AI coding assistant). Dark_Brocoli defined the features and the playability rules, tested the charts in Clone Hero and reported what needed to change (sync problems, pedal hi-hat in fills, three-pad chords, lead-in silence...); most of the code was written by Claude under that direction. The accuracy figures above come from measurements run during development.

## Running from source

Python 3.10+. Converting needs nothing beyond the standard library. Syncing to audio needs `numpy` and `ffmpeg` (in the PATH); isolating drums from a full mix also needs `demucs` (large: it installs PyTorch).

```
pip install -r requirements.txt
python -m gp2chc
```

Tests: `pip install -r requirements-dev.txt`, then `python -m pytest`. Tests that use a real tab are skipped when the file is absent (no tabs or audio are included in this repository).

## Building the Windows executable

```
pip install -r requirements.txt -r requirements-dev.txt
python packaging/build.py
```

It needs a static `ffmpeg.exe` (in the PATH, from Chocolatey, or given with the `FFMPEG` environment variable) and downloads the Demucs model if needed. Output: `dist/GP2CHC/` and `dist/GP2CHC-windows.zip`.

## License

MIT, see [LICENSE](LICENSE). The Windows executable also bundles FFmpeg (GPL v3, license included next to it), Demucs (MIT), PyTorch (BSD), NumPy (BSD) and Python (PSF).

Created by Dark_Brocoli · dark.brocoli.ttv@gmail.com

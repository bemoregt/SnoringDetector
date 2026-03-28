# Sleep Snoring Detector

A macOS desktop app that records microphone audio during sleep, builds a spectrogram baseline, and automatically saves only the strongest snoring segments using an adaptive threshold.

---

## How It Works

```
Recording (44.1 kHz)
       │
       ▼
  1-minute segments
       │
  Downsample → 8 kHz
       │
  STFT → log-magnitude spectrogram  (65 freq bins × 300 time frames)
       │
  ┌────┴──────────────────────┐
  │  Phase 0 · Baseline       │  first N minutes (1–5, selectable)
  │  Accumulate spectrograms  │
  │  → pixel-wise median      │
  └────┬──────────────────────┘
       │  baseline image ready
  ┌────┴──────────────────────┐
  │  Phase 1 · Monitoring     │
  │  normalized L2 distance   │  ‖img/‖img‖ − base/‖base‖‖
  │  vs. baseline             │
  └────┬──────────────────────┘
       │
  Adaptive threshold
  = 99th percentile of all distances so far
  → only top 1 % (adjustable) triggers a save
       │
       ▼
  Save segment as .wav
```

### Key Concepts

| Term | Description |
|------|-------------|
| **Spectrogram** | Short-Time Fourier Transform (STFT) magnitude in dB — 2D image of frequency vs. time |
| **Baseline** | Pixel-wise median of the first N spectrogram images — represents quiet/normal breathing |
| **Distance** | Normalized L2 distance between the current spectrogram and the baseline |
| **Adaptive threshold** | 99th percentile of all distances accumulated so far; updates every minute so only the true top-1 % of events are flagged |

---

## Requirements

- macOS 12 or later
- Python 3.11+ (for running from source)
- Microphone access permission

### Python dependencies (source)

```
PySide6>=6.5.0
numpy>=1.24.0
scipy>=1.10.0
sounddevice>=0.4.6
soundfile>=0.12.1
matplotlib>=3.7.0
```

Install with:

```bash
pip install -r requirements.txt
```

---

## Installation

### Option A — Pre-built app (recommended)

1. Open `dist/SnoringDetector.dmg`
2. Drag **SnoringDetector.app** to your Applications folder
3. On first launch macOS Gatekeeper may block the app (no Apple notarization)
   - Go to **System Settings → Privacy & Security** and click **Open Anyway**, or
   - Run once in Terminal: `xattr -cr /Applications/SnoringDetector.app`

### Option B — Run from source

```bash
git clone <repo>
cd 0328_CoGolYee
pip install -r requirements.txt
python snoring_detector.py
```

### Option C — Rebuild the binary yourself

```bash
pip install pyinstaller
pyinstaller snoring_detector.spec --noconfirm
# Output: dist/SnoringDetector.app
```

---

## Usage

1. **Select Folder** — choose where `.wav` files will be saved (default: `~/Desktop/snoring_recordings`)
2. **Baseline** spinner — set how many minutes to use for baseline collection (1–5 min)
3. **Top % spinner** — set the detection sensitivity; `1 %` saves only the strongest 1 % of snoring events
4. Click **● Start Recording** before going to sleep
5. Click **■ Stop** in the morning

### GUI panels

| Panel | Description |
|-------|-------------|
| **Current Segment** | Spectrogram of the most recent 1-minute window |
| **Baseline (Median)** | Median spectrogram built during the baseline phase |
| **Distance History** | Bar chart of per-segment distances; red = saved, blue = normal; dashed line = current threshold |
| **Log** | Timestamped event log |

---

## Output files

Saved files are named:

```
snoring_0042_20260328_023817_d0.3821.wav
         ↑       ↑               ↑
    segment#   timestamp      distance
```

Audio is saved at the original **44.1 kHz** sample rate as uncompressed PCM WAV.

---

## Parameters

| Constant | Default | Description |
|----------|---------|-------------|
| `SAMPLE_RATE` | 44100 Hz | Recording sample rate |
| `TARGET_SR` | 8000 Hz | Downsample rate for spectrogram |
| `SEGMENT_SECS` | 60 s | Duration of each analysis window |
| `FFT_SIZE` | 128 | STFT window size |
| `MAX_FRAMES` | 300 | Max time frames per spectrogram |
| `DEFAULT_BASELINE` | 5 min | Default baseline collection duration |
| `DEFAULT_TOP_PCT` | 1.0 % | Default detection percentile |

---

## Project structure

```
0328_CoGolYee/
├── snoring_detector.py   # main application
├── snoring_detector.spec # PyInstaller build spec
├── requirements.txt      # Python dependencies
├── setup.py              # py2app setup (legacy, unused)
└── dist/
    ├── SnoringDetector.app
    └── SnoringDetector.dmg
```

---

## License

MIT

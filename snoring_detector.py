#!/usr/bin/env python3
"""
Sleep Snoring Detector
──────────────────────
• Records audio from iMac microphone during sleep
• Computes Spectrogram images in 1-minute segments
• Builds a median Baseline image from the initial N minutes
• Compares each subsequent segment to the Baseline
• Saves audio segments whose distance exceeds the adaptive threshold
"""

import sys, queue, threading
from math import gcd
from datetime import datetime
from pathlib import Path

# PySide6 must be imported before matplotlib to avoid QtGui conflicts
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QProgressBar, QTextEdit, QGroupBox,
    QDoubleSpinBox, QFileDialog, QSplitter, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QObject

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import resample_poly

import matplotlib
matplotlib.use('QtAgg')
try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
except ImportError:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtGui import QFont


# ═══════════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════════
SAMPLE_RATE       = 44100        # recording sample rate
CHANNELS          = 1
TARGET_SR         = 8000         # downsample rate for spectrogram processing
SEGMENT_SECS      = 60           # 1-minute segments
FFT_SIZE          = 128          # spectrogram FFT size
MAX_FRAMES        = 300          # max frames for memory efficiency
DEFAULT_TOP_PCT   = 1.0          # default top 1% detection
DEFAULT_BASELINE  = 5            # default baseline collection minutes (1–5)

# Windows 2000 classic palette
W2K_BG        = '#d4d0c8'   # classic gray background
W2K_WHITE     = '#ffffff'
W2K_DARK      = '#808080'   # shadow
W2K_DARKER    = '#404040'
W2K_HIGHLIGHT = '#ffffff'   # highlight (top/left border)
W2K_SHADOW    = '#808080'   # shadow (right/bottom border)
W2K_DKSHADOW  = '#404040'   # dark shadow
W2K_TITLEBAR  = '#000080'   # navy title bar
W2K_TITLEFG   = '#ffffff'
W2K_LOGBG     = '#ffffff'
W2K_LOGFG     = '#000080'

# for matplotlib (light background)
DARK_BG   = W2K_BG
PANEL_BG  = W2K_BG


# ═══════════════════════════════════════════════════════════════════════════════
#  Signal Processing
# ═══════════════════════════════════════════════════════════════════════════════

def resample_audio(audio: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    if from_sr == to_sr:
        return audio.astype(np.float32)
    g = gcd(from_sr, to_sr)
    return resample_poly(audio, to_sr // g, from_sr // g).astype(np.float32)


def compute_spectrogram(audio: np.ndarray, fft_size: int = FFT_SIZE) -> np.ndarray:
    """
    Compute a log-magnitude spectrogram from a 1-minute audio segment.
    Returns a (n_freq, MAX_FRAMES) float32 array in dB scale,
    where n_freq = fft_size // 2 + 1.
    """
    N   = fft_size
    hop = N // 2

    if len(audio) < N:
        return np.zeros((N // 2 + 1, MAX_FRAMES), dtype=np.float32)

    win    = np.hanning(N).astype(np.float32)
    starts = np.arange(0, len(audio) - N + 1, hop)

    # uniformly subsample to exactly MAX_FRAMES columns
    if len(starts) > MAX_FRAMES:
        idx    = np.linspace(0, len(starts) - 1, MAX_FRAMES, dtype=int)
        starts = starts[idx]

    frames = np.stack([audio[s:s + N] * win for s in starts])  # (F, N)
    mag    = np.abs(np.fft.rfft(frames, N, axis=1))             # (F, n_freq)
    db     = 20.0 * np.log10(mag.T + 1e-10)                    # (n_freq, F) in dB
    return db.astype(np.float32)


def normalized_l2_distance(img: np.ndarray, baseline: np.ndarray) -> float:
    """Normalized L2 distance between two spectrogram images."""
    f = img.ravel().astype(np.float64)
    b = baseline.ravel().astype(np.float64)
    nf, nb = np.linalg.norm(f), np.linalg.norm(b)
    if nf == 0 or nb == 0:
        return 0.0
    return float(np.linalg.norm(f / nf - b / nb))


def adaptive_threshold(distances: list, top_pct: float):
    """
    Returns the (100 - top_pct) percentile of all distances as the threshold,
    so only the top top_pct% of segments exceed it.
    Requires at least 10 samples.
    """
    if len(distances) < 10:
        return None
    return float(np.percentile(distances, 100.0 - top_pct))


# ═══════════════════════════════════════════════════════════════════════════════
#  Qt Signals Container
# ═══════════════════════════════════════════════════════════════════════════════

class Signals(QObject):
    log               = Signal(str)
    bispec_updated    = Signal(object, int)       # (ndarray, seg_num)
    baseline_ready    = Signal(object)            # ndarray – median baseline image
    baseline_progress = Signal(int)               # 0 .. baseline_segs
    distance_ready    = Signal(float, int, bool)  # dist, seg_num, saved
    threshold_ready   = Signal(float)
    phase_changed     = Signal(int)               # 0=baseline  1=monitor
    done              = Signal()
    file_saved        = Signal(str)


# ═══════════════════════════════════════════════════════════════════════════════
#  Recording + Processing Thread
# ═══════════════════════════════════════════════════════════════════════════════

class RecordingWorker(QThread):
    def __init__(self, save_dir: Path, top_pct: float, baseline_segs: int,
                 signals: Signals):
        super().__init__()
        self.save_dir      = save_dir
        self.top_pct       = top_pct
        self.baseline_segs = baseline_segs   # how many 1-min segments to use
        self.signals       = signals
        self._stop         = threading.Event()
        self._q: queue.Queue = queue.Queue()

        self.seg_samples    = SAMPLE_RATE * SEGMENT_SECS
        self.baseline_imgs  = []
        self.baseline       = None          # completed median baseline image
        self.distances: list[float] = []
        self.threshold      = None
        self.seg_num        = 0

    def request_stop(self):
        self._stop.set()

    def _audio_cb(self, indata, frames, time_info, status):
        if status:
            self.signals.log.emit(f"Audio status: {status}")
        if not self._stop.is_set():
            self._q.put(indata[:, 0].copy())

    def run(self):
        self.signals.log.emit("Microphone stream started...")
        buf: list = []

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype='float32',
                blocksize=4096,
                callback=self._audio_cb,
            ):
                while not self._stop.is_set():
                    try:
                        chunk = self._q.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    buf.extend(chunk.tolist())

                    while len(buf) >= self.seg_samples:
                        seg    = np.array(buf[:self.seg_samples], dtype=np.float32)
                        buf    = buf[self.seg_samples:]
                        self.seg_num += 1
                        self._process(seg, self.seg_num)

        except Exception as e:
            self.signals.log.emit(f"[Error] {e}")
        finally:
            self.signals.done.emit()

    def _process(self, audio: np.ndarray, seg_num: int):
        self.signals.log.emit(f"Analyzing segment {seg_num}...")

        audio_ds = resample_audio(audio, SAMPLE_RATE, TARGET_SR)
        bispec   = compute_spectrogram(audio_ds)
        self.signals.bispec_updated.emit(bispec, seg_num)

        # ── Phase 0: Baseline collection ────────────────────────────────────
        if self.baseline is None:
            self.baseline_imgs.append(bispec)
            n = len(self.baseline_imgs)
            self.signals.baseline_progress.emit(n)
            self.signals.log.emit(
                f"  Collecting baseline {n}/{self.baseline_segs} min"
            )

            if n >= self.baseline_segs:
                stack         = np.stack(self.baseline_imgs, axis=0)
                self.baseline = np.median(stack, axis=0).astype(np.float32)
                self.signals.baseline_ready.emit(self.baseline)
                self.signals.phase_changed.emit(1)
                self.signals.log.emit("★ Baseline complete! Monitoring started.")
            return

        # ── Phase 1: Monitoring ─────────────────────────────────────────────
        dist = normalized_l2_distance(bispec, self.baseline)
        self.distances.append(dist)

        self.threshold = adaptive_threshold(self.distances, self.top_pct)
        if self.threshold is not None:
            self.signals.threshold_ready.emit(self.threshold)

        should_save = (self.threshold is not None and dist > self.threshold)
        self.signals.distance_ready.emit(dist, seg_num, should_save)

        thr_str = f"{self.threshold:.4f}" if self.threshold else "calculating"
        self.signals.log.emit(
            f"  dist={dist:.4f}  threshold={thr_str}  "
            f"{'→ Strong snoring detected! Saving.' if should_save else '(normal)'}"
        )

        if should_save:
            self._save_audio(audio, seg_num, dist)

    def _save_audio(self, audio: np.ndarray, seg_num: int, dist: float):
        ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
        name = f"snoring_{seg_num:04d}_{ts}_d{dist:.4f}.wav"
        path = self.save_dir / name
        try:
            sf.write(str(path), audio, SAMPLE_RATE)
            self.signals.file_saved.emit(str(path))
        except Exception as e:
            self.signals.log.emit(f"Save error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Matplotlib Canvases
# ═══════════════════════════════════════════════════════════════════════════════

class SpectroCanvas(FigureCanvas):
    """Spectrogram display canvas"""

    def __init__(self, label: str = "", parent=None):
        fig = Figure(figsize=(3, 3), dpi=80, facecolor=W2K_BG)
        super().__init__(fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        n_freq = FFT_SIZE // 2 + 1
        # extent: [xmin, xmax, ymin, ymax] = [time_s, freq_hz]
        ext = [0, SEGMENT_SECS, 0, TARGET_SR // 2]

        ax = fig.add_subplot(111)
        ax.set_facecolor(W2K_WHITE)
        self._im = ax.imshow(
            np.zeros((n_freq, MAX_FRAMES)), origin='lower', cmap='viridis',
            aspect='auto', extent=ext
        )
        ax.set_title(label, color='#000080', fontsize=9, pad=3,
                     fontfamily='Tahoma', fontweight='bold')
        ax.set_xlabel('Time (s)', color='#404040', fontsize=7)
        ax.set_ylabel('Freq (Hz)', color='#404040', fontsize=7)
        ax.tick_params(colors='#404040', labelsize=6)
        for sp in ax.spines.values():
            sp.set_edgecolor('#808080')
        cb = fig.colorbar(self._im, ax=ax, fraction=0.046, pad=0.04)
        cb.set_label('dB', color='#404040', fontsize=6)
        cb.ax.tick_params(labelcolor='#404040', labelsize=6)
        fig.tight_layout(pad=0.6)

    def update_data(self, arr: np.ndarray):
        self._im.set_data(arr)
        self._im.autoscale()   # auto clim from data range
        self.draw_idle()


class DistChart(FigureCanvas):
    """Distance history bar chart"""

    def __init__(self, parent=None):
        fig = Figure(figsize=(5, 2.5), dpi=80, facecolor=W2K_BG)
        super().__init__(fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.ax      = fig.add_subplot(111)
        self._dists  : list[float] = []
        self._saved  : list[bool]  = []
        self._thr    : float | None = None
        self._init_axes()
        fig.tight_layout(pad=0.6)

    def _init_axes(self):
        ax = self.ax
        ax.set_facecolor(W2K_WHITE)
        for sp in ax.spines.values():
            sp.set_edgecolor('#808080')
        ax.tick_params(colors='#404040', labelsize=6)
        ax.set_xlabel('Segment', color='#404040', fontsize=7)
        ax.set_ylabel('Distance', color='#404040', fontsize=7)
        ax.set_title('Distance History  (red=saved, blue=normal)', color='#000080',
                     fontsize=8, fontfamily='Tahoma', fontweight='bold')

    def add(self, dist: float, saved: bool, thr: float | None = None):
        self._dists.append(dist)
        self._saved.append(saved)
        if thr is not None:
            self._thr = thr
        self._redraw()

    def _redraw(self):
        ax = self.ax
        ax.clear()
        self._init_axes()

        x      = np.arange(len(self._dists))
        colors = ['#cc0000' if s else '#000080' for s in self._saved]
        ax.bar(x, self._dists, color=colors, width=0.8, alpha=0.9)

        if self._thr is not None:
            ax.axhline(self._thr, color='#804000', lw=1.5, ls='--',
                       label=f'Threshold {self._thr:.4f}')
            ax.legend(fontsize=7, facecolor=W2K_BG, labelcolor='#000000',
                      loc='upper left', framealpha=1.0,
                      edgecolor='#808080')

        self.figure.tight_layout(pad=0.6)
        self.draw_idle()


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sleep Snoring Detector")
        self.resize(1100, 730)

        self._worker  : RecordingWorker | None = None
        self._signals : Signals | None         = None
        self._saved_n = 0
        self._thr_val : float | None           = None
        self.save_dir = Path.home() / "Desktop" / "snoring_recordings"

        self._build_ui()
        self._apply_style()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setSpacing(6)
        vbox.setContentsMargins(8, 8, 8, 8)

        # ── Controls ─────────────────────────────────────────────────────────
        ctrl = QGroupBox("Controls")
        cl   = QHBoxLayout(ctrl)

        self.btn_start = QPushButton("● Start Recording")
        self.btn_start.setObjectName("btnStart")
        self.btn_start.clicked.connect(self.start_recording)

        self.btn_stop = QPushButton("■ Stop")
        self.btn_stop.setObjectName("btnStop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_recording)

        btn_dir = QPushButton("Select Folder")
        btn_dir.clicked.connect(self.pick_dir)

        self.lbl_dir = QLabel(str(self.save_dir))
        self.lbl_dir.setWordWrap(True)

        self.spin_pct = QDoubleSpinBox()
        self.spin_pct.setRange(0.1, 20.0)
        self.spin_pct.setValue(DEFAULT_TOP_PCT)
        self.spin_pct.setSingleStep(0.1)
        self.spin_pct.setSuffix(" %")
        self.spin_pct.setToolTip(
            "Save only the top N% strongest snoring segments.\n"
            "Lower value = only the strongest events are saved."
        )
        self.spin_pct.setFixedWidth(80)

        from PySide6.QtWidgets import QSpinBox
        self.spin_baseline = QSpinBox()
        self.spin_baseline.setRange(1, 5)
        self.spin_baseline.setValue(DEFAULT_BASELINE)
        self.spin_baseline.setSuffix(" min")
        self.spin_baseline.setToolTip(
            "Duration of the initial baseline collection period (1–5 minutes)."
        )
        self.spin_baseline.setFixedWidth(65)

        cl.addWidget(QLabel("Baseline:"))
        cl.addWidget(self.spin_baseline)
        cl.addSpacing(12)
        cl.addWidget(QLabel("Top"))
        cl.addWidget(self.spin_pct)
        cl.addWidget(QLabel("snoring only"))
        cl.addSpacing(16)
        cl.addWidget(self.btn_start)
        cl.addWidget(self.btn_stop)
        cl.addSpacing(10)
        cl.addWidget(btn_dir)
        cl.addWidget(self.lbl_dir, 1)
        vbox.addWidget(ctrl)

        # ── Status ───────────────────────────────────────────────────────────
        stat = QGroupBox("Status")
        sl   = QHBoxLayout(stat)

        self.lbl_phase = QLabel("Idle")
        self.lbl_phase.setFont(QFont("Tahoma", 11, QFont.Weight.Bold))
        self.lbl_phase.setStyleSheet("color: #000080;")

        self.lbl_seg   = QLabel("Segment: -")
        self.lbl_saved = QLabel("Saved: 0")
        self.lbl_thr   = QLabel("Threshold: -")
        self.lbl_dist  = QLabel("Distance: -")

        self.prog_base = QProgressBar()
        self.prog_base.setMaximum(DEFAULT_BASELINE)
        self.prog_base.setValue(0)
        self.prog_base.setFormat(f"Baseline %v/{DEFAULT_BASELINE} min")
        self.prog_base.setFixedWidth(220)

        sl.addWidget(self.lbl_phase)
        for w in [self.lbl_seg, self.lbl_saved, self.lbl_thr, self.lbl_dist]:
            sl.addWidget(QLabel("│"))
            sl.addWidget(w)
        sl.addWidget(self.prog_base)
        sl.addStretch()
        vbox.addWidget(stat)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: spectrogram images
        left = QGroupBox("Spectrogram")
        ll   = QVBoxLayout(left)
        row  = QHBoxLayout()

        lf = QWidget(); lfl = QVBoxLayout(lf); lfl.setContentsMargins(0,0,0,0)
        lfl.addWidget(QLabel("Current Segment", alignment=Qt.AlignmentFlag.AlignCenter))
        self.canvas_cur = SpectroCanvas("Current")
        lfl.addWidget(self.canvas_cur)

        rf = QWidget(); rfl = QVBoxLayout(rf); rfl.setContentsMargins(0,0,0,0)
        rfl.addWidget(QLabel("Baseline (Median)", alignment=Qt.AlignmentFlag.AlignCenter))
        self.canvas_base = SpectroCanvas("Baseline")
        rfl.addWidget(self.canvas_base)

        row.addWidget(lf)
        row.addWidget(rf)
        ll.addLayout(row)
        splitter.addWidget(left)

        # Right: chart + log
        right = QWidget()
        rl    = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        chart_grp = QGroupBox("Distance History")
        cgl = QVBoxLayout(chart_grp)
        self.chart = DistChart()
        cgl.addWidget(self.chart)
        rl.addWidget(chart_grp, 3)

        log_grp = QGroupBox("Log")
        lgl = QVBoxLayout(log_grp)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        lgl.addWidget(self.log)
        rl.addWidget(log_grp, 2)

        splitter.addWidget(right)
        splitter.setSizes([430, 670])
        vbox.addWidget(splitter, 1)

    def _apply_style(self):
        # Windows 2000 classic raised button borders:
        # top/left = white, right/bottom = dark gray → raised 3D look
        # pressed: reversed → sunken look
        self.setStyleSheet(f"""
            * {{
                font-family: "Tahoma", "MS Sans Serif", "Arial";
                font-size: 11px;
                color: #000000;
            }}

            QMainWindow, QWidget {{
                background-color: {W2K_BG};
            }}

            /* ── GroupBox: etched raised border ── */
            QGroupBox {{
                background-color: {W2K_BG};
                border-top:    2px solid {W2K_DARK};
                border-left:   2px solid {W2K_DARK};
                border-bottom: 2px solid {W2K_HIGHLIGHT};
                border-right:  2px solid {W2K_HIGHLIGHT};
                margin-top: 14px;
                padding-top: 6px;
                font-weight: bold;
                font-size: 11px;
                color: #000000;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 8px;
                padding: 0 3px;
                background-color: {W2K_BG};
                color: #000000;
            }}

            /* ── General button: raised 3D ── */
            QPushButton {{
                background-color: {W2K_BG};
                color: #000000;
                border-style: solid;
                border-width: 2px;
                border-top-color:    {W2K_HIGHLIGHT};
                border-left-color:   {W2K_HIGHLIGHT};
                border-right-color:  {W2K_DKSHADOW};
                border-bottom-color: {W2K_DKSHADOW};
                padding: 3px 10px;
                min-height: 20px;
            }}
            QPushButton:hover {{
                background-color: #e0dcd4;
            }}
            QPushButton:pressed {{
                border-top-color:    {W2K_DKSHADOW};
                border-left-color:   {W2K_DKSHADOW};
                border-right-color:  {W2K_HIGHLIGHT};
                border-bottom-color: {W2K_HIGHLIGHT};
                padding-left: 12px;
                padding-top: 5px;
            }}
            QPushButton:disabled {{
                color: {W2K_DARK};
                background-color: {W2K_BG};
            }}

            /* ── Start button: navy background ── */
            QPushButton#btnStart {{
                background-color: {W2K_TITLEBAR};
                color: {W2K_TITLEFG};
                font-weight: bold;
                border-top-color:    #6060c0;
                border-left-color:   #6060c0;
                border-right-color:  #000040;
                border-bottom-color: #000040;
            }}
            QPushButton#btnStart:hover {{
                background-color: #0000a0;
            }}
            QPushButton#btnStart:pressed {{
                border-top-color:    #000040;
                border-left-color:   #000040;
                border-right-color:  #6060c0;
                border-bottom-color: #6060c0;
            }}
            QPushButton#btnStart:disabled {{
                background-color: {W2K_BG};
                color: {W2K_DARK};
                border-top-color:    {W2K_HIGHLIGHT};
                border-left-color:   {W2K_HIGHLIGHT};
                border-right-color:  {W2K_DKSHADOW};
                border-bottom-color: {W2K_DKSHADOW};
            }}

            /* ── Stop button: dark red ── */
            QPushButton#btnStop {{
                background-color: #800000;
                color: {W2K_TITLEFG};
                font-weight: bold;
                border-top-color:    #c06060;
                border-left-color:   #c06060;
                border-right-color:  #400000;
                border-bottom-color: #400000;
            }}
            QPushButton#btnStop:hover  {{ background-color: #9a0000; }}
            QPushButton#btnStop:pressed {{
                border-top-color:    #400000;
                border-left-color:   #400000;
                border-right-color:  #c06060;
                border-bottom-color: #c06060;
            }}
            QPushButton#btnStop:disabled {{
                background-color: {W2K_BG};
                color: {W2K_DARK};
                border-top-color:    {W2K_HIGHLIGHT};
                border-left-color:   {W2K_HIGHLIGHT};
                border-right-color:  {W2K_DKSHADOW};
                border-bottom-color: {W2K_DKSHADOW};
            }}

            /* ── Sunken input fields ── */
            QTextEdit {{
                background-color: {W2K_WHITE};
                color: {W2K_LOGFG};
                border-style: solid;
                border-width: 2px;
                border-top-color:    {W2K_DKSHADOW};
                border-left-color:   {W2K_DKSHADOW};
                border-right-color:  {W2K_HIGHLIGHT};
                border-bottom-color: {W2K_HIGHLIGHT};
                font-family: "Courier New", "Lucida Console", monospace;
                font-size: 11px;
            }}

            QDoubleSpinBox, QSpinBox {{
                background-color: {W2K_WHITE};
                color: #000000;
                border-style: solid;
                border-width: 2px;
                border-top-color:    {W2K_DKSHADOW};
                border-left-color:   {W2K_DKSHADOW};
                border-right-color:  {W2K_HIGHLIGHT};
                border-bottom-color: {W2K_HIGHLIGHT};
                padding: 1px 3px;
            }}
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: {W2K_BG};
                border-style: solid;
                border-width: 1px;
                border-top-color:    {W2K_HIGHLIGHT};
                border-left-color:   {W2K_HIGHLIGHT};
                border-right-color:  {W2K_DKSHADOW};
                border-bottom-color: {W2K_DKSHADOW};
                width: 14px;
            }}

            /* ── ProgressBar: sunken + classic blue ── */
            QProgressBar {{
                background-color: {W2K_WHITE};
                color: #000000;
                border-style: solid;
                border-width: 2px;
                border-top-color:    {W2K_DKSHADOW};
                border-left-color:   {W2K_DKSHADOW};
                border-right-color:  {W2K_HIGHLIGHT};
                border-bottom-color: {W2K_HIGHLIGHT};
                text-align: center;
                font-size: 11px;
                border-radius: 0px;
            }}
            QProgressBar::chunk {{
                background-color: {W2K_TITLEBAR};
                border-radius: 0px;
                margin: 1px;
            }}

            /* ── Splitter ── */
            QSplitter::handle {{
                background-color: {W2K_BG};
                border-left:  1px solid {W2K_DARK};
                border-right: 1px solid {W2K_HIGHLIGHT};
                width: 4px;
            }}

            QLabel {{ color: #000000; background-color: transparent; }}

            /* ── ScrollBar classic ── */
            QScrollBar:vertical {{
                background: {W2K_BG};
                width: 16px;
                border-left: 1px solid {W2K_DARK};
            }}
            QScrollBar::handle:vertical {{
                background: {W2K_BG};
                border-style: solid;
                border-width: 1px;
                border-top-color:    {W2K_HIGHLIGHT};
                border-left-color:   {W2K_HIGHLIGHT};
                border-right-color:  {W2K_DKSHADOW};
                border-bottom-color: {W2K_DKSHADOW};
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                background: {W2K_BG};
                height: 16px;
                border-style: solid;
                border-width: 1px;
                border-top-color:    {W2K_HIGHLIGHT};
                border-left-color:   {W2K_HIGHLIGHT};
                border-right-color:  {W2K_DKSHADOW};
                border-bottom-color: {W2K_DKSHADOW};
            }}
            QScrollBar::up-arrow:vertical  {{ image: none; }}
            QScrollBar::down-arrow:vertical {{ image: none; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: #b0ac9e;
            }}
        """)

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Save Folder", str(self.save_dir))
        if d:
            self.save_dir = Path(d)
            self.lbl_dir.setText(d)

    def start_recording(self):
        baseline_segs = self.spin_baseline.value()

        self.save_dir.mkdir(parents=True, exist_ok=True)
        self._saved_n = 0
        self._thr_val = None

        self.lbl_saved.setText("Saved: 0")
        self.lbl_thr.setText("Threshold: -")
        self.lbl_dist.setText("Distance: -")
        self.lbl_seg.setText("Segment: -")
        self.prog_base.setMaximum(baseline_segs)
        self.prog_base.setFormat(f"Baseline %v/{baseline_segs} min")
        self.prog_base.setValue(0)
        self.lbl_phase.setText("Collecting Baseline...")
        self.lbl_phase.setStyleSheet("color: #804000; font-size: 11px; font-weight: bold;")

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.spin_pct.setEnabled(False)
        self.spin_baseline.setEnabled(False)

        sig = Signals()
        sig.log.connect(self._on_log)
        sig.bispec_updated.connect(self._on_bispec)
        sig.baseline_ready.connect(self._on_baseline_image)
        sig.baseline_progress.connect(self._on_base_prog)
        sig.distance_ready.connect(self._on_distance)
        sig.threshold_ready.connect(self._on_threshold)
        sig.phase_changed.connect(self._on_phase)
        sig.done.connect(self._on_done)
        sig.file_saved.connect(lambda p: self._on_log(f"Saved: {Path(p).name}"))

        self._signals = sig
        self._worker  = RecordingWorker(
            self.save_dir, self.spin_pct.value(), baseline_segs, sig
        )
        self._worker.start()
        self._on_log(
            f"Recording started — collecting {baseline_segs}-min baseline, "
            "then monitoring begins."
        )

    def stop_recording(self):
        if self._worker:
            self._worker.request_stop()
        self.btn_stop.setEnabled(False)
        self._on_log("Stop requested...")

    @Slot(str)
    def _on_log(self, msg: str):
        ts = datetime.now().strftime('%H:%M:%S')
        self.log.append(f"[{ts}] {msg}")
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    @Slot(object, int)
    def _on_bispec(self, arr: np.ndarray, seg_num: int):
        self.lbl_seg.setText(f"Segment: {seg_num}")
        self.canvas_cur.update_data(arr)

    @Slot(object)
    def _on_baseline_image(self, arr: np.ndarray):
        self.canvas_base.update_data(arr)
        self.prog_base.setValue(self.prog_base.maximum())

    @Slot(int)
    def _on_base_prog(self, n: int):
        self.prog_base.setValue(n)

    @Slot(float, int, bool)
    def _on_distance(self, dist: float, seg_num: int, saved: bool):
        self.lbl_dist.setText(f"Distance: {dist:.4f}")
        if saved:
            self._saved_n += 1
            self.lbl_saved.setText(f"Saved: {self._saved_n}")
        self.chart.add(dist, saved, self._thr_val)

    @Slot(float)
    def _on_threshold(self, thr: float):
        self._thr_val = thr
        self.lbl_thr.setText(f"Threshold: {thr:.4f}")

    @Slot(int)
    def _on_phase(self, phase: int):
        if phase == 1:
            self.lbl_phase.setText("Monitoring")
            self.lbl_phase.setStyleSheet("color: #000080; font-size: 11px; font-weight: bold;")

    @Slot()
    def _on_done(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.spin_pct.setEnabled(True)
        self.spin_baseline.setEnabled(True)
        self.lbl_phase.setText("Done")
        self.lbl_phase.setStyleSheet("color: #404040; font-size: 11px; font-weight: bold;")
        self._on_log(f"Recording finished — {self._saved_n} file(s) saved.")

    # ── Close event ──────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
            self._worker.wait(3000)
        event.accept()


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Sleep Snoring Detector")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

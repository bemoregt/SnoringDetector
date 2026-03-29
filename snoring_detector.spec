# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Sleep Snoring Detector (macOS .app)
Build:  pyinstaller snoring_detector.spec --noconfirm
"""

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# ── Collect packages with C-extensions that PyInstaller often misses ───────
def ca(pkg):
    b, d, h = collect_all(pkg)
    return b, d, h

np_b,  np_d,  np_h  = ca('numpy')
sp_b,  sp_d,  sp_h  = ca('scipy')
mpl_b, mpl_d, mpl_h = ca('matplotlib')
sd_b,  sd_d,  sd_h  = ca('sounddevice')
sf_b,  sf_d,  sf_h  = ca('soundfile')

# NOTE: PySide6 / shiboken6 intentionally left to PyInstaller's own hooks
# to avoid duplicate Qt-framework symlink conflicts.

all_binaries = np_b + sp_b + mpl_b + sd_b + sf_b
all_datas    = np_d + sp_d + mpl_d + sd_d + sf_d
all_hidden   = list(set(
    np_h + sp_h + mpl_h + sd_h + sf_h
    + collect_submodules('numpy')
    + collect_submodules('scipy')
    + collect_submodules('matplotlib')
))

block_cipher = None

a = Analysis(
    ['snoring_detector.py'],
    pathex=[],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hidden + [
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'shiboken6',
        'matplotlib.backends.backend_qtagg',
        'matplotlib.backends.backend_qt5agg',
        'cffi',
        '_cffi_backend',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'PyQt5', 'PyQt6', 'wx',
        'IPython', 'jupyter', 'notebook',
        'nltk', 'sklearn', 'tensorflow', 'torch', 'cv2',
        'pdb', 'profile', 'cProfile',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SnoringDetector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch='x86_64',
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='SnoringDetector',
)

app = BUNDLE(
    coll,
    name='SnoringDetector.app',
    icon=None,
    bundle_identifier='com.local.snoringdetector',
    version='1.0.0',
    info_plist={
        'CFBundleName':                'SnoringDetector',
        'CFBundleDisplayName':         'Sleep Snoring Detector (Intel)',
        'CFBundleVersion':             '1.0.0',
        'CFBundleShortVersionString':  '1.0.0',
        'NSMicrophoneUsageDescription':
            'This app records microphone audio to detect snoring events.',
        'NSHighResolutionCapable':     True,
        'LSMinimumSystemVersion':      '12.0',
        'NSPrincipalClass':            'NSApplication',
        'NSAppleScriptEnabled':        False,
    },
)

"""
py2app setup for Sleep Snoring Detector
Build:  python setup.py py2app
"""
import os
import sys
from setuptools import setup

# ── Locate PySide6 package dir ─────────────────────────────────────────────
import PySide6 as _pyside6
PYSIDE6_DIR = os.path.dirname(_pyside6.__file__)
QT_LIB_DIR  = os.path.join(PYSIDE6_DIR, 'Qt', 'lib')
QT_PLUG_DIR = os.path.join(PYSIDE6_DIR, 'Qt', 'plugins')

# ── Collect Qt .framework dirs to include as data ──────────────────────────
qt_frameworks = []
if os.path.isdir(QT_LIB_DIR):
    for entry in os.listdir(QT_LIB_DIR):
        full = os.path.join(QT_LIB_DIR, entry)
        if entry.endswith('.framework') and os.path.isdir(full):
            qt_frameworks.append(full)

# ── Collect Qt plugins ──────────────────────────────────────────────────────
qt_plugins = []
if os.path.isdir(QT_PLUG_DIR):
    qt_plugins = [(QT_PLUG_DIR, 'qt_plugins')]

APP      = ['snoring_detector.py']
APP_NAME = 'SnoringDetector'

OPTIONS = {
    'argv_emulation': False,            # must be False for PySide6
    'iconfile': None,
    'plist': {
        'CFBundleName':               APP_NAME,
        'CFBundleDisplayName':        'Sleep Snoring Detector',
        'CFBundleIdentifier':         'com.local.snoringdetector',
        'CFBundleVersion':            '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSMicrophoneUsageDescription':
            'This app records microphone audio to detect snoring events.',
        'NSHighResolutionCapable':    True,
        'LSMinimumSystemVersion':     '12.0',
    },
    # Packages to bundle explicitly
    'packages': [
        'PySide6',
        'numpy', 'scipy',
        'sounddevice', 'soundfile',
        'matplotlib',
        'shiboken6',
    ],
    # Extra data: Qt frameworks + plugins
    'frameworks': qt_frameworks,
    'resources':  [],
    # Include these modules explicitly (py2app sometimes misses them)
    'includes': [
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'matplotlib.backends.backend_qtagg',
        'matplotlib.backends.backend_qt5agg',
        'scipy.signal',
        'scipy.ndimage',
        'shiboken6',
    ],
    # Exclude large unused packages
    'excludes': [
        'tkinter', 'PyQt5', 'PyQt6',
        'wx', 'gtk',
        'IPython', 'jupyter',
        'test', 'unittest',
    ],
    'semi_standalone': False,
    'site_packages':   True,
}

setup(
    name=APP_NAME,
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)

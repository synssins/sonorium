# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Sonorium Launcher.

This builds the native Windows launcher (Sonorium.exe) that:
- Provides a native PyQt6 UI
- Manages the Python core as a subprocess
- Handles system tray integration
- Manages settings and updates

Build command:
    pyinstaller Sonorium.spec

Output: app/windows/Sonorium.exe (same directory as spec file)
"""

import os
from pathlib import Path

# Get the spec file directory
spec_dir = Path(SPECPATH)
app_dir = spec_dir.parent  # app/
project_dir = app_dir.parent  # SonoriumDev/

# Icon paths (required)
icon_path = app_dir / 'core' / 'icon.png'
logo_path = app_dir / 'core' / 'logo.png'

# Output directly to app/windows/ (not dist/)
distpath = str(spec_dir)
workpath = str(spec_dir / 'build')

# Analysis - gather all dependencies
a = Analysis(
    [str(spec_dir / 'launcher.py')],
    pathex=[str(spec_dir)],
    binaries=[],
    datas=[
        (str(icon_path), '.'),
        (str(logo_path), '.'),
    ],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude unnecessary modules to reduce size
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

# Remove duplicate binaries/datas
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# Build the executable
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Sonorium',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,  # Compress executable
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window - this is a GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),  # Application icon
    version_info={
        'FileVersion': '1.0.0.0',
        'ProductVersion': '1.0.0.0',
        'FileDescription': 'Sonorium - Ambient Soundscape Mixer',
        'InternalName': 'Sonorium',
        'LegalCopyright': 'Copyright 2025',
        'OriginalFilename': 'Sonorium.exe',
        'ProductName': 'Sonorium',
        'CompanyName': '',
    } if os.name == 'nt' else None,
)

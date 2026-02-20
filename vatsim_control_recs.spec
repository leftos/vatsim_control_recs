# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for VATSIM Control Recommendations.

Usage:
    pyinstaller vatsim_control_recs.spec --noconfirm
"""

from PyInstaller.utils.hooks import collect_all

# Collect spaCy, its language model, Textual (includes CSS/templates),
# and Rich (includes dynamically-loaded _unicode_data modules)
spacy_datas, spacy_binaries, spacy_hiddenimports = collect_all('spacy')
model_datas, model_binaries, model_hiddenimports = collect_all('en_core_web_sm')
textual_datas, textual_binaries, textual_hiddenimports = collect_all('textual')
rich_datas, rich_binaries, rich_hiddenimports = collect_all('rich')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=spacy_binaries + model_binaries + textual_binaries + rich_binaries,
    datas=[
        # Application data files
        ('data/raw/APT_BASE.csv', 'data/raw'),
        ('data/raw/airports.json', 'data/raw'),
        ('data/raw/iata-icao.csv', 'data/raw'),
        ('data/airport_names.csv', 'data'),
        ('data/aircraft_data.csv', 'data'),
        ('data/airport_spatial_cache.json', 'data'),
        ('data/custom_groupings.json', 'data'),
        ('data/preset_groupings', 'data/preset_groupings'),
    ] + spacy_datas + model_datas + textual_datas + rich_datas,
    hiddenimports=spacy_hiddenimports + model_hiddenimports + textual_hiddenimports + rich_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PIL',
        'Pillow',
        'tkinter',
        '_tkinter',
        'matplotlib',
        'pandas',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='vatsim_control_recs',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='vatsim_control_recs',
)

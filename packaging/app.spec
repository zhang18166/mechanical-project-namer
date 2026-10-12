# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166
from pathlib import Path
root = Path(SPECPATH).parent
a = Analysis([str(root / 'launcher.py')], pathex=[str(root)], binaries=[], datas=[],
    hiddenimports=['win32timezone', 'keyring.backends.Windows'], hookspath=[],
    hooksconfig={}, runtime_hooks=[], excludes=['pytest', 'pytestqt'], noarchive=False)
# PDF export uses QtPrintSupport. The optional PDF image reader brings in
# QtWebEngine's QtPdf; it is not used by this application and is not shipped.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {'qpdf.dll', 'qt6pdf.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MechanicalProjectNamer',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, icon='NONE')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name='MechanicalProjectNamer')

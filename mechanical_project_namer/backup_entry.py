# SPDX-License-Identifier: GPL-3.0-only
"""Maintenance entry does not load/migrate any business DB before showing the UI."""
import argparse
import os
from pathlib import Path
import sys


def default_session_root():
    inherited=os.environ.get('MECHANICAL_PROJECT_NAMER_DEV_ROOT')
    if inherited:
        source=Path(inherited).absolute()
        return source.parent/(source.name+'-maintenance')
    return Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'MechanicalWorkbenchDev/phase3b-maintenance'


def main():
    parser=argparse.ArgumentParser(description='离线库备份与新位置恢复（本地未加密）')
    parser.add_argument('--profile',default='');parser.add_argument('--session-root',type=Path)
    args=parser.parse_args()
    from .development import configure_development
    session=args.session_root or default_session_root()
    configure_development(['--dev','--dev-root',str(session)])
    from PyQt6.QtWidgets import QApplication
    from .ui.backups_dialog import BackupsDialog
    from .style import APP_STYLE
    app=QApplication([sys.argv[0]]);app.setStyleSheet(APP_STYLE)
    window=BackupsDialog(args.profile);window.show();return app.exec()


if __name__=='__main__':raise SystemExit(main())

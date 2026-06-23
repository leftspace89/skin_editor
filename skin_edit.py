#!/usr/bin/env python3
"""skin_edit.py - GUI skin editor for S2 SonSilah / District 187 weapons.

Loads a weapon .Model00p, shows it in a Blender-style 4-pane viewport (Top / Front
/ Right ortho + an orbit perspective view), lets you paint diffuse / brightness /
spec / normal / roughness directly on the model or on a 2D UV panel, and exports
edited DXT5 .dds textures (reusing tools/model00p/png2dds).

Usage:
  py skin_edit.py [--game-root DIR] [MODEL.Model00p]
    --game-root DIR   S2 game root (the folder containing weapons/). Default: env
                      S2_GAME_ROOT, the last folder chosen in the GUI, else set it
                      from File > Set Game Root.

Controls:
  Navigate mode  : LMB orbit (perspective), MMB pan, wheel zoom, double-click/Tab maximize a pane
  Paint mode     : LMB paint; MMB pan + wheel zoom still work
  UV panel       : MMB pan, wheel zoom, LMB paint (in Paint mode)
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Set S2_GAME_ROOT (or pass --game-root, or pick it once in the GUI). No path is hardcoded.
DEFAULT_GAME_ROOT = os.environ.get('S2_GAME_ROOT', '')


def main():
    ap = argparse.ArgumentParser(description='S2 weapon skin editor (GUI).')
    ap.add_argument('model', nargs='?', help='path to a .Model00p to open on startup')
    ap.add_argument('--game-root', default=None,
                    help='S2 game root (folder containing weapons/). If omitted, uses '
                         '$S2_GAME_ROOT or the last folder chosen in the GUI; otherwise '
                         'set it from File > Set Game Root.')
    args = ap.parse_args()

    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        print('PySide6 is required. Install with:\n'
              '  pip install -r "%s"' % os.path.join(_HERE, 'requirements.txt'),
              file=sys.stderr)
        return 2

    # moderngl needs a 3.3 core-profile context with a depth buffer.
    fmt = QtGui.QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QtGui.QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    QtGui.QSurfaceFormat.setDefaultFormat(fmt)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts, True)

    app = QtWidgets.QApplication(sys.argv)
    app.setOrganizationName('S2Rev')
    app.setApplicationName('SkinEdit')

    # Resolution order: CLI flag > last GUI choice (QSettings) > built-in default.
    game_root = args.game_root
    if not game_root:
        game_root = QtCore.QSettings().value('game_root', '') or DEFAULT_GAME_ROOT
    print('game root: %s' % game_root)

    from app_window import MainWindow

    win = MainWindow(game_root, model_path=args.model)
    win.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())

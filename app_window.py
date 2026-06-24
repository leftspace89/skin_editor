"""app_window.py - MainWindow wiring the 3D viewport, 2D UV panel, toolbars and
the export dialog together."""
import os
import re

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

import brush as brushmod
import mat00io
import materials
import scene as scenemod
import textures
from gl_viewport import GLViewport
from uv_panel import UVPanel

VIEW_MODES = [('Lit', 0), ('Diffuse', 1), ('Normal', 2), ('Roughness', 3),
              ('Spec', 4), ('Flat', 5), ('Emissive', 6)]

# Ready-made tileable emissive patterns shipped with the tool.
PATTERN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'patterns')

# Ready-made glow/animation looks. Each fills in every knob (omitted keys reset to a sane
# base), so picking one is a one-click effect. 'anim' enables the master Animate switch.
_GLOW_BASE = dict(glow=3.0, glow_speed=0.8, glow_amount=0.0, bloom=0.006,
                  flow_amount=0.0, flow_speed=0.3, flow_freq=3.0, flow_dir=1, flow_space=0,
                  rim_scale=0.0, rim_power=3.0, scroll_speed=0.0, hue_speed=0.0,
                  emissive_tile=1.0, anim=False)
def _preset(**kw):
    d = dict(_GLOW_BASE); d.update(kw); return d
GLOW_PRESETS = {
    'Steady glow':        _preset(glow_amount=0.0),
    'Soft pulse':         _preset(glow=3.0, glow_speed=0.8, glow_amount=0.5, anim=True),
    'Heartbeat':          _preset(glow=4.0, glow_speed=1.6, glow_amount=0.8, anim=True),
    'Energy flow':        _preset(glow=3.0, flow_amount=0.9, flow_speed=0.5, flow_freq=4.0,
                                  scroll_speed=0.3, anim=True),
    'Pattern scroll':     _preset(glow=3.0, emissive_tile=4.0, scroll_speed=0.3, flow_dir=1, anim=True),
    'Rainbow cycle':      _preset(glow=3.0, hue_speed=0.3, anim=True),
    'Rim energy':         _preset(glow=1.5, rim_scale=1.6, rim_power=3.0, hue_speed=0.2, anim=True),
    'Tron pulse':         _preset(glow=4.0, glow_amount=0.4, flow_amount=1.0, flow_speed=0.6,
                                  flow_freq=6.0, hue_speed=0.0, anim=True),
}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, game_root, model_path=None):
        super().__init__()
        self.setWindowTitle('S2 Skin Editor')
        self.resize(1500, 900)
        self.game_root = game_root
        self.model = None
        self.uv_all = None

        self.rsettings = scenemod.RenderSettings()
        self.brush = brushmod.BrushSettings()

        self.viewport = GLViewport(self.rsettings, self.brush)
        self.setCentralWidget(self.viewport)

        self.uv_panel = UVPanel(self.rsettings, self.brush)
        uv_box = QtWidgets.QWidget()
        uv_lay = QtWidgets.QVBoxLayout(uv_box); uv_lay.setContentsMargins(2, 2, 2, 2); uv_lay.setSpacing(2)
        rep_btn = QtWidgets.QPushButton('Replace active map with image…')
        rep_btn.setToolTip('Replace the shown map (diffuse / emissive / etc.) of the active piece '
                           'with a PNG/DDS/TGA — e.g. a pattern as the emissive.')
        rep_btn.clicked.connect(self._open_texture_dialog)
        uv_lay.addWidget(rep_btn)
        uv_lay.addWidget(self.uv_panel, 1)
        dock = QtWidgets.QDockWidget('UV / Texture', self)
        dock.setWidget(uv_box)
        dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        self.uv_dock = dock

        # Effects panel: live-edit the loaded skin's specular_glow material.
        self.glow_panel = GlowPanel(self)
        gdock = QtWidgets.QDockWidget('Effects', self)
        gdock.setWidget(self.glow_panel)
        gdock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, gdock)
        self.glow_dock = gdock

        self._build_model_browser()
        self._build_menu()
        self._build_toolbar()
        self.statusBar().showMessage('Ready')

        self.viewport.status.connect(self.statusBar().showMessage)
        self.uv_panel.painted.connect(self._on_uv_painted)

        self._rescan_models()
        if model_path:
            self.open_model(model_path)

    # ---- UI ------------------------------------------------------------
    def _build_menu(self):
        m = self.menuBar().addMenu('&File')
        a = m.addAction('Set Game Root...'); a.setShortcut('Ctrl+G')
        a.triggered.connect(self._set_game_root_dialog)
        m.addSeparator()
        a = m.addAction('Open Model...'); a.setShortcut('Ctrl+O')
        a.triggered.connect(self._open_dialog)
        a = m.addAction('Replace Active Map from Image...'); a.setShortcut('Ctrl+R')
        a.triggered.connect(self._open_texture_dialog)
        a = m.addAction('Generate Normal Map from Diffuse...')
        a.triggered.connect(self._generate_normal)
        a = m.addAction('Generate Glow (Emissive) Map from Diffuse...')
        a.triggered.connect(self._generate_emissive)
        a = m.addAction('Export UV Layout (PNG guide)...')
        a.triggered.connect(self._export_uv_layout)
        m.addSeparator()
        a = m.addAction('Save All (textures + material)'); a.setShortcut('Ctrl+S')
        a.triggered.connect(self._save_all)
        a = m.addAction('Create New Skin (named, with options)...')
        a.setShortcut('Ctrl+Shift+N')
        a.triggered.connect(self._create_new_skin)
        a = m.addAction('Delete Skin (removes its files)...')
        a.triggered.connect(self._delete_skin)
        m.addSeparator()
        a = m.addAction('Quit'); a.setShortcut('Ctrl+Q'); a.triggered.connect(self.close)

        v = self.menuBar().addMenu('&View')
        a = v.addAction('Reset View'); a.setShortcut('Home')
        a.triggered.connect(self.viewport.reset_view)

    def _build_model_browser(self):
        dock = QtWidgets.QDockWidget('Models', self)
        dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QtWidgets.QHBoxLayout()
        self.model_filter = QtWidgets.QLineEdit()
        self.model_filter.setPlaceholderText('filter...')
        self.model_filter.textChanged.connect(self._rebuild_tree)
        rescan = QtWidgets.QToolButton(); rescan.setText('Rescan')
        rescan.clicked.connect(self._rescan_models)
        bar.addWidget(self.model_filter); bar.addWidget(rescan)
        lay.addLayout(bar)
        self.model_tree = QtWidgets.QTreeWidget()
        self.model_tree.setHeaderHidden(True)
        self.model_tree.itemActivated.connect(self._on_model_picked)
        self.model_tree.itemDoubleClicked.connect(self._on_model_picked)
        lay.addWidget(self.model_tree)
        self.model_count_lbl = QtWidgets.QLabel('')
        lay.addWidget(self.model_count_lbl)
        dock.setWidget(w)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
        self.model_dock = dock

    # Content subtrees scanned for models (weapons + male/female costumes + characters).
    _MODEL_SUBTREES = ('weapons', 'costumes_f', 'costumes_m',
                       'characters_f', 'characters_m', 'characters', 'props')

    def _scan_models(self):
        """Find non-empty .Model00p files under the game root's content subtrees
        (weapons, costumes_f/m, characters, …); returns [(label, fullpath)] sorted."""
        root = self.game_root
        if not os.path.isdir(root):
            return []
        scan_roots = [os.path.join(root, s) for s in self._MODEL_SUBTREES
                      if os.path.isdir(os.path.join(root, s))] or [root]
        found, cap = [], 12000
        for scan_root in scan_roots:
            for dirpath, _dirs, files in os.walk(scan_root):
                for fn in files:
                    if fn.lower().endswith('.model00p'):
                        full = os.path.join(dirpath, fn)
                        try:
                            if os.path.getsize(full) <= 0:   # 0-byte view-model stubs
                                continue
                        except OSError:
                            continue
                        found.append((os.path.relpath(full, root).replace('\\', '/'), full))
                        if len(found) >= cap:
                            break
                if len(found) >= cap:
                    break
            if len(found) >= cap:
                break
        found.sort(key=lambda t: t[0].lower())
        return found

    def _rescan_models(self):
        self._all_models = self._scan_models()
        self._rebuild_tree()

    def _rebuild_tree(self):
        if not hasattr(self, '_all_models'):
            self._all_models = []
        flt = self.model_filter.text().strip().lower()
        self.model_tree.clear()
        folders = {}                       # dir-tuple -> QTreeWidgetItem

        def folder_item(parts):
            if not parts:
                return None
            if parts in folders:
                return folders[parts]
            parent = folder_item(parts[:-1])
            it = QtWidgets.QTreeWidgetItem([parts[-1]])
            it.setFlags(it.flags() & ~QtCore.Qt.ItemIsSelectable)
            (parent.addChild(it) if parent is not None
             else self.model_tree.addTopLevelItem(it))
            folders[parts] = it
            return it

        shown = 0
        for label, full in self._all_models:
            if flt and flt not in label.lower():
                continue
            *dirs, fname = label.split('/')
            parent = folder_item(tuple(dirs))
            leaf = QtWidgets.QTreeWidgetItem([fname])
            leaf.setData(0, QtCore.Qt.UserRole, full)
            leaf.setToolTip(0, full)
            (parent.addChild(leaf) if parent is not None
             else self.model_tree.addTopLevelItem(leaf))
            shown += 1
        if flt:
            self.model_tree.expandAll()
        total = len(self._all_models)
        suffix = '' if shown == total else ' / %d' % total
        self.model_count_lbl.setText('%d%s model(s)' % (shown, suffix))

    def _on_model_picked(self, item, _col=0):
        full = item.data(0, QtCore.Qt.UserRole)
        if full:
            self.open_model(full)

    def _build_toolbar(self):
        tb = self.addToolBar('Main')
        tb.setIconSize(QtCore.QSize(16, 16))

        # navigate / paint
        self.mode_group = QtWidgets.QButtonGroup(self)
        for label, val in (('Navigate', 'orbit'), ('Paint', 'paint')):
            b = QtWidgets.QToolButton(); b.setText(label); b.setCheckable(True)
            b.clicked.connect(lambda _=False, v=val: self._set_tool_mode(v))
            self.mode_group.addButton(b)
            tb.addWidget(b)
            if val == 'orbit':
                b.setChecked(True)
        tb.addSeparator()

        # brush mode
        tb.addWidget(QtWidgets.QLabel(' Brush: '))
        self.brush_combo = QtWidgets.QComboBox()
        self.brush_combo.addItems(list(brushmod.MODES))
        self.brush_combo.currentTextChanged.connect(self._set_brush_mode)
        tb.addWidget(self.brush_combo)

        self.color_btn = QtWidgets.QToolButton(); self.color_btn.setText('Color')
        self.color_btn.clicked.connect(self._pick_color)
        self._update_color_btn()
        tb.addWidget(self.color_btn)

        tb.addWidget(QtWidgets.QLabel(' Size '))
        self.size_slider = self._slider(2, 200, int(self.brush.radius), self._set_radius)
        tb.addWidget(self.size_slider)
        tb.addWidget(QtWidgets.QLabel(' Strength '))
        self.str_slider = self._slider(1, 100, int(self.brush.strength * 100), self._set_strength)
        tb.addWidget(self.str_slider)
        tb.addWidget(QtWidgets.QLabel(' Hardness '))
        self.hard_slider = self._slider(0, 100, int(self.brush.hardness * 100), self._set_hardness)
        tb.addWidget(self.hard_slider)
        tb.addSeparator()

        # view mode + toggles
        tb.addWidget(QtWidgets.QLabel(' View: '))
        self.view_combo = QtWidgets.QComboBox()
        for label, _ in VIEW_MODES:
            self.view_combo.addItem(label)
        self.view_combo.currentIndexChanged.connect(self._set_view_mode)
        tb.addWidget(self.view_combo)

        self.flip_cb = QtWidgets.QCheckBox('Flip V'); self.flip_cb.setChecked(self.rsettings.flip_v)
        self.flip_cb.toggled.connect(self._set_flip)
        tb.addWidget(self.flip_cb)
        self.invr_cb = QtWidgets.QCheckBox('Inv Rough')
        self.invr_cb.toggled.connect(lambda v: self._set('invert_roughness', v))
        tb.addWidget(self.invr_cb)

        tb.addWidget(QtWidgets.QLabel(' Glow '))
        self.glow_slider = self._slider(0, 400, int(self.rsettings.glow * 100), self._set_glow)
        tb.addWidget(self.glow_slider)
        # (Animate toggle lives in the Glow panel - single source, no toolbar duplicate.)

        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(' Skin: '))
        self.skin_combo = QtWidgets.QComboBox()
        self.skin_combo.setMinimumWidth(90)
        self.skin_combo.currentTextChanged.connect(self._switch_skin)
        tb.addWidget(self.skin_combo)
        del_btn = QtWidgets.QToolButton()
        del_btn.setText('🗑')
        del_btn.setToolTip('Delete the selected skin (removes its texture + material files)')
        del_btn.clicked.connect(self._delete_skin)
        tb.addWidget(del_btn)

        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(' Map: '))
        self.group_combo = QtWidgets.QComboBox()
        self.group_combo.setMinimumWidth(110)
        self.group_combo.currentIndexChanged.connect(lambda _: self._sync_uv_panel(reset_view=True))
        tb.addWidget(self.group_combo)

    def _slider(self, lo, hi, val, cb):
        s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        s.setRange(lo, hi); s.setValue(val); s.setFixedWidth(90)
        s.valueChanged.connect(cb)
        return s

    # ---- settings handlers --------------------------------------------
    def _set_tool_mode(self, v):
        self.viewport.tool_mode = v
        self.uv_panel.tool_mode = v
        self.statusBar().showMessage('Mode: %s' % v)

    def _set_brush_mode(self, mode):
        self.brush.mode = mode
        self.color_btn.setEnabled(mode in ('diffuse', 'emissive'))
        self._update_color_btn()
        self._sync_uv_panel()

    def _set_radius(self, v): self.brush.radius = float(v)
    def _set_strength(self, v): self.brush.strength = v / 100.0
    def _set_hardness(self, v): self.brush.hardness = v / 100.0

    def _set_glow(self, v):
        self.rsettings.glow = v / 100.0
        if hasattr(self, 'glow_panel'):
            self.glow_panel.sync_from_settings()
        self.viewport.update()

    def _set_glow_anim(self, on):
        self.viewport.set_glow_anim(on)
        self._sync_glow_widgets()

    def _sync_glow_widgets(self):
        """Reflect rsettings glow intensity into the toolbar slider + the Glow panel,
        without re-triggering their handlers."""
        self.glow_slider.blockSignals(True)
        self.glow_slider.setValue(int(self.rsettings.glow * 100))
        self.glow_slider.blockSignals(False)
        if hasattr(self, 'glow_panel'):
            self.glow_panel.sync_from_settings()

    def _find_glow_materials(self):
        """Locate the .Mat00 file(s) for the currently loaded model + skin whose shader
        is the animated-glow shader (specular_glow.fx), so the Glow panel can edit their
        fGlowScale/Speed/Amount in place. Includes piece + PV/world sibling materials."""
        if not self.model:
            return []
        model_dir = os.path.dirname(os.path.abspath(self.model.path))
        skin = self.model.skin or ''
        stems = [g['texset'].name for g in self.model.draw_groups]
        for s in list(stems):
            stems += materials.sibling_materials(s, self.game_root, model_dir)
        names = set()
        for s in stems:
            if skin:
                names.add('%s-%s' % (s, skin))
            names.add(s)
        paths = {}
        for nm in names:
            p = materials.find_mat00(nm, self.game_root, model_dir)
            if p:
                paths[os.path.normcase(os.path.abspath(p))] = p
        out = []
        for p in paths.values():
            try:
                _, shader, _ = mat00io.parse(open(p, 'rb').read())
            except Exception:
                continue
            if shader.lower() in mat00io.OUR_EFFECT_SHADERS:
                out.append((p, shader))
        return out

    def _set_view_mode(self, idx):
        self.rsettings.view_mode = VIEW_MODES[idx][1]
        self.viewport.update()

    def _set_flip(self, v):
        self.rsettings.flip_v = v
        self.viewport.update()
        self.uv_panel.refresh_image(); self.uv_panel.update()

    def _set(self, attr, v):
        setattr(self.rsettings, attr, v)
        self.viewport.update()

    def _pick_color(self):
        glow = self.brush.mode == 'emissive'
        cur = self.brush.glow_color if glow else self.brush.color
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(*cur), self,
                                            'Glow color' if glow else 'Brush color')
        if c.isValid():
            rgb = (c.red(), c.green(), c.blue())
            if glow:
                self.brush.glow_color = rgb
            else:
                self.brush.color = rgb
            self._update_color_btn()

    def _update_color_btn(self):
        rgb = self.brush.glow_color if self.brush.mode == 'emissive' else self.brush.color
        self.color_btn.setStyleSheet('QToolButton{background:rgb(%d,%d,%d);}' % rgb)

    # ---- game root / model / skin -------------------------------------
    def _set_game_root_dialog(self):
        start = self.game_root if os.path.isdir(self.game_root) else ''
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Select S2 game root (the folder containing "weapons")', start)
        if not d:
            return
        self.game_root = d
        QtCore.QSettings().setValue('game_root', d)   # remembered for next launch
        self._rescan_models()
        # re-resolve skins/textures from the new root for the loaded model
        if self.model:
            self.open_model(self.model.path, skin=self.model.skin)
        self.statusBar().showMessage('Game root: %s' % d)

    def _open_dialog(self):
        start = os.path.join(self.game_root, 'weapons', '1_main', 'view_models')
        if not os.path.isdir(start):
            start = self.game_root
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Open Model', start, 'Model00p (*.Model00p);;All files (*)')
        if path:
            self.open_model(path)

    def open_model(self, path, skin=''):
        try:
            lm = materials.load(path, self.game_root, skin=skin)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Load failed', str(e))
            return
        # load all texture buffers up front (so UV panel + export work immediately)
        for g in lm.draw_groups:
            if not g['texset'].buffers:
                textures.load_textureset(g['texset'])
        self.model = lm
        self.uv_all = lm.vertices[:, 6:8].copy()
        self.viewport.load_model(lm)
        self._refresh_skins()
        self._refresh_groups()
        self._sync_uv_panel(reset_view=True)
        if hasattr(self, 'glow_panel'):
            self.glow_panel.refresh_targets()
        n_tris = sum(g['faces'].shape[0] for g in lm.draw_groups)
        self.setWindowTitle('S2 Skin Editor - %s' % os.path.basename(path))
        self.statusBar().showMessage('%s  %d verts  %d tris  groups: %s'
                                     % (lm.name, len(lm.vertices), n_tris,
                                        ', '.join(g['name'] for g in lm.draw_groups)))

    def _open_texture_dialog(self):
        """Replace a chosen map (diffuse / emissive / spec / normal / roughness) of the
        active piece with an imported PNG/DDS/TGA - e.g. drop a grid pattern onto the
        EMISSIVE map and turn on Scroll energy for a moving grid. For diffuse, the new
        image's RGB becomes the skin; its spec-mask alpha can be restored on export."""
        if not self.model:
            return
        g = self._active_group()
        if not g:
            return
        ts = g['texset']
        # Let the user pick WHICH map to replace (default = the one currently shown).
        order = ['diffuse', 'normal', 'roughness', 'spec', 'emissive']
        roles = [r for r in order if r in ts.buffers] or ['diffuse']
        active = self.uv_panel.role if self.uv_panel.role in roles else roles[0]
        role, ok = QtWidgets.QInputDialog.getItem(
            self, 'Replace map', 'Which map to replace with an image?',
            roles, roles.index(active), False)
        if not ok or not role:
            return
        start = os.path.join(self.game_root, 'weapons', '1_main', 'textures')
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Replace %s map from image' % role,
            start if os.path.isdir(start) else self.game_root,
            'Images (*.png *.dds *.tga *.bmp *.jpg);;All files (*)')
        if not path:
            return
        if self._replace_map(role, path, g):
            self.statusBar().showMessage('Replaced %s of %s with %s'
                                         % (role, g['name'], os.path.basename(path)))

    def _replace_map(self, role, path, group=None):
        """Load an image into <role>'s buffer of a piece, upload it, and show it. Shared
        by the Replace-map dialog and the pattern picker. Returns True on success."""
        g = group or self._active_group()
        if not g:
            return False
        ts = g['texset']
        try:
            buf = textures.load_dds(path)            # any PIL-readable image -> RGBA
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Load failed', str(e))
            return False
        # for diffuse, keep the original spec mask in alpha if the new image is opaque
        if role == 'diffuse' and role in ts.original and buf[:, :, 3].min() == 255:
            orig = ts.original[role]
            if orig.shape == buf.shape:
                buf[:, :, 3] = orig[:, :, 3]
        ts.buffers[role] = buf
        ts.original[role] = buf.copy()
        self.viewport.makeCurrent()
        try:
            textures.upload_textureset(self.viewport.ctx, ts)
        finally:
            self.viewport.doneCurrent()
        self.viewport.update()
        self.uv_panel.set_target(ts, role, uv_coords=self.uv_all, faces=g['faces'], reset_view=False)
        return True

    def _apply_pattern(self, path):
        """Drop a ready pattern (from tools/skin_edit/patterns) onto the active piece's
        emissive map, so the glow/scroll/tint effects show it."""
        if not self.model:
            QtWidgets.QMessageBox.information(self, 'Pattern', 'Load a model first.')
            return
        if self._replace_map('emissive', path):
            self.statusBar().showMessage('Applied pattern %s to emissive' % os.path.basename(path))

    def _generate_normal(self):
        """Open the normal-from-diffuse generator for the active piece. Applying
        writes the result into the piece's _N (normal) buffer + GL texture."""
        if not self.model:
            QtWidgets.QMessageBox.information(self, 'Normal map', 'Load a model first.')
            return
        g = self._active_group()
        if not g or g['texset'].buffers.get('diffuse') is None:
            QtWidgets.QMessageBox.information(self, 'Normal map', 'No diffuse to derive from.')
            return
        ts = g['texset']

        def apply_normal(nm):
            ts.buffers['normal'] = nm
            ts.original['normal'] = nm.copy()
            self.viewport.makeCurrent()
            try:
                textures.upload_textureset(self.viewport.ctx, ts)
            finally:
                self.viewport.doneCurrent()
            self.viewport.update()
            if self.uv_panel.texset is ts and self.uv_panel.role == 'normal':
                self.uv_panel.refresh_image(); self.uv_panel.update()

        NormalGenDialog(self, ts, apply_normal).exec()
        self.statusBar().showMessage('Generated normal map for %s' % g['name'])

    def _generate_emissive(self):
        """Open the glow-from-diffuse generator for the active piece. Applying writes
        the result into the piece's _EM (emissive/glow) buffer + GL texture."""
        if not self.model:
            QtWidgets.QMessageBox.information(self, 'Glow map', 'Load a model first.')
            return
        g = self._active_group()
        if not g or g['texset'].buffers.get('diffuse') is None:
            QtWidgets.QMessageBox.information(self, 'Glow map', 'No diffuse to derive from.')
            return
        ts = g['texset']

        def apply_emissive(em):
            ts.buffers['emissive'] = em
            ts.original['emissive'] = em.copy()
            self.viewport.makeCurrent()
            try:
                textures.upload_textureset(self.viewport.ctx, ts)
            finally:
                self.viewport.doneCurrent()
            self.viewport.update()
            if self.uv_panel.texset is ts and self.uv_panel.role == 'emissive':
                self.uv_panel.refresh_image(); self.uv_panel.update()

        EmissiveGenDialog(self, ts, apply_emissive).exec()
        self.statusBar().showMessage('Generated glow map for %s' % g['name'])

    def _refresh_skins(self):
        self.skin_combo.blockSignals(True)
        self.skin_combo.clear()
        for s in (self.model.skins or ['']):
            self.skin_combo.addItem(s if s else '(base)')
        # select current
        cur = self.model.skin or ''
        idx = max(0, (self.model.skins or ['']).index(cur)) if cur in (self.model.skins or ['']) else 0
        self.skin_combo.setCurrentIndex(idx)
        self.skin_combo.blockSignals(False)

    def _refresh_groups(self):
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        for g in self.model.draw_groups:
            self.group_combo.addItem(g['name'])
        # default to the largest piece (the body), not the scope (which sorts first)
        groups = self.model.draw_groups
        default = max(range(len(groups)), key=lambda i: groups[i]['faces'].shape[0]) \
            if groups else 0
        self.group_combo.setCurrentIndex(default)
        self.group_combo.blockSignals(False)

    def _switch_skin(self, text):
        if not self.model:
            return
        skin = '' if text == '(base)' else text
        self.open_model(self.model.path, skin=skin)

    def _active_group(self):
        if not self.model:
            return None
        idx = max(0, self.group_combo.currentIndex())
        if idx >= len(self.model.draw_groups):
            idx = 0
        return self.model.draw_groups[idx]

    def _sync_uv_panel(self, reset_view=False):
        g = self._active_group()
        if not g:
            return
        role = brushmod.role_for_mode(self.brush.mode, self.rsettings.use_alpha_spec)
        self.uv_panel.set_target(g['texset'], role, uv_coords=self.uv_all,
                                 faces=g['faces'], reset_view=reset_view)

    # ---- paint sync ----------------------------------------------------
    def _on_uv_painted(self, texset, role):
        # UV panel painted -> the buffer + dirty rect are set; redraw the 3D views
        self.viewport.update()

    # ---- UV layout guide ----------------------------------------------
    def _export_uv_layout(self):
        """Render the active piece's UV wireframe over its current texture to a PNG
        guide. Open it in Photoshop/GIMP, paint a new skin aligned to the UVs, then
        bring it back via 'Replace Active Map from Image' (or compile to DDS)."""
        if not self.model:
            QtWidgets.QMessageBox.information(self, 'UV layout', 'Load a model first.')
            return
        g = self._active_group()
        if not g:
            return
        role = self.uv_panel.role or 'diffuse'
        buf = g['texset'].buffers.get(role)
        h, w = (buf.shape[0], buf.shape[1]) if buf is not None else (1024, 1024)
        w = max(w, 512); h = max(h, 512)

        img = QtGui.QImage(w, h, QtGui.QImage.Format_RGBA8888)
        img.fill(QtGui.QColor(0, 0, 0, 0))
        p = QtGui.QPainter(img)
        # faint texture underlay (so the artist sees what maps where)
        if buf is not None:
            rgb = np.ascontiguousarray(buf[:, :, :3])
            bg = QtGui.QImage(rgb.tobytes(), buf.shape[1], buf.shape[0],
                              buf.shape[1] * 3, QtGui.QImage.Format_RGB888)
            p.setOpacity(0.85)
            p.drawImage(QtCore.QRectF(0, 0, w, h), bg)
            p.setOpacity(1.0)
        # UV wireframe
        uv = self.uv_all
        faces = g['faces']
        flip = self.rsettings.flip_v
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtGui.QPen(QtGui.QColor(0, 230, 120, 220), 1.0))

        def pt(i):
            u, v = float(uv[i, 0]), float(uv[i, 1])
            return QtCore.QPointF(u * w, (1.0 - v) * h if flip else v * h)

        for a, b, c in faces:
            pa, pb, pc = pt(a), pt(b), pt(c)
            p.drawLine(pa, pb); p.drawLine(pb, pc); p.drawLine(pc, pa)
        p.end()

        default = os.path.join(os.path.dirname(g['texset'].roles.get(role, '') or
                                               os.path.join(self.game_root, 'x')),
                               '%s_%s_UV.png' % (g['name'], role))
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Export UV layout', default, 'PNG (*.png)')
        if not path:
            return
        if img.save(path):
            self.statusBar().showMessage('Wrote UV guide %dx%d -> %s' % (w, h, path))
        else:
            QtWidgets.QMessageBox.critical(self, 'Export failed', 'Could not write %s' % path)

    # ---- new skin ------------------------------------------------------
    def _create_new_skin(self):
        """Write the current edits as a complete NEW skin for the whole model:
        skin-tagged textures (<mat>-<skin>_D/_N/...) + cloned .Mat00 materials that
        point at them, laid out under a chosen output base so the tree copies into
        the game."""
        if not self.model:
            QtWidgets.QMessageBox.information(self, 'New skin', 'Load a model first.')
            return
        order = ['diffuse', 'normal', 'roughness', 'spec', 'emissive']
        present = {r for g in self.model.draw_groups for r in g['texset'].buffers}
        roles_present = [r for r in order if r in present]
        dlg = NewSkinDialog(self, roles_present)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        self._write_new_skin(**dlg.result())

    # ---- delete skin ---------------------------------------------------
    def _skin_files(self, skin):
        """Every on-disk file belonging to a user-created '<mat>-<skin>' skin of the
        active model: the skin-tagged textures (<mat>-<skin>_D/_N/...dds) and the cloned
        materials (<mat>-<skin>.Mat00, including PV_/_Hilt siblings). Searches the
        near-the-model textures/materials dirs plus the dirs the model's roles actually
        resolved from. Matches the EXACT skin token so 'Gold' never catches 'Gold2'."""
        model_dir = os.path.dirname(os.path.abspath(self.model.path))
        stems = set()
        for g in self.model.draw_groups:
            stems.add(g['texset'].name)
            for sib in materials.sibling_materials(g['texset'].name, self.game_root, model_dir):
                stems.add(sib)
        tex_dirs, mat_dirs = set(), set()
        for d in materials._search_dirs(self.game_root, model_dir):
            if os.path.isdir(d):
                tex_dirs.add(os.path.normpath(d))
        for d in materials._mat_search_dirs(self.game_root, model_dir):
            if os.path.isdir(d):
                mat_dirs.add(os.path.normpath(d))
        for g in self.model.draw_groups:
            for p in g['texset'].roles.values():
                if p and os.path.isfile(p):
                    tex_dirs.add(os.path.normpath(os.path.dirname(p)))
        out, seen = [], set()

        def collect(dirs, pat):
            for d in dirs:
                try:
                    names = os.listdir(d)
                except OSError:
                    continue
                for nm in names:
                    if pat.match(nm):
                        ap = os.path.normpath(os.path.join(d, nm))
                        if ap.lower() not in seen and os.path.isfile(ap):
                            seen.add(ap.lower()); out.append(ap)

        for stem in stems:
            pre = re.escape('%s-%s' % (stem, skin))
            collect(tex_dirs, re.compile(r'(?i)^' + pre + r'(_[A-Za-z0-9]+)?\.dds$'))
            collect(mat_dirs, re.compile(r'(?i)^' + pre + r'\.Mat00$'))
        return sorted(out)

    def _delete_skin(self):
        """Delete the selected skin and remove its texture + material files from disk."""
        if not self.model:
            QtWidgets.QMessageBox.information(self, 'Delete skin', 'Load a model first.')
            return
        text = self.skin_combo.currentText()
        skin = '' if text == '(base)' else text
        if not skin:
            QtWidgets.QMessageBox.information(
                self, 'Delete skin',
                'Select a skin in the Skin selector first.\nThe base (un-skinned) entry '
                'cannot be deleted.')
            return
        files = self._skin_files(skin)
        if not files:
            QtWidgets.QMessageBox.information(
                self, 'Delete skin',
                'No "%s-..." files were found for skin "%s".\n\nThis is either a built-in '
                'game variant/material (not a skin you created here) or its files live '
                'elsewhere — those are left untouched for safety.' % (
                    self.model.draw_groups[0]['texset'].name if self.model.draw_groups else '',
                    skin))
            return
        listing = '\n'.join('  ' + os.path.relpath(f, self.game_root).replace('\\', '/')
                            for f in files)
        ans = QtWidgets.QMessageBox.warning(
            self, 'Delete skin "%s"?' % skin,
            'This permanently deletes %d file(s):\n\n%s\n\nThis cannot be undone.'
            % (len(files), listing),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel)
        if ans != QtWidgets.QMessageBox.Yes:
            return
        removed, errors = [], []
        for f in files:
            try:
                os.remove(f)
                removed.append(f)
            except OSError as e:
                errors.append('%s: %s' % (os.path.basename(f), e))
        # Drop the stale index entries so the skin disappears from the selector.
        materials._DDS_INDEX.pop(self.game_root, None)
        materials._MAT_INDEX.pop(self.game_root, None)
        # Reload at the base skin so the now-deleted skin is gone from the combo.
        self.open_model(self.model.path, skin='')
        msg = 'Deleted %d file(s) for skin "%s".' % (len(removed), skin)
        if errors:
            msg += '\n\nFailed:\n- ' + '\n- '.join(errors)
            QtWidgets.QMessageBox.warning(self, 'Delete skin', msg)
        else:
            self.statusBar().showMessage(msg)

    def _save_all(self):
        """One button for everything: write ALL current textures + the material(s) for
        the current skin (with the Glow panel's current effects) under the last output
        base. Prompts for skin name / base only the first time."""
        if not self.model:
            QtWidgets.QMessageBox.information(self, 'Save All', 'Load a model first.')
            return
        import re
        st = QtCore.QSettings()
        skin = self.model.skin or st.value('save_skin', '') or ''
        if not skin:
            skin, ok = QtWidgets.QInputDialog.getText(self, 'Save All', 'Skin name:', text='MySkin')
            if not ok:
                return
            skin = re.sub(r'[^A-Za-z0-9_]+', '', skin.strip().replace(' ', '_'))
            if not skin:
                return
        base = st.value('export_base', '') or ''
        if not base or not os.path.isdir(base):
            base = QtWidgets.QFileDialog.getExistingDirectory(
                self, 'Choose output base folder (saved for next time)', base or self.game_root)
            if not base:
                return
        st.setValue('save_skin', skin)
        order = ['diffuse', 'normal', 'roughness', 'spec', 'emissive']
        present = {r for g in self.model.draw_groups for r in g['texset'].buffers}
        roles = [r for r in order if r in present]
        gp = self.glow_panel.current_params()
        shader = self.glow_panel.current_shader()
        glow = (self.glow_panel.element.currentIndex() != 0) or gp.get('fglowscale', 0.0) > 0.0
        self._write_new_skin(skin=skin, base=base, roles=roles, write_mat=True,
                             brightness=1.0, spec=1.0, ref_alpha=True, glow=glow,
                             siblings=True, glow_mode='both', glow_anim=True, glow_params=gp,
                             glow_shader=shader)

    def _write_new_skin(self, skin, base, roles, write_mat, brightness, spec, ref_alpha,
                        glow=False, siblings=True, glow_mode='both', glow_anim=False,
                        glow_scale=3.0, glow_speed=0.8, glow_amount=0.4, glow_params=None,
                        glow_shader=None):
        self._glow_shader = glow_shader   # picked elemental shader (None -> default glow)
        # glow_params (full fX dict from the Glow panel) wins; else just the 3 dialog knobs.
        glow_knobs = dict(glow_params) if glow_params else {
            'fglowscale': glow_scale, 'fglowpulsespeed': glow_speed, 'fglowpulseamount': glow_amount}
        import posixpath
        model_dir = os.path.dirname(os.path.abspath(self.model.path))
        QtCore.QSettings().setValue('export_base', base)
        written, warnings = [], []

        for g in self.model.draw_groups:
            ts = g['texset']
            matname = ts.name                                  # e.g. SN_AWM300 / SN_AWM300_Scope
            texrel = textures.relative_export_path(ts, 'diffuse', self.game_root)
            texreldir = posixpath.dirname(texrel)              # weapons/.../textures
            matreldir = posixpath.dirname(texreldir) + '/materials' \
                if posixpath.basename(texreldir).lower() == 'textures' else texreldir

            # textures
            wrote_emissive = False
            for role in roles:
                if role not in ts.buffers:
                    continue
                buf = ts.buffers[role]
                suffix = textures._ROLE_SUFFIX.get(role, '_D')
                rel = '%s/%s-%s%s.dds' % (texreldir, matname, skin, suffix)
                out = os.path.normpath(os.path.join(base, rel))
                br = brightness if role == 'diffuse' else 1.0
                sp = spec if role == 'diffuse' else 1.0
                try:
                    textures.export_role(ts, role, out, brightness=br, spec=sp,
                                         ref_alpha=ref_alpha and role == 'diffuse')
                    written.append(rel)
                    if role == 'emissive':
                        wrote_emissive = True
                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, 'Write failed', '%s: %s' % (rel, e))
                    return

            # A spec map (colored glow) is derived from the painted emissive to add a
            # light-reactive flare. The 'both' glow mode includes it (emissive bloom +
            # specular flare); 'emissive' mode is pure self-glow only.
            need_spec = glow and glow_mode == 'both'
            spec_bs = None
            if need_spec:
                em = ts.buffers.get('emissive')
                if em is not None:
                    srel = '%s/%s-%s_C.dds' % (texreldir, matname, skin)
                    sout = os.path.normpath(os.path.join(base, srel))
                    try:
                        textures.save_dds(textures.spec_from_emissive(em), sout)
                        written.append(srel)
                        spec_bs = srel.replace('/', '\\')
                    except Exception as e:
                        warnings.append('spec map for %s: %s' % (matname, e))

            # materials (own piece + PV_/non-PV/_Hilt siblings) pointing at these textures
            if write_mat:
                diff_bs = ('%s/%s-%s_D.dds' % (texreldir, matname, skin)).replace('/', '\\')
                em_bs = ('%s/%s-%s_EM.dds' % (texreldir, matname, skin)).replace('/', '\\') \
                    if (wrote_emissive or glow) else None
                # the glow material always keeps the emissive (self-glow); the 'both'
                # mode additionally references the spec map for a light-reactive flare.
                mat_em, mat_sp = em_bs, spec_bs
                self._emit_skin_mat(matname, matname, skin, base, matreldir, model_dir,
                                    diff_bs, mat_em, mat_sp, glow, written, warnings,
                                    glow_anim=glow_anim, glow_knobs=glow_knobs)
                # sibling view materials reuse the SAME skin textures (shared atlas)
                if siblings:
                    for sib in materials.sibling_materials(matname, self.game_root, model_dir):
                        self._emit_skin_mat(sib, matname, skin, base, matreldir, model_dir,
                                            diff_bs, mat_em, mat_sp, glow, written, warnings,
                                            glow_anim=glow_anim, glow_knobs=glow_knobs)

        if 'emissive' in roles and not glow:
            warnings.append("emissive (_EM) textures were written but NOT referenced by "
                            "the materials - the original shader doesn't support a glow "
                            "map. Re-run with 'Make it glow' checked to wire them via "
                            "specular.fx.")
        msg = 'New skin "%s" written under\n%s\n\n%s' % (skin, base, '\n'.join(written))
        if warnings:
            msg += '\n\nWarnings:\n- ' + '\n- '.join(warnings)
        if siblings:
            msg += ('\n\nNote: sibling (PV_/_Hilt) materials point at the same skin '
                    'textures, which is correct when the views share a texture atlas.')
        # Make the new skin appear in the Skin selector immediately (no restart needed).
        if skin and self.model is not None:
            if skin not in (self.model.skins or []):
                self.model.skins = list(self.model.skins or []) + [skin]
            self._refresh_skins()
        QtWidgets.QMessageBox.information(self, 'Saved', msg)

    def _emit_skin_mat(self, mat_stem, tex_stem, skin, base, matreldir, model_dir,
                      diff_bs, em_bs, spec_bs, glow, written, warnings,
                      glow_anim=False, glow_knobs=None):
        """Clone <mat_stem>.Mat00 into <mat_stem>-<skin>.Mat00 pointing at the skin
        textures. A glow skin retargets at specular.fx (static) or specular_glow.fx
        (glowanim - animated pulse, when glow_anim) and is rebuilt CLEAN from that
        shader's declared params (dropping tDecalMap etc.) so the engine accepts it.
        glow_knobs (fGlowScale/Speed/Amount) are written into the glowanim material.
        The caller decides which maps to pass: tEmissiveMap (self-glow, bright in the
        dark) and/or tSpecularMap (light-reactive flare); when a spec map is present we
        broaden the specular power so it reads as a glow, not a pinpoint highlight."""
        orig = materials.find_mat00(mat_stem, self.game_root, model_dir)
        orig_bytes = open(orig, 'rb').read() if orig else None
        try:
            if glow:
                maps = {'tdiffusemap': diff_bs}
                if em_bs:
                    maps['temissivemap'] = em_bs
                if spec_bs:
                    maps['tspecularmap'] = spec_bs
                sp_power = mat00io.GLOW_SPEC_POWER if spec_bs else None
                # picked elemental shader (fire/lightning/glow) wins; else the glowanim/static default
                shader = getattr(self, '_glow_shader', None) or \
                    (mat00io.GLOW_PULSE_SHADER if glow_anim else mat00io.GLOW_SHADER)
                use_knobs = glow_knobs if (glow_anim or getattr(self, '_glow_shader', None)) else None
                data = mat00io.build_glow(shader, orig_bytes, maps, spec_power=sp_power,
                                          scalar_overrides=use_knobs)
                if not orig:
                    warnings.append('no original %s.Mat00 found - built a fresh glow '
                                    'material' % mat_stem)
            elif orig_bytes:
                # keep the original shader -> only swap maps it already declares
                data = mat00io.reskin(orig_bytes, diff_bs, emissive_path=em_bs,
                                      spec_path=spec_bs, shader=None, add_maps=set())
            else:
                data = mat00io.synthesize(diff_bs, shader=mat00io.DEFAULT_WEAPON_SHADER)
                warnings.append('no original %s.Mat00 found - synthesized one' % mat_stem)
            matrel = '%s/%s-%s.Mat00' % (matreldir, mat_stem, skin)
            mout = os.path.normpath(os.path.join(base, matrel))
            os.makedirs(os.path.dirname(mout), exist_ok=True)
            open(mout, 'wb').write(data)
            written.append(matrel)
        except Exception as e:
            warnings.append('material for %s: %s' % (mat_stem, e))

    # ---- export --------------------------------------------------------
    def _export_dialog(self):
        if not self.model:
            QtWidgets.QMessageBox.information(self, 'Export', 'Load a model first.')
            return
        g = self._active_group()
        dlg = ExportDialog(self, g['texset'], self.game_root)
        dlg.exec()


class NormalGenDialog(QtWidgets.QDialog):
    """Auto-generate a normal map from the diffuse, with live preview. Strength /
    blur / invert-Y / height-source sliders update a downscaled preview instantly;
    'Apply to model' (or a live toggle) writes the full-res result via apply_cb."""
    def __init__(self, parent, texset, apply_cb):
        super().__init__(parent)
        self.setWindowTitle('Generate Normal Map - %s' % texset.name)
        self.resize(820, 540)
        self.ts = texset
        self.apply_cb = apply_cb
        self._diffuse = texset.buffers['diffuse']
        self._small = textures.downscale_rgba(self._diffuse, 384)   # fast preview src
        self._qimg = None

        lay = QtWidgets.QHBoxLayout(self)

        # left: preview (resizes with the window)
        self.preview = QtWidgets.QLabel()
        self.preview.setMinimumSize(300, 300)
        self.preview.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                   QtWidgets.QSizePolicy.Expanding)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet('background:#202124;border:1px solid #555;')
        lay.addWidget(self.preview, 1)

        # right: controls
        form = QtWidgets.QFormLayout()
        lay.addLayout(form)
        self.strength = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.strength.setRange(10, 1500); self.strength.setValue(200)   # /100 => 0.1..15
        self.blur = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.blur.setRange(0, 10); self.blur.setValue(0)
        self.invert = QtWidgets.QCheckBox('Invert Y (DirectX-style)')
        self.src = QtWidgets.QComboBox(); self.src.addItems(['luma', 'max'])
        self.live = QtWidgets.QCheckBox('Update model live'); self.live.setChecked(False)
        self.strength_lbl = QtWidgets.QLabel(); self.blur_lbl = QtWidgets.QLabel()
        form.addRow('Strength', self._wrap(self.strength, self.strength_lbl))
        form.addRow('Blur (smooth)', self._wrap(self.blur, self.blur_lbl))
        form.addRow('Height from', self.src)
        form.addRow('', self.invert)
        form.addRow('', self.live)

        for w in (self.strength, self.blur):
            w.valueChanged.connect(self._changed)
        self.invert.toggled.connect(self._changed)
        self.src.currentIndexChanged.connect(self._changed)
        self.live.toggled.connect(self._changed)

        btns = QtWidgets.QDialogButtonBox()
        apply_btn = btns.addButton('Apply to model', QtWidgets.QDialogButtonBox.AcceptRole)
        btns.addButton(QtWidgets.QDialogButtonBox.Close)
        apply_btn.clicked.connect(self._apply_full)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        self._changed()

    def _wrap(self, slider, label):
        w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(slider, 1); h.addWidget(label)
        return w

    def _params(self):
        return dict(strength=self.strength.value() / 100.0, blur=self.blur.value(),
                    invert_y=self.invert.isChecked(), height_source=self.src.currentText())

    def _changed(self, *_):
        p = self._params()
        self.strength_lbl.setText('%.2f' % p['strength'])
        self.blur_lbl.setText(str(p['blur']))
        nm = textures.normal_from_diffuse(self._small, **p)
        rgb = np.ascontiguousarray(nm[:, :, :3])
        self._qimg = QtGui.QImage(rgb.tobytes(), rgb.shape[1], rgb.shape[0],
                                  rgb.shape[1] * 3, QtGui.QImage.Format_RGB888).copy()
        self._show()
        if self.live.isChecked():
            self.apply_cb(textures.normal_from_diffuse(self._diffuse, **p))

    def _show(self):
        if self._qimg is not None:
            self.preview.setPixmap(QtGui.QPixmap.fromImage(self._qimg).scaled(
                self.preview.size(), QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._show()

    def _apply_full(self):
        self.apply_cb(textures.normal_from_diffuse(self._diffuse, **self._params()))


class EmissiveGenDialog(QtWidgets.QDialog):
    """Auto-generate a glow (emissive) map from the diffuse, with live preview.
    Threshold/softness/intensity/spread + feature source + optional color tint pick
    which areas glow and how much; 'Apply to model' writes the full-res _EM map."""
    def __init__(self, parent, texset, apply_cb):
        super().__init__(parent)
        self.setWindowTitle('Generate Glow (Emissive) Map - %s' % texset.name)
        self.resize(820, 540)
        self.ts = texset
        self.apply_cb = apply_cb
        self._diffuse = texset.buffers['diffuse']
        self._small = textures.downscale_rgba(self._diffuse, 384)
        self._tint = list(parent.brush.glow_color) if hasattr(parent, 'brush') else [40, 230, 255]
        self._qimg = None

        lay = QtWidgets.QHBoxLayout(self)
        self.preview = QtWidgets.QLabel()
        self.preview.setMinimumSize(300, 300)
        self.preview.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                   QtWidgets.QSizePolicy.Expanding)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet('background:#101012;border:1px solid #555;')
        lay.addWidget(self.preview, 1)

        form = QtWidgets.QFormLayout()
        lay.addLayout(form)
        self.source = QtWidgets.QComboBox()
        self.source.addItems(['bright', 'dark', 'saturation', 'color'])
        self.threshold = self._sl(0, 100, 65)
        self.softness = self._sl(1, 60, 18)
        self.intensity = self._sl(10, 400, 150)
        self.spread = self._sl(0, 16, 2)
        self.use_tint = QtWidgets.QCheckBox('Recolor glow to tint')
        self.tint_btn = QtWidgets.QToolButton(); self.tint_btn.setText('Tint color')
        self.tint_btn.clicked.connect(self._pick_tint)
        self.live = QtWidgets.QCheckBox('Update model live')
        self.thr_lbl = QtWidgets.QLabel(); self.soft_lbl = QtWidgets.QLabel()
        self.int_lbl = QtWidgets.QLabel(); self.spr_lbl = QtWidgets.QLabel()
        form.addRow('Glow from', self.source)
        form.addRow('Threshold', self._wrap(self.threshold, self.thr_lbl))
        form.addRow('Softness', self._wrap(self.softness, self.soft_lbl))
        form.addRow('Intensity', self._wrap(self.intensity, self.int_lbl))
        form.addRow('Spread (feather)', self._wrap(self.spread, self.spr_lbl))
        form.addRow('', self.use_tint)
        form.addRow('', self.tint_btn)
        form.addRow('', self.live)

        for s in (self.threshold, self.softness, self.intensity, self.spread):
            s.valueChanged.connect(self._changed)
        self.source.currentIndexChanged.connect(self._changed)
        self.use_tint.toggled.connect(self._changed)
        self.live.toggled.connect(self._changed)

        btns = QtWidgets.QDialogButtonBox()
        ab = btns.addButton('Apply to model', QtWidgets.QDialogButtonBox.AcceptRole)
        btns.addButton(QtWidgets.QDialogButtonBox.Close)
        ab.clicked.connect(self._apply_full)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        self._update_tint_btn()
        self._changed()

    def _sl(self, lo, hi, val):
        s = QtWidgets.QSlider(QtCore.Qt.Horizontal); s.setRange(lo, hi); s.setValue(val)
        return s

    def _wrap(self, slider, label):
        w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(slider, 1); h.addWidget(label)
        return w

    def _pick_tint(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(*self._tint), self, 'Glow tint')
        if c.isValid():
            self._tint = [c.red(), c.green(), c.blue()]
            self._update_tint_btn(); self._changed()

    def _update_tint_btn(self):
        self.tint_btn.setStyleSheet('QToolButton{background:rgb(%d,%d,%d);}' % tuple(self._tint))

    def _params(self):
        return dict(threshold=self.threshold.value() / 100.0,
                    softness=self.softness.value() / 100.0,
                    intensity=self.intensity.value() / 100.0,
                    spread=self.spread.value(),
                    source=self.source.currentText(),
                    use_tint=self.use_tint.isChecked(),
                    tint_color=tuple(self._tint))

    def _changed(self, *_):
        p = self._params()
        self.thr_lbl.setText('%.2f' % p['threshold'])
        self.soft_lbl.setText('%.2f' % p['softness'])
        self.int_lbl.setText('%.2f' % p['intensity'])
        self.spr_lbl.setText(str(p['spread']))
        em = textures.emissive_from_diffuse(self._small, **p)
        rgb = np.ascontiguousarray(em[:, :, :3])
        self._qimg = QtGui.QImage(rgb.tobytes(), rgb.shape[1], rgb.shape[0],
                                  rgb.shape[1] * 3, QtGui.QImage.Format_RGB888).copy()
        self._show()
        if self.live.isChecked():
            self.apply_cb(textures.emissive_from_diffuse(self._diffuse, **p))

    def _show(self):
        if self._qimg is not None:
            self.preview.setPixmap(QtGui.QPixmap.fromImage(self._qimg).scaled(
                self.preview.size(), QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._show()

    def _apply_full(self):
        self.apply_cb(textures.emissive_from_diffuse(self._diffuse, **self._params()))


class GlowPanel(QtWidgets.QWidget):
    """Live glow-properties panel. Edits Intensity / Speed / Pulse, drives the preview
    in real time, and writes the values back into the loaded skin's EXISTING
    specular_glow.fx material(s) - no new skin/material required. The same three knobs
    are fGlowScale / fGlowPulseSpeed / fGlowPulseAmount in the .Mat00."""
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.rs = win.rsettings
        self._mats = []
        form = QtWidgets.QFormLayout(self)

        # Animation preset picker: choose a ready-made look and it fills in all knobs.
        self.preset = QtWidgets.QComboBox()
        self.preset.addItem('— Preset —')
        for name in GLOW_PRESETS:
            self.preset.addItem(name)
        self.preset.activated.connect(self._on_preset)
        form.addRow('Preset', self.preset)

        # Element / shader: Glow (specular_glow), Fire (specular_fire), Lightning (specular_electric).
        self.element = QtWidgets.QComboBox()
        self.element.addItems(['Glow', 'Fire 🔥', 'Lightning ⚡'])
        self.element.setToolTip('Picks the in-game shader. Fire uses flame.dds, Lightning uses '
                                'lightning.dds as the emissive (use the Pattern picker).')
        self.element.currentIndexChanged.connect(self._on_element)
        form.addRow('Element', self.element)

        self.anim_cb = QtWidgets.QCheckBox('Animate (master switch for all motion)')
        self.anim_cb.setChecked(self.rs.glow_anim)
        self.anim_cb.toggled.connect(self._on_anim)
        form.addRow(self.anim_cb)

        self.intensity = self._spin(0.0, 8.0, 0.25, self.rs.glow)
        self.speed = self._spin(0.0, 5.0, 0.1, self.rs.glow_speed)
        self.pulse = self._spin(0.0, 1.0, 0.05, self.rs.glow_amount)
        self.bloom = self._spin(0.0, 0.05, 0.001, 0.006)
        self.bloom.setDecimals(3)
        self.bloom.setToolTip('In-shader glow bleed radius (fGlowBlur). Makes first-person '
                              'weapons glow since the engine post-process bloom skips the '
                              'view model. Preview-only here; affects the saved material.')
        # Moving flow pattern (a band sweeping across the weapon).
        self.flow_amount = self._spin(0.0, 1.0, 0.05, 0.0)
        self.flow_speed = self._spin(-3.0, 3.0, 0.05, 0.3)
        self.flow_freq = self._spin(0.0, 20.0, 0.5, 3.0)
        self.flow_dir = QtWidgets.QComboBox(); self.flow_dir.addItems(['U / X', 'V / Y', 'Z (obj)'])
        self.flow_dir.setCurrentIndex(1)
        self.flow_space = QtWidgets.QComboBox(); self.flow_space.addItems(['UV (per-texture)', 'Object space (world)'])
        for w in (self.intensity, self.speed, self.pulse, self.bloom,
                  self.flow_amount, self.flow_speed, self.flow_freq):
            w.valueChanged.connect(self._on_change)
        self.flow_dir.currentIndexChanged.connect(self._on_change)
        self.flow_space.currentIndexChanged.connect(self._on_change)
        form.addRow('Intensity (fGlowScale)', self.intensity)
        form.addRow('Speed Hz (fGlowPulseSpeed)', self.speed)
        form.addRow('Pulse 0..1 (fGlowPulseAmount)', self.pulse)
        form.addRow('Bloom radius (fGlowBlur)', self.bloom)
        sep = QtWidgets.QLabel('— Moving pattern —'); sep.setStyleSheet('color:#888;')
        form.addRow(sep)
        self.flow_amount.setToolTip('0 = off (plain glow); >0 = a bright band sweeps across '
                                    'the glow areas. Enable Anim to see it move.')
        self.flow_space.setToolTip('UV = flows per texture island (can differ across the weapon). '
                                   'Object space = flows one consistent physical direction (triplanar).')
        form.addRow('Flow space (fFlowSpace)', self.flow_space)
        form.addRow('Flow amount (fFlowAmount)', self.flow_amount)
        form.addRow('Flow speed Hz (fFlowSpeed)', self.flow_speed)
        form.addRow('Flow bands (fFlowFreq)', self.flow_freq)
        form.addRow('Flow axis (fFlowDir)', self.flow_dir)

        # Fresnel rim / scrolling energy / hue cycle.
        self.rim_scale = self._spin(0.0, 4.0, 0.05, 0.0)
        self.rim_power = self._spin(0.5, 8.0, 0.25, 3.0)
        self.scroll_speed = self._spin(-2.0, 2.0, 0.05, 0.0)
        self.hue_speed = self._spin(0.0, 2.0, 0.05, 0.0)
        self.emissive_tile = self._spin(0.005, 64.0, 0.05, 1.0)
        self.emissive_tile.setDecimals(3)
        for w in (self.rim_scale, self.rim_power, self.scroll_speed, self.hue_speed, self.emissive_tile):
            w.valueChanged.connect(self._on_change)
        sep2 = QtWidgets.QLabel('— Effects —'); sep2.setStyleSheet('color:#888;')
        form.addRow(sep2)
        self.rim_scale.setToolTip('View-dependent glowing edge (brightens at grazing angles).')
        self.scroll_speed.setToolTip('Pans the emissive along the flow axis = energy flowing. 0 = off.')
        self.hue_speed.setToolTip('Cycles the glow color through the spectrum. 0 = off. Enable Anim.')
        self.emissive_tile.setToolTip('Tiles the emissive/grid pattern. UV space: 1 = as authored, '
                                      'higher = smaller squares. OBJECT space: model units are large, '
                                      'so use SMALL values (~0.02-0.2) or the grid is too dense to see.')
        form.addRow('Pattern tile (fEmissiveTile)', self.emissive_tile)
        form.addRow('Rim glow (fRimScale)', self.rim_scale)
        form.addRow('Rim power (fRimPower)', self.rim_power)
        form.addRow('Scroll energy (fScrollSpeed)', self.scroll_speed)
        form.addRow('Hue cycle Hz (fHueSpeed)', self.hue_speed)

        # Pattern picker (drops a ready tileable pattern onto the emissive) + color tint.
        sep3 = QtWidgets.QLabel('— Pattern —'); sep3.setStyleSheet('color:#888;')
        form.addRow(sep3)
        self._tint = (1.0, 1.0, 1.0)
        self.pattern_combo = QtWidgets.QComboBox()
        self.pattern_combo.addItem('(keep current emissive)')
        self._pattern_paths = []
        try:
            for f in sorted(os.listdir(PATTERN_DIR)):
                if f.lower().endswith('.dds'):
                    self._pattern_paths.append(os.path.join(PATTERN_DIR, f))
                    self.pattern_combo.addItem(os.path.splitext(f)[0])
        except OSError:
            pass
        self.pattern_combo.activated.connect(self._on_pattern)
        self.pattern_combo.setToolTip('Drop a ready pattern onto the active piece\'s emissive map. '
                                      'Then use Pattern tile / Scroll / Color to taste.')
        self.color_btn = QtWidgets.QPushButton('Pattern color…')
        self.color_btn.clicked.connect(self._pick_color)
        form.addRow('Pattern', self.pattern_combo)
        form.addRow('Color', self.color_btn)

        self.info = QtWidgets.QLabel('Load a model to edit its glow material.')
        self.info.setWordWrap(True)
        self.info.setStyleSheet('color:#aaa;')
        form.addRow(self.info)

        row = QtWidgets.QHBoxLayout()
        self.reload_btn = QtWidgets.QPushButton('Reload from material')
        self.save_btn = QtWidgets.QPushButton('Save glow to material')
        self.reload_btn.clicked.connect(self.reload_from_material)
        self.save_btn.clicked.connect(self.save_to_materials)
        row.addWidget(self.reload_btn)
        row.addWidget(self.save_btn)
        holder = QtWidgets.QWidget(); holder.setLayout(row)
        form.addRow(holder)
        # One button that saves EVERYTHING (all textures + the material with these effects).
        self.saveall_btn = QtWidgets.QPushButton('💾  Save All  (textures + material)')
        self.saveall_btn.setStyleSheet('font-weight:bold; padding:6px;')
        self.saveall_btn.clicked.connect(self.win._save_all)
        form.addRow(self.saveall_btn)
        self.refresh_targets()

    def _spin(self, lo, hi, step, val):
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(lo, hi); s.setSingleStep(step); s.setDecimals(2); s.setValue(float(val))
        return s

    # ---- element / shader ---------------------------------------------
    _ELEMENT_SHADERS = None   # filled lazily (mat00io constants)

    def current_shader(self):
        import mat00io as _m
        return [_m.GLOW_PULSE_SHADER, _m.SPECULAR_FIRE_SHADER, _m.SPECULAR_ELECTRIC_SHADER][self.element.currentIndex()]

    # Element-appropriate defaults + the source texture each one needs.
    _ELEMENT_DEFAULTS = {
        1: dict(glow=3.0, glow_speed=6.0, glow_amount=0.35, scroll_speed=0.6, emissive_tile=2.0,
                flow_amount=0.0, hue_speed=0.0, rim_scale=0.0, anim=True),   # fire
        2: dict(glow=3.0, glow_speed=1.5, glow_amount=0.85, scroll_speed=0.0, emissive_tile=2.0,
                flow_amount=0.0, hue_speed=0.0, rim_scale=0.0, anim=True),   # lightning
    }
    _ELEMENT_PATTERN = {1: 'flame', 2: 'lightning'}

    def _on_element(self, idx):
        self.rs.effect_mode = float(idx)
        if idx in self._ELEMENT_DEFAULTS:
            # fill in fire/lightning-appropriate knobs so the preview actually shows it...
            self.apply_params(self._ELEMENT_DEFAULTS[idx])
            # ...and load the source texture the effect samples (flame / lightning) as emissive
            if self.win.model is not None:
                want = self._ELEMENT_PATTERN[idx]
                for i, p in enumerate(self._pattern_paths):
                    if os.path.splitext(os.path.basename(p))[0].lower() == want:
                        self.pattern_combo.blockSignals(True); self.pattern_combo.setCurrentIndex(i + 1); self.pattern_combo.blockSignals(False)
                        self.win._apply_pattern(p)
                        break
        self.win._set_glow_anim(self.anim_cb.isChecked())
        self.win.viewport.update()

    # ---- pattern + color ----------------------------------------------
    def _on_pattern(self, idx):
        if idx <= 0 or idx > len(self._pattern_paths):
            return
        self.win._apply_pattern(self._pattern_paths[idx - 1])

    def _pick_color(self):
        cur = QtGui.QColor.fromRgbF(*self._tint)
        c = QtWidgets.QColorDialog.getColor(cur, self, 'Pattern / glow color')
        if c.isValid():
            self._tint = (c.redF(), c.greenF(), c.blueF())
            self.color_btn.setStyleSheet('background:%s; padding:4px;' % c.name())
            # A fixed color and the rainbow cycle conflict - the cycle would override the
            # chosen color, so turn the cycle off when the user picks a color.
            if self.hue_speed.value() != 0.0:
                self.hue_speed.blockSignals(True); self.hue_speed.setValue(0.0); self.hue_speed.blockSignals(False)
            self.rs.glow_tint = self._tint
            self._on_change()   # pushes tint + hue=0 to rsettings/preview

    # ---- presets -------------------------------------------------------
    def _on_preset(self, idx):
        if idx <= 0:
            return
        name = self.preset.itemText(idx)
        params = GLOW_PRESETS.get(name)
        if params:
            self.apply_params(params)

    def apply_params(self, p):
        """Set every glow widget from a params dict (preset or loaded material), then
        push to the preview in one shot."""
        spins = {'glow': self.intensity, 'glow_speed': self.speed, 'glow_amount': self.pulse,
                 'bloom': self.bloom, 'flow_amount': self.flow_amount, 'flow_speed': self.flow_speed,
                 'flow_freq': self.flow_freq, 'rim_scale': self.rim_scale, 'rim_power': self.rim_power,
                 'scroll_speed': self.scroll_speed, 'hue_speed': self.hue_speed,
                 'emissive_tile': self.emissive_tile}
        combos = {'flow_dir': self.flow_dir, 'flow_space': self.flow_space}
        widgets = list(spins.values()) + list(combos.values()) + [self.anim_cb]
        for w in widgets:
            w.blockSignals(True)
        for key, w in spins.items():
            if key in p:
                w.setValue(float(p[key]))
        for key, w in combos.items():
            if key in p:
                w.setCurrentIndex(int(p[key]))
        if 'anim' in p:
            self.anim_cb.setChecked(bool(p['anim']))
        for w in widgets:
            w.blockSignals(False)
        # apply animate state (master switch) + push all values to rsettings/preview
        self.win._set_glow_anim(self.anim_cb.isChecked())
        self._on_change()

    def current_params(self):
        """Full set of shader scalar params from the current widget values (lowercase
        fX names) - used by both Save-to-material and the unified Save All."""
        return {'fglowscale': self.intensity.value(), 'fglowpulsespeed': self.speed.value(),
                'fglowpulseamount': self.pulse.value(), 'fglowblur': self.bloom.value(),
                'fflowamount': self.flow_amount.value(), 'fflowspeed': self.flow_speed.value(),
                'fflowfreq': self.flow_freq.value(), 'fflowdir': float(self.flow_dir.currentIndex()),
                'frimscale': self.rim_scale.value(), 'frimpower': self.rim_power.value(),
                'fscrollspeed': self.scroll_speed.value(), 'fhuespeed': self.hue_speed.value(),
                'femissivetile': self.emissive_tile.value(),
                'fflowspace': float(self.flow_space.currentIndex()),
                'vglowtint': tuple(self._tint)}

    # ---- live preview --------------------------------------------------
    def _on_anim(self, on):
        self.win._set_glow_anim(on)        # starts/stops the viewport pulse + syncs toolbar

    def _on_change(self, *_):
        self.rs.glow = self.intensity.value()
        self.rs.glow_speed = self.speed.value()
        self.rs.glow_amount = self.pulse.value()
        self.rs.flow_amount = self.flow_amount.value()
        self.rs.flow_speed = self.flow_speed.value()
        self.rs.flow_freq = self.flow_freq.value()
        self.rs.flow_dir = float(self.flow_dir.currentIndex())
        self.rs.flow_space = float(self.flow_space.currentIndex())
        self.rs.rim_scale = self.rim_scale.value()
        self.rs.rim_power = self.rim_power.value()
        self.rs.scroll_speed = self.scroll_speed.value()
        self.rs.hue_speed = self.hue_speed.value()
        self.rs.emissive_tile = self.emissive_tile.value()
        self.rs.glow_tint = self._tint
        self.win._sync_glow_widgets()      # mirror into the toolbar slider/checkbox
        self.win.viewport.refresh_animation()  # flow needs the clock running too
        self.win.viewport.update()

    def sync_from_settings(self):
        """Pull values back from rsettings (e.g. when the toolbar slider moved)."""
        for w, val in ((self.intensity, self.rs.glow), (self.speed, self.rs.glow_speed),
                       (self.pulse, self.rs.glow_amount)):
            w.blockSignals(True); w.setValue(float(val)); w.blockSignals(False)
        self.anim_cb.blockSignals(True); self.anim_cb.setChecked(self.rs.glow_anim)
        self.anim_cb.blockSignals(False)

    # ---- material binding ----------------------------------------------
    def refresh_targets(self):
        """Re-scan which glow material(s) the loaded model/skin maps to."""
        self._mats = self.win._find_glow_materials()
        has = bool(self._mats)
        self.save_btn.setEnabled(has)
        self.reload_btn.setEnabled(has)
        if has:
            names = ', '.join(os.path.basename(p) for p, _ in self._mats)
            self.info.setText('Editing %d glow material(s): %s' % (len(self._mats), names))
            self.reload_from_material()
        elif self.win.model is not None:
            self.info.setText('This model/skin has no specular_glow.fx material. The sliders '
                              'still drive the preview; use Create New Skin → glowanim to '
                              'make one, then Save here edits it in place.')
        else:
            self.info.setText('Load a model to edit its glow material.')

    def reload_from_material(self):
        if not self._mats:
            return
        path, shader = self._mats[0]
        # reflect which element shader this material uses
        eidx = {mat00io.GLOW_PULSE_SHADER.lower(): 0, mat00io.SPECULAR_FIRE_SHADER.lower(): 1,
                mat00io.SPECULAR_ELECTRIC_SHADER.lower(): 2}.get(shader.lower(), 0)
        self.element.blockSignals(True); self.element.setCurrentIndex(eidx); self.element.blockSignals(False)
        self.rs.effect_mode = float(eidx)
        try:
            _, _, props = mat00io.parse(open(path, 'rb').read())
        except Exception as e:
            self.info.setText('Could not read %s: %s' % (os.path.basename(path), e))
            return
        d = {n.lower(): v for n, _, v in props}
        ws = (self.intensity, self.speed, self.pulse, self.bloom,
              self.flow_amount, self.flow_speed, self.flow_freq, self.flow_dir,
              self.rim_scale, self.rim_power, self.scroll_speed, self.hue_speed,
              self.emissive_tile, self.flow_space)
        for w in ws:
            w.blockSignals(True)
        if 'fglowscale' in d:       self.intensity.setValue(float(d['fglowscale']))
        if 'fglowpulsespeed' in d:  self.speed.setValue(float(d['fglowpulsespeed']))
        if 'fglowpulseamount' in d: self.pulse.setValue(float(d['fglowpulseamount']))
        if 'fglowblur' in d:        self.bloom.setValue(float(d['fglowblur']))
        if 'fflowamount' in d:      self.flow_amount.setValue(float(d['fflowamount']))
        if 'fflowspeed' in d:       self.flow_speed.setValue(float(d['fflowspeed']))
        if 'fflowfreq' in d:        self.flow_freq.setValue(float(d['fflowfreq']))
        if 'fflowdir' in d:         self.flow_dir.setCurrentIndex(max(0, min(2, int(round(float(d['fflowdir']))))))
        if 'fflowspace' in d:       self.flow_space.setCurrentIndex(1 if float(d['fflowspace']) >= 0.5 else 0)
        if 'frimscale' in d:        self.rim_scale.setValue(float(d['frimscale']))
        if 'frimpower' in d:        self.rim_power.setValue(float(d['frimpower']))
        if 'fscrollspeed' in d:     self.scroll_speed.setValue(float(d['fscrollspeed']))
        if 'fhuespeed' in d:        self.hue_speed.setValue(float(d['fhuespeed']))
        if 'femissivetile' in d:    self.emissive_tile.setValue(float(d['femissivetile']))
        if 'vglowtint' in d and isinstance(d['vglowtint'], (tuple, list)) and len(d['vglowtint']) >= 3:
            t = d['vglowtint']
            self._tint = (float(t[0]), float(t[1]), float(t[2]))
            c = QtGui.QColor.fromRgbF(*[min(1.0, max(0.0, x)) for x in self._tint])
            self.color_btn.setStyleSheet('background:%s; padding:4px;' % c.name())
        for w in ws:
            w.blockSignals(False)
        self._on_change()

    def save_to_materials(self):
        if not self._mats:
            return
        over = self.current_params()
        shader = self.current_shader()   # retarget to the chosen element (glow/fire/lightning)
        done, failed = [], []
        for path, _ in self._mats:
            try:
                orig = open(path, 'rb').read()
                # rebuild on the chosen shader, keeping the material's existing textures,
                # writing the params that shader declares (filtered by GLOW_PARAMS).
                newdata = mat00io.build_glow(shader, orig, {}, scalar_overrides=over)
                open(path, 'wb').write(newdata)
                done.append(os.path.basename(path))
            except Exception as e:
                failed.append('%s: %s' % (os.path.basename(path), e))
        msg = 'Saved glow (intensity=%.2f speed=%.2fHz pulse=%.2f) into:\n- %s' % (
            over['fglowscale'], over['fglowpulsespeed'], over['fglowpulseamount'],
            '\n- '.join(done) or '(none)')
        if failed:
            msg += '\n\nFailed:\n- ' + '\n- '.join(failed)
        QtWidgets.QMessageBox.information(self, 'Glow saved', msg)


class NewSkinDialog(QtWidgets.QDialog):
    """Collect parameters for 'Create New Skin': a skin name, output base, which
    maps to write, whether to clone the .Mat00, and diffuse brightness/spec/ref."""
    def __init__(self, parent, roles_present):
        super().__init__(parent)
        self.setWindowTitle('Create New Skin')
        self.resize(560, 0)
        lay = QtWidgets.QFormLayout(self)

        self.name = QtWidgets.QLineEdit('MySkin')
        lay.addRow('Skin name', self.name)

        base = QtCore.QSettings().value('export_base', '') or \
            os.path.normpath(os.path.join(parent.game_root, '..', 'output'))
        row = QtWidgets.QHBoxLayout()
        self.base_edit = QtWidgets.QLineEdit(base)
        bb = QtWidgets.QToolButton(); bb.setText('Browse...')
        bb.clicked.connect(self._browse)
        row.addWidget(self.base_edit, 1); row.addWidget(bb)
        lay.addRow('Output base folder', row)

        self.role_cbs = {}
        rolebox = QtWidgets.QHBoxLayout()
        for role in roles_present:
            cb = QtWidgets.QCheckBox(role)
            cb.setChecked(role in ('diffuse', 'normal', 'roughness', 'spec', 'emissive'))
            if role == 'diffuse':
                cb.setChecked(True); cb.setEnabled(False)
            self.role_cbs[role] = cb
            rolebox.addWidget(cb)
        lay.addRow('Write maps', rolebox)

        self.mat_cb = QtWidgets.QCheckBox('Write cloned .Mat00 materials')
        self.mat_cb.setChecked(True)
        lay.addRow('', self.mat_cb)

        self.glow_cb = QtWidgets.QCheckBox(
            'Make it glow  (retarget material at specular.fx)')
        self.glow_cb.setChecked('emissive' in roles_present)
        lay.addRow('', self.glow_cb)

        self.glow_mode = QtWidgets.QComboBox()
        # index 0 -> 'both' (default): emissive bloom + broad specular flare under light
        # index 1 -> 'emissive':       pure self-glow (bright in the dark only)
        self.glow_mode.addItems(['Glow in dark + flare under light (emissive + specular)',
                                 'Self-glow only (emissive - bright in the dark)'])
        self.glow_mode.setEnabled(self.glow_cb.isChecked())
        self.glow_cb.toggled.connect(self.glow_mode.setEnabled)
        lay.addRow('Glow style', self.glow_mode)

        # Which glow shader to retarget the material at: static specular.fx, or the
        # animated specular_glow.fx ("glowanim") whose emissive pulses + blooms in-game.
        rs = getattr(parent, 'rsettings', None)
        self.glow_shader = QtWidgets.QComboBox()
        self.glow_shader.addItems(['Static glow (specular.fx)',
                                   'Animated pulse - glowanim (specular_glow.fx)'])
        self.glow_shader.setCurrentIndex(1 if (rs and rs.glow_anim) else 0)
        self.glow_shader.setEnabled(self.glow_cb.isChecked())
        self.glow_cb.toggled.connect(self.glow_shader.setEnabled)
        lay.addRow('Glow shader', self.glow_shader)

        # glowanim pulse knobs (written into the material as fGlowScale/Speed/Amount).
        # Seed from the live preview settings so "what you see is what you export".
        self.gscale = QtWidgets.QDoubleSpinBox(); self.gscale.setRange(0.0, 8.0)
        self.gscale.setSingleStep(0.25); self.gscale.setValue(float(rs.glow) if rs else 3.0)
        self.gspeed = QtWidgets.QDoubleSpinBox(); self.gspeed.setRange(0.0, 5.0)
        self.gspeed.setSingleStep(0.1); self.gspeed.setValue(float(rs.glow_speed) if rs else 0.8)
        self.gamount = QtWidgets.QDoubleSpinBox(); self.gamount.setRange(0.0, 1.0)
        self.gamount.setSingleStep(0.05); self.gamount.setValue(float(rs.glow_amount) if rs else 0.4)
        prow = QtWidgets.QHBoxLayout()
        for lbl, w in (('Intensity', self.gscale), ('Speed Hz', self.gspeed), ('Pulse', self.gamount)):
            prow.addWidget(QtWidgets.QLabel(lbl)); prow.addWidget(w)
        self.pulse_row = QtWidgets.QWidget(); self.pulse_row.setLayout(prow)

        def _sync_pulse():
            self.pulse_row.setEnabled(self.glow_cb.isChecked()
                                      and self.glow_shader.currentIndex() == 1)
        self.glow_cb.toggled.connect(_sync_pulse)
        self.glow_shader.currentIndexChanged.connect(_sync_pulse)
        _sync_pulse()
        lay.addRow('Pulse', self.pulse_row)

        self.sib_cb = QtWidgets.QCheckBox(
            'Also write sibling view materials (PV_ first-person / non-PV / _Hilt)')
        self.sib_cb.setChecked(True)
        lay.addRow('', self.sib_cb)

        self.bright = QtWidgets.QDoubleSpinBox(); self.bright.setRange(0.1, 3.0)
        self.bright.setSingleStep(0.05); self.bright.setValue(1.0)
        self.spec = QtWidgets.QDoubleSpinBox(); self.spec.setRange(0.0, 3.0)
        self.spec.setSingleStep(0.05); self.spec.setValue(1.0)
        self.refalpha = QtWidgets.QCheckBox('Copy spec alpha from original (diffuse)')
        self.refalpha.setChecked(True)
        lay.addRow('Brightness (RGB x)', self.bright)
        lay.addRow('Spec (alpha x)', self.spec)
        lay.addRow('', self.refalpha)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText('Create')
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)

    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Select output base folder', self.base_edit.text())
        if d:
            self.base_edit.setText(d)

    def _ok(self):
        import re
        raw = self.name.text().strip()
        skin = re.sub(r'[^A-Za-z0-9_]+', '', raw.replace(' ', '_'))
        if not skin:
            QtWidgets.QMessageBox.warning(self, 'New skin', 'Enter a valid skin name.')
            return
        if not self.base_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, 'New skin', 'Choose an output base folder.')
            return
        self._skin = skin
        self.accept()

    def result(self):
        return dict(
            skin=self._skin,
            base=self.base_edit.text().strip(),
            roles=[r for r, cb in self.role_cbs.items() if cb.isChecked()],
            write_mat=self.mat_cb.isChecked(),
            brightness=self.bright.value(),
            spec=self.spec.value(),
            ref_alpha=self.refalpha.isChecked(),
            glow=self.glow_cb.isChecked(),
            siblings=self.sib_cb.isChecked(),
            glow_mode=('emissive' if self.glow_mode.currentIndex() == 1 else 'both'),
            glow_anim=(self.glow_shader.currentIndex() == 1),
            glow_scale=self.gscale.value(),
            glow_speed=self.gspeed.value(),
            glow_amount=self.gamount.value())


class ExportDialog(QtWidgets.QDialog):
    """Export the active piece's maps to DDS under a chosen OUTPUT BASE folder,
    recreating each texture's game-relative subpath (e.g.
    <base>/weapons/3_melee/textures/Melee_HandAxe_D.dds) so the tree can be copied
    straight into the game."""
    def __init__(self, parent, texset, game_root):
        super().__init__(parent)
        self.setWindowTitle('Export / Compile DDS - %s' % texset.name)
        self.resize(640, 320)
        self.texset = texset
        self.game_root = game_root
        self.rows = []

        lay = QtWidgets.QVBoxLayout(self)

        # output base folder (remembered)
        base = QtCore.QSettings().value('export_base', '') or \
            os.path.normpath(os.path.join(game_root, '..', 'output'))
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel('Output base folder:'))
        self.base_edit = QtWidgets.QLineEdit(base)
        self.base_edit.textChanged.connect(self._refresh_previews)
        bb = QtWidgets.QToolButton(); bb.setText('Browse...')
        bb.clicked.connect(self._browse_base)
        top.addWidget(self.base_edit, 1); top.addWidget(bb)
        lay.addLayout(top)

        form = QtWidgets.QGridLayout()
        lay.addLayout(form)
        for c, t in enumerate(('Export', 'Map', 'Game-relative path -> full output path')):
            form.addWidget(QtWidgets.QLabel('<b>%s</b>' % t), 0, c)
        r = 1
        for role in ('diffuse', 'normal', 'roughness', 'spec', 'emissive'):
            if role not in texset.buffers:
                continue
            cb = QtWidgets.QCheckBox(); cb.setChecked(role == 'diffuse')
            rel = textures.relative_export_path(texset, role, game_root)
            preview = QtWidgets.QLabel()
            preview.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            preview.setWordWrap(True)
            form.addWidget(cb, r, 0)
            form.addWidget(QtWidgets.QLabel(role), r, 1)
            form.addWidget(preview, r, 2)
            self.rows.append((role, cb, rel, preview))
            r += 1

        opts = QtWidgets.QFormLayout()
        lay.addLayout(opts)
        self.bright = QtWidgets.QDoubleSpinBox(); self.bright.setRange(0.1, 3.0)
        self.bright.setSingleStep(0.05); self.bright.setValue(1.0)
        self.spec = QtWidgets.QDoubleSpinBox(); self.spec.setRange(0.0, 3.0)
        self.spec.setSingleStep(0.05); self.spec.setValue(1.0)
        self.refalpha = QtWidgets.QCheckBox('Copy spec alpha from original (diffuse)')
        self.refalpha.setChecked(True)
        opts.addRow('Brightness (RGB x)', self.bright)
        opts.addRow('Spec (alpha x)', self.spec)
        opts.addRow('', self.refalpha)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText('Export')
        btns.accepted.connect(self._export)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._refresh_previews()

    def _full_path(self, rel):
        return os.path.normpath(os.path.join(self.base_edit.text().strip(), rel))

    def _refresh_previews(self):
        for role, cb, rel, preview in self.rows:
            preview.setText('%s\n  -> %s' % (rel, self._full_path(rel)))

    def _browse_base(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Select output base folder', self.base_edit.text())
        if d:
            self.base_edit.setText(d)

    def _export(self):
        base = self.base_edit.text().strip()
        if not base:
            QtWidgets.QMessageBox.warning(self, 'Export', 'Choose an output base folder.')
            return
        QtCore.QSettings().setValue('export_base', base)
        done = []
        for role, cb, rel, preview in self.rows:
            if not cb.isChecked():
                continue
            out = self._full_path(rel)
            try:
                bright = self.bright.value() if role == 'diffuse' else 1.0
                spec = self.spec.value() if role == 'diffuse' else 1.0
                textures.export_role(self.texset, role, out, brightness=bright,
                                     spec=spec, ref_alpha=self.refalpha.isChecked())
                done.append(rel)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, 'Export failed', '%s: %s' % (role, e))
                return
        if done:
            QtWidgets.QMessageBox.information(
                self, 'Export', 'Wrote under\n%s\n\n%s' % (base, '\n'.join(done)))
        self.accept()

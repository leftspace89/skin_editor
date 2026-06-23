"""gl_viewport.py - the 4-pane Blender-style 3D viewport.

A single QOpenGLWidget owns one moderngl context and renders the scene four times
into quadrant sub-rects (Top / Front / Right ortho + an orbit perspective pane).
The 3D panes are navigation-only (orbit/pan/zoom + draggable splitters); texture
painting happens in the 2D UV/Texture panel, not here.
"""
import numpy as np
import moderngl
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtOpenGLWidgets import QOpenGLWidget

import camera as cammod
import scene as scenemod


class GLViewport(QOpenGLWidget):
    status = QtCore.Signal(str)

    def __init__(self, render_settings, brush_settings, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        self.settings = render_settings
        self.brush = brush_settings
        self.cameras = cammod.make_default_cameras()
        self.pane_names = [c.name for c in self.cameras]
        self.tool_mode = 'orbit'        # kept for API symmetry; viewport never paints
        self.maximized = None           # None or pane index 0..3

        # Animation clock for the pulsing glow ("glowanim"). The timer only runs while
        # animated, so a static scene costs nothing; settings.time (seconds) feeds the
        # same pulse the in-game shader uses.
        self._anim_timer = QtCore.QTimer(self)
        self._anim_timer.setInterval(16)   # ~60 fps
        self._anim_timer.timeout.connect(self._tick)
        self._anim_clock = QtCore.QElapsedTimer()

        self.ctx = None
        self.scene = None
        self.gizmo = None
        self._pending_model = None

        self._panes = []               # logical-space rects [(x,y,w,h)]
        self._split = [0.5, 0.5]       # vertical / horizontal divider fractions
        self._drag_divider = None      # 'v' | 'h' | 'both' while dragging a splitter
        self._active_pane = None
        self._last_mouse = None
        self._button = None
        self._last_uv = None
        self._last_gid = -1
        self.DIV = 5                   # divider grab tolerance (logical px)

    # ---- GL lifecycle --------------------------------------------------
    def initializeGL(self):
        self.ctx = moderngl.create_context()
        self.scene = scenemod.Scene(self.ctx)
        self.gizmo = scenemod.Gizmo(self.ctx)
        self.bloom = scenemod.BloomPipeline(self.ctx)
        if self._pending_model is not None:
            self._do_load(self._pending_model)
            self._pending_model = None

    def set_glow_anim(self, on):
        """Mirror settings.glow_anim, then (re)evaluate whether the clock should run."""
        self.settings.glow_anim = bool(on)
        self.refresh_animation()

    def refresh_animation(self):
        """Run the animation clock only while Animate is on - this is the single master
        switch for ALL time-based effects (pulse, flow, scroll, hue). Off = frozen."""
        if self.settings.glow_anim:
            if not self._anim_clock.isValid():
                self._anim_clock.start()
            self._anim_timer.start()
        else:
            self._anim_timer.stop()
            self.update()   # one repaint to settle

    def _tick(self):
        self.settings.time = self._anim_clock.elapsed() / 1000.0
        self.update()

    def load_model(self, loaded_model):
        if self.ctx is None:
            self._pending_model = loaded_model
            return
        self.makeCurrent()
        try:
            self._do_load(loaded_model)
        finally:
            self.doneCurrent()
        self.update()

    def _do_load(self, lm):
        self.scene.load(lm)
        for c in self.cameras:
            c.frame(lm.center, lm.radius)

    def resizeGL(self, w, h):
        self._recompute_panes()

    def _recompute_panes(self):
        w, h = self.width(), self.height()
        if self.maximized is not None:
            self._panes = [None, None, None, None]
            self._panes[self.maximized] = (0, 0, w, h)
            return
        hw = int(round(w * self._split[0]))
        hh = int(round(h * self._split[1]))
        self._panes = [
            (0, 0, hw, hh),            # TL
            (hw, 0, w - hw, hh),       # TR
            (0, hh, hw, h - hh),       # BL
            (hw, hh, w - hw, h - hh),  # BR
        ]

    def _divider_at(self, pos):
        """Return 'v', 'h', 'both', or None for the splitter under the cursor."""
        if self.maximized is not None:
            return None
        w, h = self.width(), self.height()
        vx, hy = w * self._split[0], h * self._split[1]
        on_v = abs(pos.x() - vx) <= self.DIV
        on_h = abs(pos.y() - hy) <= self.DIV
        if on_v and on_h:
            return 'both'
        return 'v' if on_v else ('h' if on_h else None)

    # ---- paint ---------------------------------------------------------
    def paintGL(self):
        if self.ctx is None:
            return
        self._recompute_panes()
        default_fbo = self.ctx.detect_framebuffer()
        if self.scene is not None:
            self.scene.flush_dirty()
        dpr = self.devicePixelRatioF()
        H = self.height()
        fbw = int(round(self.width() * dpr))
        fbh = int(round(self.height() * dpr))
        bg = (0.13, 0.14, 0.16, 1.0)

        # Render the scene into the offscreen MRT (color + emissive) so the emissive
        # can be bloomed; then composite (scene + bloom) onto the real framebuffer.
        self.bloom.ensure_size(fbw, fbh)
        self.bloom.color_only.clear(*bg, depth=1.0)          # color + depth
        self.bloom.emis_only.clear(0.0, 0.0, 0.0, 1.0)       # emissive -> black
        sfbo = self.bloom.scene_fbo
        sfbo.use()
        self.ctx.enable(moderngl.DEPTH_TEST)

        gizmos = []
        for i, rect in enumerate(self._panes):
            if rect is None:
                continue
            x, y, pw, ph = rect
            if pw <= 0 or ph <= 0:
                continue
            gx = int(round(x * dpr))
            gy = int(round((H - (y + ph)) * dpr))   # GL y is bottom-up
            gpw = int(round(pw * dpr))
            gph = int(round(ph * dpr))
            if self._active_pane == i:               # subtle active-pane tint
                self.bloom.color_only.scissor = (gx, gy, gpw, gph)
                self.bloom.color_only.clear(0.16, 0.17, 0.20, 1.0, depth=1.0)
                self.bloom.color_only.scissor = None
                sfbo.use()
            sfbo.scissor = (gx, gy, gpw, gph)
            sfbo.viewport = (gx, gy, gpw, gph)
            cam = self.cameras[i]
            aspect = gpw / gph if gph else 1.0
            if self.scene is not None and self.scene.model is not None:
                self.scene.draw(cam.view_matrix(), cam.proj_matrix(aspect),
                                cam.eye(), self.settings)
            if cam.kind == 'persp':
                gs = max(44, min(int(min(gpw, gph) * 0.18), 96))
                margin = int(6 * dpr)
                gizmos.append((cam, (gx + gpw - gs - margin, gy + gph - gs - margin, gs)))
        sfbo.scissor = None

        # composite scene + bloom to the screen
        strength = 1.4 if getattr(self.settings, 'glow', 0) > 0 else 0.0
        self.bloom.composite(default_fbo, (0, 0, fbw, fbh),
                             iterations=4, radius=2.5, strength=strength)

        # overlays drawn on the real framebuffer (NOT bloomed)
        default_fbo.use()
        self.ctx.enable(moderngl.DEPTH_TEST)
        for cam, corner in gizmos:
            if self.gizmo is not None:
                self.gizmo.draw(default_fbo, cam, corner, dpr)
        if self.maximized is None:
            self._draw_borders(default_fbo, dpr)
        default_fbo.scissor = None
        self.ctx.scissor = None

    def _draw_borders(self, fbo, dpr):
        w, h = self.width(), self.height()
        hw = int(round(w * self._split[0]))
        hh = int(round(h * self._split[1]))
        t = max(1, int(round(1.5 * dpr)))
        border = (0.32, 0.33, 0.36, 1.0)
        ghw = int(round(hw * dpr))
        fullw, fullh = int(round(w * dpr)), int(round(h * dpr))
        # vertical line at x=hw, horizontal line at y=hh (GL bottom-up)
        for rect in ((ghw - t // 2, 0, t, fullh),
                     (0, int(round((h - hh) * dpr)) - t // 2, fullw, t)):
            fbo.scissor = rect
            fbo.clear(*border, viewport=rect)
        fbo.scissor = None

    # ---- mouse routing -------------------------------------------------
    def _pane_at(self, pos):
        for i, rect in enumerate(self._panes):
            if rect is None:
                continue
            x, y, w, h = rect
            if x <= pos.x() < x + w and y <= pos.y() < y + h:
                return i
        return None

    def _ndc(self, pane_idx, pos):
        x, y, w, h = self._panes[pane_idx]
        lx = (pos.x() - x) / max(w, 1)
        ly = (pos.y() - y) / max(h, 1)
        return lx * 2 - 1, 1 - ly * 2, (w / h if h else 1.0)

    def mousePressEvent(self, e):
        self._button = e.button()
        self._last_mouse = e.position()
        self._last_uv = None
        self._last_gid = -1
        # grab a splitter if the cursor is on one (left button only)
        if self._button == QtCore.Qt.LeftButton:
            div = self._divider_at(e.position())
            if div:
                self._drag_divider = div
                self._active_pane = None
                return
        self._active_pane = self._pane_at(e.position())
        self.update()

    def mouseMoveEvent(self, e):
        # dragging a viewport splitter
        if self._drag_divider:
            w, h = max(self.width(), 1), max(self.height(), 1)
            if self._drag_divider in ('v', 'both'):
                self._split[0] = float(np.clip(e.position().x() / w, 0.1, 0.9))
            if self._drag_divider in ('h', 'both'):
                self._split[1] = float(np.clip(e.position().y() / h, 0.1, 0.9))
            self._recompute_panes()
            self.update()
            return
        if self._active_pane is None or self._last_mouse is None:
            self._update_hover_cursor(e.position())
            return
        # navigation only - the 3D viewports do not paint (use the UV/Texture panel)
        dx = e.position().x() - self._last_mouse.x()
        dy = e.position().y() - self._last_mouse.y()
        cam = self.cameras[self._active_pane]
        pw, ph = self._pane_size(self._active_pane)
        if self._button == QtCore.Qt.MiddleButton:
            cam.pan(dx, dy, pw, ph)
        elif self._button == QtCore.Qt.LeftButton:
            if cam.kind == 'persp':
                cam.orbit(dx * 0.4, -dy * 0.4)
            else:
                cam.pan(dx, dy, pw, ph)
        elif self._button == QtCore.Qt.RightButton and cam.kind == 'persp':
            cam.orbit(dx * 0.4, -dy * 0.4)
        self._last_mouse = e.position()
        self.update()

    def _update_hover_cursor(self, pos):
        div = self._divider_at(pos)
        if div == 'v':
            self.setCursor(QtCore.Qt.SplitHCursor)
        elif div == 'h':
            self.setCursor(QtCore.Qt.SplitVCursor)
        elif div == 'both':
            self.setCursor(QtCore.Qt.SizeAllCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, e):
        self._button = None
        self._last_mouse = None
        self._last_uv = None
        self._active_pane = None
        self._drag_divider = None
        self.update()

    def mouseDoubleClickEvent(self, e):
        p = self._pane_at(e.position())
        if p is None:
            return
        self.maximized = None if self.maximized is not None else p
        self._recompute_panes()
        self.update()

    def wheelEvent(self, e):
        pos = e.position()
        p = self._pane_at(pos)
        if p is None:
            return
        delta = e.angleDelta().y()
        factor = 0.85 if delta > 0 else 1.18
        self.cameras[p].zoom(factor)
        self.update()

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Tab:
            p = self._pane_at(self.mapFromGlobal(QtGui.QCursor.pos()))
            if p is not None:
                self.maximized = None if self.maximized is not None else p
                self._recompute_panes()
                self.update()
        else:
            super().keyPressEvent(e)

    def _pane_size(self, idx):
        r = self._panes[idx]
        return (r[2], r[3]) if r else (self.width(), self.height())

    def reset_view(self):
        if self.scene and self.scene.model:
            for c in self.cameras:
                c.frame(self.scene.model.center, self.scene.model.radius)
            self.update()

"""uv_panel.py - 2D texture/UV paint panel.

Shows the active map buffer flat (QPainter), with pan/zoom and a UV wireframe
overlay. Painting here writes into the same TextureSet numpy buffer the 3D viewport
uses, so edits sync live (it emits `painted`, the window re-uploads the sub-rect).
"""
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

import brush as brushmod
import textures


class UVPanel(QtWidgets.QWidget):
    painted = QtCore.Signal(object, str)   # (texset, role)

    def __init__(self, render_settings, brush_settings, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(220, 220)
        self.settings = render_settings
        self.brush = brush_settings
        self.tool_mode = 'orbit'

        self.texset = None
        self.role = 'diffuse'
        self.uv_coords = None      # (N,2) f4
        self.faces = None          # (M,3) i4 (active group)
        self._qimg = None

        self._zoom = 1.0
        self._origin = QtCore.QPointF(10, 10)
        self._last = None
        self._button = None
        self._last_uv = None
        self._fitted = True
        self._cursor = None

    # ---- target --------------------------------------------------------
    def set_target(self, texset, role, uv_coords=None, faces=None, reset_view=True):
        self.texset = texset
        self.role = role
        if uv_coords is not None:
            self.uv_coords = uv_coords
        if faces is not None:
            self.faces = faces
        self.refresh_image()
        if reset_view:
            self.fit()
        self.update()

    def refresh_image(self):
        if self.texset is None:
            self._qimg = None
            return
        buf = self.texset.buffers.get(self.role)
        if buf is None:
            self._qimg = None
            return
        h, w = buf.shape[:2]
        disp = np.ascontiguousarray(buf[:, :, :3])
        self._qimg = QtGui.QImage(disp.tobytes(), w, h, w * 3,
                                  QtGui.QImage.Format_RGB888).copy()

    def fit(self):
        if self._qimg is None:
            return
        w, h = self._qimg.width(), self._qimg.height()
        avail_w = max(self.width() - 20, 1)
        avail_h = max(self.height() - 20, 1)
        self._zoom = min(avail_w / w, avail_h / h)
        ox = (self.width() - w * self._zoom) * 0.5
        oy = (self.height() - h * self._zoom) * 0.5
        self._origin = QtCore.QPointF(ox, oy)
        self._fitted = True

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Keep the image fitted to the panel until the user manually pans/zooms.
        if self._qimg is not None and getattr(self, '_fitted', True):
            self.fit()
            self.update()

    # ---- transforms ----------------------------------------------------
    def _screen_to_texel(self, pos):
        tx = (pos.x() - self._origin.x()) / self._zoom
        ty = (pos.y() - self._origin.y()) / self._zoom
        return tx, ty

    def _texel_to_uv(self, tx, ty):
        if self._qimg is None:
            return None
        w, h = self._qimg.width(), self._qimg.height()
        u = tx / w
        # invert the flip_v applied in uv_to_texel so paint_dab reproduces (tx,ty)
        v = (1.0 - ty / h) if self.settings.flip_v else (ty / h)
        return (u, v)

    # ---- paint ---------------------------------------------------------
    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(30, 31, 34))
        if self._qimg is None:
            p.setPen(QtGui.QColor(150, 150, 150))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, 'No texture loaded')
            return
        w, h = self._qimg.width(), self._qimg.height()
        target = QtCore.QRectF(self._origin.x(), self._origin.y(), w * self._zoom, h * self._zoom)
        p.drawImage(target, self._qimg)
        p.setPen(QtGui.QPen(QtGui.QColor(80, 80, 90), 1))
        p.drawRect(target)
        self._draw_uv_wire(p)
        # brush footprint ring (exact: radius is in texels, zoom maps to screen)
        if self.tool_mode == 'paint' and self._cursor is not None:
            rr = self.brush.radius * self._zoom
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 200), 1))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(self._cursor, rr, rr)

    def leaveEvent(self, e):
        self._cursor = None
        self.update()

    def _draw_uv_wire(self, p):
        if self.uv_coords is None or self.faces is None or self._qimg is None:
            return
        w, h = self._qimg.width(), self._qimg.height()
        flip = self.settings.flip_v
        uv = self.uv_coords
        ox, oy, z = self._origin.x(), self._origin.y(), self._zoom

        def sx(u):
            return ox + (u * w) * z

        def sy(v):
            tv = (1.0 - v) if flip else v
            return oy + (tv * h) * z

        p.setPen(QtGui.QPen(QtGui.QColor(70, 200, 120, 140), 1))
        faces = self.faces
        # cap the number of drawn edges to keep the overlay responsive
        step = max(1, faces.shape[0] // 20000)
        for i in range(0, faces.shape[0], step):
            a, b, c = faces[i]
            pa = QtCore.QPointF(sx(uv[a, 0]), sy(uv[a, 1]))
            pb = QtCore.QPointF(sx(uv[b, 0]), sy(uv[b, 1]))
            pc = QtCore.QPointF(sx(uv[c, 0]), sy(uv[c, 1]))
            p.drawLine(pa, pb)
            p.drawLine(pb, pc)
            p.drawLine(pc, pa)

    # ---- interaction ---------------------------------------------------
    def mousePressEvent(self, e):
        self._button = e.button()
        self._last = e.position()
        self._last_uv = None
        if self._button == QtCore.Qt.LeftButton and self.tool_mode == 'paint':
            self._paint(e.position(), continuous=False)

    def mouseMoveEvent(self, e):
        self._cursor = e.position()
        if self._button is None:
            if self.tool_mode == 'paint':
                self.update()       # refresh the brush ring
            return
        dx = e.position().x() - self._last.x()
        dy = e.position().y() - self._last.y()
        if self._button == QtCore.Qt.MiddleButton:
            self._origin += QtCore.QPointF(dx, dy)
            self._fitted = False
            self.update()
        elif self._button == QtCore.Qt.LeftButton and self.tool_mode == 'paint':
            self._paint(e.position(), continuous=True)
        self._last = e.position()

    def mouseReleaseEvent(self, e):
        self._button = None
        self._last_uv = None

    def wheelEvent(self, e):
        pos = e.position()
        tx, ty = self._screen_to_texel(pos)
        factor = 1.18 if e.angleDelta().y() > 0 else 0.85
        self._zoom = max(0.02, self._zoom * factor)
        # keep the texel under the cursor fixed
        self._origin = QtCore.QPointF(pos.x() - tx * self._zoom, pos.y() - ty * self._zoom)
        self._fitted = False
        self.update()

    def _paint(self, pos, continuous):
        if self.texset is None:
            return
        tx, ty = self._screen_to_texel(pos)
        uv = self._texel_to_uv(tx, ty)
        if uv is None:
            return
        role = None
        if continuous and self._last_uv is not None:
            role = self._stroke(self._last_uv, uv)
        else:
            res = brushmod.paint_dab(self.texset, uv, self.brush,
                                     self.settings.use_alpha_spec, self.settings.flip_v)
            if res:
                role, rect = res
                textures.mark_dirty(self.texset, role, *rect)
        self._last_uv = uv
        if role:
            self.refresh_image()
            self.painted.emit(self.texset, role)
            self.update()

    def _stroke(self, uv0, uv1):
        buf = self.texset.buffers.get(
            brushmod.role_for_mode(self.brush.mode, self.settings.use_alpha_spec))
        tw = buf.shape[1] if buf is not None else 512
        d_px = np.hypot((uv1[0] - uv0[0]) * tw, (uv1[1] - uv0[1]) * tw)
        step_px = max(self.brush.radius * self.brush.spacing, 1.0)
        n = max(1, int(d_px / step_px))
        last_role = None
        for k in range(1, n + 1):
            t = k / n
            uv = (uv0[0] + (uv1[0] - uv0[0]) * t, uv0[1] + (uv1[1] - uv0[1]) * t)
            res = brushmod.paint_dab(self.texset, uv, self.brush,
                                     self.settings.use_alpha_spec, self.settings.flip_v)
            if res:
                last_role, rect = res
                textures.mark_dirty(self.texset, last_role, *rect)
        return last_role

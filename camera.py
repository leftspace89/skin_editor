"""camera.py - one Camera class covering fixed orthographic panes (Top/Front/Right)
and an orbiting perspective pane. All matrices are column-major (mathutil)."""
import numpy as np

import mathutil as mu

# Fixed orthographic pane setups: (view direction, up). The mdl2obj (-x, z, -y)
# transform puts the model in a Z-UP right-handed space (vertical = +Z), so cameras
# are Z-up. A gun lies along the +Y axis (barrel length), height is +Z, width +X.
WORLD_UP = mu.vec3(0, 0, 1)
ORTHO_SETUPS = {
    'front': (mu.vec3(0, -1, 0), mu.vec3(0, 0, 1)),   # look along -Y (end-on)
    'right': (mu.vec3(-1, 0, 0), mu.vec3(0, 0, 1)),   # look along -X (side profile)
    'top':   (mu.vec3(0, 0, -1), mu.vec3(0, 1, 0)),   # look down -Z
}


class Camera:
    def __init__(self, kind, name=''):
        self.kind = kind          # 'persp' | 'ortho'
        self.name = name
        self.target = mu.vec3(0, 0, 0)
        self.distance = 5.0       # eye distance from target (placement)
        # perspective
        self.azimuth = 35.0
        self.elevation = 20.0
        self.fov = 45.0
        # ortho
        self.half_extent = 1.0    # half of the vertical world span shown
        self.view_dir = mu.vec3(0, 0, -1)
        self.up = mu.vec3(0, 1, 0)
        self.near = 0.01
        self.far = 1000.0

    # ---- framing -------------------------------------------------------
    def frame(self, center, radius):
        self.target = mu.vec3(center)
        r = max(float(radius), 1e-3)
        self.near = max(r * 0.01, 1e-3)
        self.far = r * 50.0
        if self.kind == 'persp':
            self.distance = r / np.tan(np.radians(self.fov) * 0.5) * 1.3
        else:
            self.half_extent = r * 1.15
            self.distance = r * 10.0

    # ---- eye / basis ---------------------------------------------------
    def eye(self):
        if self.kind == 'persp':
            az, el = np.radians(self.azimuth), np.radians(self.elevation)
            # Z-up orbit: vertical component on +Z, azimuth sweeps the XY plane.
            d = mu.vec3(np.cos(el) * np.sin(az), np.cos(el) * np.cos(az), np.sin(el))
            return self.target + d * self.distance
        return self.target - mu.normalize(self.view_dir) * self.distance

    def _up(self):
        return self.up if self.kind == 'ortho' else WORLD_UP

    def view_matrix(self):
        return mu.look_at(self.eye(), self.target, self._up())

    def proj_matrix(self, aspect):
        if self.kind == 'persp':
            return mu.perspective(self.fov, aspect, self.near, self.far)
        ex = self.half_extent * max(aspect, 1e-3)
        ey = self.half_extent
        return mu.ortho(-ex, ex, -ey, ey, -self.far, self.far)

    def right_up(self):
        """World-space right/up axes of the current view (for screen-plane panning)."""
        fwd = mu.normalize(self.target - self.eye()) if self.kind == 'persp' \
            else mu.normalize(self.view_dir)
        up0 = self._up()
        right = mu.normalize(np.cross(fwd, up0))
        up = mu.normalize(np.cross(right, fwd))
        return right, up

    # ---- interaction ---------------------------------------------------
    def orbit(self, dazi_deg, delev_deg):
        if self.kind != 'persp':
            return
        self.azimuth = (self.azimuth + dazi_deg) % 360.0
        self.elevation = float(np.clip(self.elevation + delev_deg, -89.0, 89.0))

    def pan(self, dx_px, dy_px, pane_w, pane_h):
        """Pan the target in the view plane by a pixel delta."""
        if pane_h <= 0:
            return
        if self.kind == 'persp':
            world_per_px = (2.0 * self.distance * np.tan(np.radians(self.fov) * 0.5)) / pane_h
        else:
            world_per_px = (2.0 * self.half_extent) / pane_h
        right, up = self.right_up()
        self.target = self.target - right * (dx_px * world_per_px) + up * (dy_px * world_per_px)

    def zoom(self, factor):
        if self.kind == 'persp':
            self.distance = float(np.clip(self.distance * factor, self.near * 2, self.far))
        else:
            self.half_extent = max(self.half_extent * factor, 1e-3)


def make_default_cameras():
    """Quad layout cameras keyed by pane index 0..3 (TL,TR,BL,BR)."""
    cams = []
    top = Camera('ortho', 'Top'); top.view_dir, top.up = ORTHO_SETUPS['top']
    front = Camera('ortho', 'Front'); front.view_dir, front.up = ORTHO_SETUPS['front']
    right = Camera('ortho', 'Right'); right.view_dir, right.up = ORTHO_SETUPS['right']
    persp = Camera('persp', 'Perspective')
    cams = [top, front, right, persp]
    return cams

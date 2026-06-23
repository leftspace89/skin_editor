"""brush.py - picking + brush-dab compositing.

PickMesh wraps a LoadedModel for fast, fully-vectorized ray/triangle picking (one
ray vs all triangles at once - weapon view-models are small, no BVH needed). A hit
yields the interpolated uv0 and the owning draw-group index. paint_dab() writes a
circular falloff dab into the right role buffer for the active brush mode.
"""
import numpy as np

import mathutil as mu

MODES = ('diffuse', 'brightness', 'spec', 'normal', 'roughness', 'emissive', 'erase glow')


class BrushSettings:
    def __init__(self):
        self.mode = 'diffuse'
        self.radius = 24.0          # texture pixels
        self.hardness = 0.5         # 0 soft .. 1 hard edge
        self.strength = 0.7         # 0..1 per-dab opacity
        self.color = (220, 40, 40)  # diffuse paint color (RGB 0..255)
        self.glow_color = (40, 230, 255)  # emissive/glow paint color (RGB 0..255)
        self.factor = 1.15          # brightness/spec multiply target (>1 up, <1 down)
        self.rough_target = 200     # roughness paint value 0..255
        self.normal_flatten = True  # normal mode pushes toward flat (128,128,255)
        self.spacing = 0.4          # dab spacing as fraction of radius


def role_for_mode(mode, use_alpha_spec):
    """Which TextureSet role a brush mode writes into."""
    if mode in ('diffuse', 'brightness'):
        return 'diffuse'
    if mode == 'spec':
        return 'diffuse' if use_alpha_spec else 'spec'
    if mode == 'normal':
        return 'normal'
    if mode == 'roughness':
        return 'roughness'
    if mode in ('emissive', 'erase glow'):
        return 'emissive'
    return 'diffuse'


class PickMesh:
    def __init__(self, loaded_model):
        lm = loaded_model
        self.P = np.ascontiguousarray(lm.positions.astype('f8'))
        self.uv = np.ascontiguousarray(lm.vertices[:, 6:8].astype('f8'))
        faces, gids = [], []
        for gi, g in enumerate(lm.draw_groups):
            f = g['faces']
            if f.shape[0]:
                faces.append(f)
                gids.append(np.full(f.shape[0], gi, dtype='i4'))
        if faces:
            self.faces = np.concatenate(faces, axis=0).astype('i8')
            self.gid = np.concatenate(gids, axis=0)
        else:
            self.faces = np.zeros((0, 3), 'i8')
            self.gid = np.zeros((0,), 'i4')
        if self.faces.shape[0]:
            self.v0 = self.P[self.faces[:, 0]]
            self.e1 = self.P[self.faces[:, 1]] - self.v0
            self.e2 = self.P[self.faces[:, 2]] - self.v0
            self.fn = np.cross(self.e1, self.e2)
        else:
            self.v0 = self.e1 = self.e2 = self.fn = np.zeros((0, 3))

    def raycast(self, origin, direction, cull_back=True, tmin=1e-5):
        """Return (uv (2,), group_index) of the nearest hit, or (None, -1)."""
        if self.faces.shape[0] == 0:
            return None, -1
        o = np.asarray(origin, dtype='f8')
        dd = mu.normalize(np.asarray(direction, dtype='f8')).astype('f8')

        pvec = np.cross(dd, self.e2)
        det = np.einsum('ij,ij->i', self.e1, pvec)
        eps = 1e-9
        valid = np.abs(det) > eps
        if cull_back:
            valid &= (np.einsum('ij,j->i', self.fn, dd) < 0.0)
        inv = np.zeros_like(det)
        inv[valid] = 1.0 / det[valid]
        tvec = o - self.v0
        u = np.einsum('ij,ij->i', tvec, pvec) * inv
        qvec = np.cross(tvec, self.e1)
        v = np.einsum('ij,j->i', qvec, dd) * inv
        t = np.einsum('ij,ij->i', self.e2, qvec) * inv
        hit = valid & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1.0 + 1e-6) & (t > tmin)
        if not np.any(hit):
            return None, -1
        idx = np.where(hit)[0]
        best = idx[np.argmin(t[idx])]
        uu, vv = u[best], v[best]
        w0 = 1.0 - uu - vv
        f = self.faces[best]
        uv = w0 * self.uv[f[0]] + uu * self.uv[f[1]] + vv * self.uv[f[2]]
        return uv.astype('f4'), int(self.gid[best])


def ray_from_ndc(cam, ndc_x, ndc_y, aspect):
    """Build a world-space ray for a pane camera at clip coords ndc in [-1,1]."""
    view = cam.view_matrix()
    proj = cam.proj_matrix(aspect)
    inv = np.linalg.inv(proj @ view)
    near = mu.unproject((ndc_x, ndc_y, -1.0), inv)
    far = mu.unproject((ndc_x, ndc_y, 1.0), inv)
    if cam.kind == 'persp':
        origin = cam.eye()
        direction = mu.normalize(far - near)
    else:
        origin = near
        direction = mu.normalize(far - near)
    return origin, direction


def uv_to_texel(uv, w, h, flip_v):
    cx = uv[0] * w
    cy = (1.0 - uv[1]) * h if flip_v else uv[1] * h
    return cx, cy


def _falloff(local_x, local_y, cx, cy, radius, hardness, strength):
    dist = np.sqrt((local_x - cx) ** 2 + (local_y - cy) ** 2)
    inner = radius * np.clip(hardness, 0.0, 0.999)
    m = np.clip((radius - dist) / max(radius - inner, 1e-3), 0.0, 1.0)
    m = m * m * (3 - 2 * m)            # smoothstep
    return (m * strength).astype('f4')


def paint_dab(texset, uv, brush, use_alpha_spec, flip_v):
    """Composite one dab. Returns (role, (x0,y0,x1,y1)) of the modified rect, or None."""
    role = role_for_mode(brush.mode, use_alpha_spec)
    buf = texset.buffers.get(role)
    if buf is None:
        return None
    h, w = buf.shape[:2]
    cx, cy = uv_to_texel(uv, w, h, flip_v)
    r = max(float(brush.radius), 1.0)
    x0 = int(np.floor(cx - r)); x1 = int(np.ceil(cx + r)) + 1
    y0 = int(np.floor(cy - r)); y1 = int(np.ceil(cy + r)) + 1
    x0 = max(0, x0); y0 = max(0, y0); x1 = min(w, x1); y1 = min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return None

    ys, xs = np.mgrid[y0:y1, x0:x1]
    m = _falloff(xs, ys, cx, cy, r, brush.hardness, brush.strength)  # (h,w) 0..1
    if not np.any(m > 0):
        return None
    m3 = m[:, :, None]

    region = buf[y0:y1, x0:x1].astype('f4')
    mode = brush.mode
    if mode == 'diffuse':
        col = np.array(brush.color, dtype='f4')
        region[:, :, :3] = region[:, :, :3] * (1 - m3) + col[None, None, :] * m3
    elif mode == 'brightness':
        f = 1.0 + (brush.factor - 1.0) * m3
        region[:, :, :3] = np.clip(region[:, :, :3] * f, 0, 255)
    elif mode == 'spec':
        f = 1.0 + (brush.factor - 1.0) * m
        if use_alpha_spec:
            region[:, :, 3] = np.clip(region[:, :, 3] * f, 0, 255)
        else:
            region[:, :, :3] = np.clip(region[:, :, :3] * f[:, :, None], 0, 255)
    elif mode == 'roughness':
        tgt = float(brush.rough_target)
        region[:, :, :3] = region[:, :, :3] * (1 - m3) + tgt * m3
    elif mode == 'normal':
        tgt = np.array([128, 128, 255], dtype='f4') if brush.normal_flatten \
            else np.array([128, 128, 255], dtype='f4')
        region[:, :, :3] = region[:, :, :3] * (1 - m3) + tgt[None, None, :] * m3
    elif mode == 'emissive':
        # paint the glow color into the emissive map (the glow mask)
        col = np.array(brush.glow_color, dtype='f4')
        region[:, :, :3] = region[:, :, :3] * (1 - m3) + col[None, None, :] * m3
        region[:, :, 3] = 255
    elif mode == 'erase glow':
        # paint emissive back to black (no glow)
        region[:, :, :3] = region[:, :, :3] * (1 - m3)
        region[:, :, 3] = 255

    buf[y0:y1, x0:x1] = np.clip(region, 0, 255).astype('uint8')
    return role, (x0, y0, x1, y1)

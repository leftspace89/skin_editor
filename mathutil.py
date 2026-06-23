"""mathutil.py - minimal mat4/vec3 helpers.

Matrices are stored as STANDARD math matrices (numpy float32 (4,4)) such that
`M @ v` transforms a column vector and `mul(P, V, M) == P @ V @ M`. This keeps all
multiplication and np.linalg.inv math ordinary. OpenGL wants column-major memory,
so transpose only at the GL boundary via gl(M) before handing bytes to a uniform.
"""
import numpy as np


def vec3(x, y=None, z=None):
    if y is None:
        return np.asarray(x, dtype='f4')
    return np.array([x, y, z], dtype='f4')


def normalize(v):
    v = np.asarray(v, dtype='f4')
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def identity():
    return np.identity(4, dtype='f4')


def _m(rows):
    return np.array(rows, dtype='f4')


def perspective(fovy_deg, aspect, near, far):
    f = 1.0 / np.tan(np.radians(fovy_deg) * 0.5)
    a = aspect if aspect > 1e-6 else 1.0
    return _m([
        [f / a, 0, 0, 0],
        [0, f, 0, 0],
        [0, 0, (far + near) / (near - far), (2 * far * near) / (near - far)],
        [0, 0, -1, 0],
    ])


def ortho(left, right, bottom, top, near, far):
    rl = (right - left) or 1e-6
    tb = (top - bottom) or 1e-6
    fn = (far - near) or 1e-6
    return _m([
        [2 / rl, 0, 0, -(right + left) / rl],
        [0, 2 / tb, 0, -(top + bottom) / tb],
        [0, 0, -2 / fn, -(far + near) / fn],
        [0, 0, 0, 1],
    ])


def look_at(eye, target, up):
    eye = np.asarray(eye, dtype='f4')
    f = normalize(np.asarray(target, dtype='f4') - eye)
    s = normalize(np.cross(f, normalize(up)))
    u = np.cross(s, f)
    return _m([
        [s[0], s[1], s[2], -float(np.dot(s, eye))],
        [u[0], u[1], u[2], -float(np.dot(u, eye))],
        [-f[0], -f[1], -f[2], float(np.dot(f, eye))],
        [0, 0, 0, 1],
    ])


def mul(*mats):
    """Standard math product: mul(P, V, M) == P @ V @ M."""
    out = mats[0]
    for m in mats[1:]:
        out = out @ m
    return out.astype('f4')


def normal_matrix(model):
    """Inverse-transpose of the model's upper-left 3x3 (math form), for normals."""
    m3 = model[:3, :3]
    try:
        return np.linalg.inv(m3).T.astype('f4')
    except np.linalg.LinAlgError:
        return m3.astype('f4')


def gl(m):
    """Column-major, C-contiguous float32 copy ready for a moderngl uniform.write()
    (GLSL reads bytes column-major, so we transpose the math matrix here)."""
    return np.ascontiguousarray(np.asarray(m, dtype='f4').T)


def unproject(ndc, inv_view_proj):
    """ndc = (x,y,z) in clip space (-1..1). inv_view_proj is the standard math
    inverse of (proj @ view). Returns world-space point (3,)."""
    p = np.array([ndc[0], ndc[1], ndc[2], 1.0], dtype='f4')
    w = inv_view_proj @ p
    if abs(w[3]) > 1e-9:
        w = w / w[3]
    return w[:3].astype('f4')

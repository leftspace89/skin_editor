"""textures.py - DDS <-> numpy IO, moderngl upload + dirty sub-rect updates, and
DDS export through the shared png2dds.save_dds_rgba writer.

Pixel buffers are numpy (H,W,4) uint8 in PIL row order (row 0 = top of the image).
Sampling orientation is handled with a single flip_v flag in the shader + brush so
the buffer convention here stays simple.
"""
import os
import sys

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
# png2dds is bundled in ./vendor (self-contained); fall back to the dev sibling.
for _p in (os.path.normpath(os.path.join(_HERE, '..', 'model00p')),
           os.path.join(_HERE, 'vendor')):        # vendor inserted last -> wins
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import png2dds   # noqa: E402  (shared DDS writer)

DEFAULT_SIZE = 512

# Per-role neutral fill used when a map is missing on disk.
_ROLE_FILL = {
    'diffuse':   (160, 160, 165, 255),
    'normal':    (128, 128, 255, 255),   # flat tangent-space normal
    'roughness': (128, 128, 128, 255),
    'spec':      (60, 60, 60, 255),
    'emissive':  (0, 0, 0, 255),
}


def load_dds(path):
    """Load any image Pillow can open (.dds DXT1/3/5, .png, .tga) -> (H,W,4) uint8."""
    img = Image.open(path).convert('RGBA')
    return np.asarray(img, dtype='uint8').copy()


def downscale_rgba(rgba, max_size):
    """Return an RGBA copy whose longest side is <= max_size (for fast previews)."""
    h, w = rgba.shape[:2]
    if max(h, w) <= max_size:
        return rgba
    im = Image.fromarray(rgba, 'RGBA')
    s = max_size / float(max(h, w))
    im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    return np.asarray(im, dtype='uint8').copy()


def normal_from_diffuse(diffuse, strength=2.0, blur=0, invert_y=False, height_source='luma'):
    """Generate a tangent-space normal map (RGBA uint8, flat-blue convention) from a
    diffuse image by treating its brightness as a height field and taking the Sobel
    gradient. Tiling-safe (wrap edges). `strength` scales the bump; `blur` is a count
    of smoothing passes; `invert_y` flips the green channel (DirectX vs OpenGL); the
    Z (blue) channel is solved so the vector stays unit-length."""
    rgb = diffuse[:, :, :3].astype('f4') / 255.0
    if height_source == 'max':
        h = rgb.max(axis=2)
    else:
        h = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    for _ in range(int(blur)):
        h = (h + np.roll(h, 1, 0) + np.roll(h, -1, 0)
             + np.roll(h, 1, 1) + np.roll(h, -1, 1)) / 5.0
    # Sobel gradients with wrap-around (seamless on tiling textures)
    hl, hr = np.roll(h, 1, 1), np.roll(h, -1, 1)
    hu, hd = np.roll(h, 1, 0), np.roll(h, -1, 0)
    hul, hur = np.roll(hu, 1, 1), np.roll(hu, -1, 1)
    hdl, hdr = np.roll(hd, 1, 1), np.roll(hd, -1, 1)
    gx = ((hur + 2 * hr + hdr) - (hul + 2 * hl + hdl)) / 8.0
    gy = ((hdl + 2 * hd + hdr) - (hul + 2 * hu + hur)) / 8.0
    nx = -gx * float(strength)
    ny = (gy if invert_y else -gy) * float(strength)
    nz = np.ones_like(nx)
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + nz * nz)
    out = np.empty((h.shape[0], h.shape[1], 4), 'uint8')
    out[..., 0] = np.clip((nx * inv * 0.5 + 0.5) * 255.0, 0, 255)
    out[..., 1] = np.clip((ny * inv * 0.5 + 0.5) * 255.0, 0, 255)
    out[..., 2] = np.clip((nz * inv * 0.5 + 0.5) * 255.0, 0, 255)
    out[..., 3] = 255
    return out


def emissive_from_diffuse(diffuse, threshold=0.70, softness=0.15, intensity=1.0,
                          spread=0, source='bright', use_tint=False,
                          tint_color=(40, 230, 255)):
    """Generate an emissive/glow map (RGBA uint8) from a diffuse image. Pixels whose
    selected feature exceeds `threshold` (softened by `softness`) glow; the rest is
    black (no glow). `source` picks the feature: 'bright' (luma), 'dark' (1-luma, for
    glowing cracks), 'saturation' (colorful areas), or 'color' (closeness to
    tint_color). `use_tint` recolors the glow to tint_color, else it keeps the
    diffuse color. `intensity` scales it; `spread` feathers/blooms it (blur passes)."""
    rgb = diffuse[:, :, :3].astype('f4') / 255.0
    luma = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    if source == 'dark':
        mask = 1.0 - luma
    elif source == 'saturation':
        mask = rgb.max(axis=2) - rgb.min(axis=2)
    elif source == 'color':
        tc = np.array(tint_color, 'f4') / 255.0
        mask = 1.0 - np.sqrt(((rgb - tc[None, None, :]) ** 2).sum(axis=2)) / np.sqrt(3.0)
    else:
        mask = luma
    lo, hi = threshold - softness, threshold + softness
    t = np.clip((mask - lo) / max(hi - lo, 1e-4), 0.0, 1.0)
    m = t * t * (3.0 - 2.0 * t)                       # smoothstep
    if use_tint:
        col = np.array(tint_color, 'f4') / 255.0
        glow = col[None, None, :] * m[..., None]
    else:
        glow = rgb * m[..., None]
    glow = glow * float(intensity)
    for _ in range(int(spread)):
        glow = (glow + np.roll(glow, 1, 0) + np.roll(glow, -1, 0)
                + np.roll(glow, 1, 1) + np.roll(glow, -1, 1)) / 5.0
    out = np.empty((rgb.shape[0], rgb.shape[1], 4), 'uint8')
    out[..., :3] = np.clip(glow * 255.0, 0, 255)
    out[..., 3] = 255
    return out


def spec_from_emissive(emissive, intensity=1.0):
    """Specular map derived from an emissive/glow map: glowing areas become shiny so
    they ALSO catch a light-dependent specular highlight in-engine (emissive blooms
    unlit; this makes the same areas flare when a light hits them). The glow COLOR is
    kept (tSpecularMap = 'color of light bounced off the surface'), so a cyan glow
    gives cyan highlights - this is also how first-person weapons glow under light
    (PVdiffuse_weapon_SpecColor.fx has tSpecularMap but no emissive). Returns (H,W,4)."""
    rgb = np.clip(emissive[:, :, :3].astype('f4') * float(intensity), 0, 255).astype('uint8')
    out = np.empty((rgb.shape[0], rgb.shape[1], 4), 'uint8')
    out[..., :3] = rgb
    out[..., 3] = 255
    return out


def snap_to_dxt(rgba):
    """DXT requires dimensions that are multiples of 4. Resize the buffer to the
    NEAREST multiple of 4 (LANCZOS) so odd-sized textures export instead of being
    skipped. Returns the buffer unchanged if it's already aligned."""
    h, w = rgba.shape[:2]
    nw = max(4, int(round(w / 4.0)) * 4)
    nh = max(4, int(round(h / 4.0)) * 4)
    if (nw, nh) == (w, h):
        return rgba
    im = Image.fromarray(np.ascontiguousarray(rgba), 'RGBA').resize((nw, nh), Image.LANCZOS)
    return np.asarray(im, dtype='uint8').copy()


def save_dds(rgba, out_path, brightness=1.0, spec=1.0):
    """Write an arbitrary RGBA buffer (not tied to a TextureSet) to a DXT5 .dds,
    creating parent folders. Auto-snaps non-multiple-of-4 dimensions."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    return png2dds.save_dds_rgba(snap_to_dxt(rgba), out_path, brightness=brightness, spec=spec)


def _fill(role, size=DEFAULT_SIZE):
    c = _ROLE_FILL.get(role, (128, 128, 128, 255))
    a = np.empty((size, size, 4), dtype='uint8')
    a[:, :] = c
    return a


def load_textureset(ts):
    """Populate ts.buffers / ts.original for every editable role. Missing maps get
    a neutral fill sized to the diffuse (so brush + UV align across maps)."""
    # diffuse first so others can match its size
    order = ['diffuse', 'normal', 'roughness', 'spec', 'emissive']
    base_size = DEFAULT_SIZE
    for role in order:
        path = ts.roles.get(role)
        if path and os.path.isfile(path):
            try:
                buf = load_dds(path)
            except Exception as e:
                print('  ! failed to load %s (%s): %s' % (role, path, e))
                buf = _fill(role, base_size)
        else:
            buf = _fill(role, base_size)
        if role == 'diffuse':
            base_size = buf.shape[0]
        ts.buffers[role] = buf
        ts.original[role] = buf.copy()
    # any maps that came back at the fill size while diffuse was larger: leave them;
    # the shader samples by UV so differing sizes are fine.


def make_gl_texture(ctx, arr):
    h, w = arr.shape[:2]
    tex = ctx.texture((w, h), 4, np.ascontiguousarray(arr).tobytes())
    tex.build_mipmaps()
    tex.repeat_x = True
    tex.repeat_y = True
    return tex


def upload_textureset(ctx, ts):
    """Create moderngl textures for all roles of a TextureSet (call with the GL
    context current)."""
    for role, buf in ts.buffers.items():
        if role in ts.gltex:
            ts.gltex[role].release()
        ts.gltex[role] = make_gl_texture(ctx, buf)
    ts.dirty.clear()


def mark_dirty(ts, role, x0, y0, x1, y1):
    """Grow the pending dirty rect for a role (inclusive-exclusive, pixel coords)."""
    cur = ts.dirty.get(role)
    if cur is None:
        ts.dirty[role] = [x0, y0, x1, y1]
    else:
        cur[0] = min(cur[0], x0)
        cur[1] = min(cur[1], y0)
        cur[2] = max(cur[2], x1)
        cur[3] = max(cur[3], y1)


def flush_dirty(ts):
    """Re-upload only the changed sub-rect of each dirty role to its GL texture,
    then regenerate that texture's mipmaps so the edit shows at every zoom level
    (without this, the distant/perspective view samples a stale lower mip and the
    paint appears to 'not land')."""
    touched = []
    for role, rect in list(ts.dirty.items()):
        tex = ts.gltex.get(role)
        buf = ts.buffers.get(role)
        if tex is None or buf is None:
            continue
        x0, y0, x1, y1 = rect
        h, w = buf.shape[:2]
        x0 = max(0, min(x0, w)); x1 = max(0, min(x1, w))
        y0 = max(0, min(y0, h)); y1 = max(0, min(y1, h))
        if x1 <= x0 or y1 <= y0:
            continue
        sub = np.ascontiguousarray(buf[y0:y1, x0:x1])
        tex.write(sub.tobytes(), viewport=(x0, y0, x1 - x0, y1 - y0))
        touched.append(tex)
    for tex in touched:
        try:
            tex.build_mipmaps()
        except Exception:
            pass
    ts.dirty.clear()


def export_role(ts, role, out_path, brightness=1.0, spec=1.0, ref_alpha=False):
    """Export one role's buffer to DDS. For diffuse, the alpha channel is the spec
    mask; ref_alpha copies the pristine original alpha instead of the edited one."""
    buf = ts.buffers.get(role)
    if buf is None:
        raise ValueError('no buffer for role %r' % role)
    out = buf.copy()
    if ref_alpha and role in ts.original:
        orig = ts.original[role]
        if orig.shape == out.shape:
            out[:, :, 3] = orig[:, :, 3]
    out = snap_to_dxt(out)                  # DXT needs multiple-of-4 dims; resize if not
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    return png2dds.save_dds_rgba(out, out_path, brightness=brightness, spec=spec)


_ROLE_SUFFIX = {'diffuse': '_D', 'normal': '_N', 'roughness': '_R',
                'spec': '_C', 'emissive': '_EM'}

# Folder names that mark the start of a game-relative path. Used to recover the
# in-game subpath even when a file is loaded from outside the configured game root.
_GAME_ANCHORS = ('weapons', 'costumes_f', 'costumes_m', 'characters_f',
                 'characters_m', 'materials', 'models', 'characters', 'props')


def extract_game_subpath(path):
    """Return the path starting at the first recognised game anchor folder
    (e.g. '.../X/weapons/3_melee/textures' -> 'weapons/3_melee/textures'), or None."""
    parts = os.path.abspath(path).replace('\\', '/').split('/')
    low = [p.lower() for p in parts]
    for a in _GAME_ANCHORS:
        if a in low:
            return '/'.join(parts[low.index(a):])
    return None


def relative_export_path(ts, role, game_root):
    """Game-relative path for a role, e.g. 'weapons/3_melee/textures/Melee_HandAxe_D.dds'.
    An output base + this path recreates the in-game folder tree.

    Directory: the role's own source dir (if under the game root), else the diffuse's
    dir, else the model-derived textures dir (ts.default_reldir), else 1_main.
    Filename: the role's own source name, else the diffuse name with its suffix
    swapped, else '<material><suffix>.dds'."""
    gr = os.path.abspath(game_root)
    suffix = _ROLE_SUFFIX.get(role, '_D')

    def reldir(p):
        d = os.path.dirname(os.path.abspath(p))
        try:
            r = os.path.relpath(d, gr)
            if not r.startswith('..'):
                return r.replace('\\', '/')
        except ValueError:
            pass
        return extract_game_subpath(d)   # anchor-based recovery (may be None)

    # filename
    own = ts.roles.get(role)
    diff = ts.roles.get('diffuse')
    if own:
        fname = os.path.splitext(os.path.basename(own))[0] + '.dds'  # force .dds
    elif diff:
        stem = os.path.basename(diff)
        for s in ('_D.dds', '_d.dds'):
            if stem.lower().endswith(s.lower()):
                stem = stem[:-len(s)]
                break
        else:
            stem = os.path.splitext(stem)[0]
        fname = '%s%s.dds' % (stem, suffix)
    else:
        fname = '%s%s.dds' % (ts.name, suffix)

    # directory
    d = reldir(own) if own else None
    if d is None and diff:
        d = reldir(diff)
    if d is None:
        d = (ts.default_reldir or 'weapons/1_main/textures')
    return '%s/%s' % (d.rstrip('/'), fname)

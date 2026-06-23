"""png2dds.py - convert a PNG to a DXT5 .dds matching S2/LithTech weapon textures.

S2 weapon diffuse (_D) textures are DXT5 and use the ALPHA channel as a SPECULAR
MASK (not transparency): bright alpha = shiny, dark alpha = matte. A reskin saved
from an image editor usually comes out with alpha = all 255 (fully opaque), which
makes the whole surface uniformly shiny in-game -> the "looks alpha-like / washed"
problem. So by default this tool takes the RGB from your new PNG but COPIES the
spec-mask alpha from a reference texture (the one you reskinned), giving the new
skin the original material's shininess.

Usage:
  python png2dds.py new.png [out.dds] [--ref REF.png|REF.dds] [--alpha keep|opaque|ref]
                    [--brightness F] [--spec F]
    --ref          texture to copy the spec-mask alpha from (PNG or DDS)
    --alpha        keep   = use new.png's own alpha
                   opaque = force alpha 255 (uniform full spec)
                   ref    = copy alpha from --ref  (default when --ref given)
    --brightness F multiply the DIFFUSE (RGB) by F. <1 darkens a too-bright skin
                   (e.g. 0.8 = 20% darker, 0.6 = 40%). Fixes "skin too bright".
    --spec F       multiply the spec-mask ALPHA by F. <1 dulls the shine (0.5 = half,
                   0 = fully matte). Fixes "too shiny / washed / glary".
"""
import os
import sys
import numpy as np
from PIL import Image


def save_dds_rgba(rgba, out_path, brightness=1.0, spec=1.0, fmt='DXT5', verbose=True):
    """Write an in-memory RGBA buffer to a DDS, applying the same brightness (RGB
    multiply) and spec (alpha multiply) scaling as the CLI. `rgba` may be a PIL
    'RGBA' Image or a numpy (H,W,4) uint8 array. Used both by convert() below and
    directly by the skin editor so it never has to round-trip through a temp PNG."""
    if isinstance(rgba, np.ndarray):
        img = Image.fromarray(np.ascontiguousarray(rgba[:, :, :4]).astype('uint8'), 'RGBA')
    else:
        img = rgba.convert('RGBA')
    W, H = img.size
    rgb = img.convert('RGB')
    a = img.getchannel('A')
    if brightness != 1.0:                   # scale diffuse -> fixes too-bright skin
        rgb = Image.fromarray(np.clip(np.asarray(rgb, float) * brightness, 0, 255).astype('uint8'))
    if spec != 1.0:                         # scale spec mask -> fixes too-shiny skin
        a = Image.fromarray(np.clip(np.asarray(a, float) * spec, 0, 255).astype('uint8'))
    out = Image.merge('RGBA', (*rgb.split(), a))
    # DXT needs dimensions multiple of 4 (1024 already is)
    out.save(out_path, pixel_format=fmt)
    if verbose:
        amin, amax = a.getextrema()
        print('wrote %s  %dx%d %s  bright=%.2f spec=%.2f  alpha=%d..%d'
              % (out_path, W, H, fmt, brightness, spec, amin, amax))
    return out_path


def convert(png_path, out_path=None, ref_path=None, alpha_mode=None,
            brightness=1.0, spec=1.0):
    out_path = out_path or os.path.splitext(png_path)[0] + '.dds'
    src = Image.open(png_path).convert('RGBA')
    W, H = src.size
    if alpha_mode is None:
        alpha_mode = 'ref' if ref_path else 'keep'

    if alpha_mode == 'opaque':
        a = Image.new('L', (W, H), 255)
    elif alpha_mode == 'ref' and ref_path:
        ref = Image.open(ref_path).convert('RGBA')
        if ref.size != (W, H):
            ref = ref.resize((W, H), Image.LANCZOS)
        a = ref.getchannel('A')
    else:                                   # keep
        a = src.getchannel('A')

    rgb = src.convert('RGB')
    # Recombine then hand off to the shared writer (which re-applies brightness/spec).
    merged = Image.merge('RGBA', (*rgb.split(), a))
    return save_dds_rgba(merged, out_path, brightness=brightness, spec=spec)


if __name__ == '__main__':
    args = sys.argv[1:]

    def opt(name, default=None):
        return next((args[i + 1] for i, a in enumerate(args) if a == name), default)
    ref = opt('--ref'); amode = opt('--alpha')
    bright = float(opt('--brightness', 1.0)); spec = float(opt('--spec', 1.0))
    flagvals = {ref, amode, opt('--brightness'), opt('--spec')}
    pos = [a for i, a in enumerate(args)
           if not a.startswith('--') and (i == 0 or not args[i - 1].startswith('--'))]
    if not pos:
        print(__doc__)
        sys.exit(1)
    convert(pos[0], pos[1] if len(pos) > 1 else None, ref, amode, bright, spec)

"""mat00io.py - read/write LithTech .Mat00 (LTMI) material files.

Layout (little-endian):
    'LTMI'
    u32   version (always 1 in retail / what we write)
    u16   shader-name length + ascii shader path (e.g. shaders\\skeletal\\Solid\\...fx)
    u32   prop_count
    per prop:
        u32  type   (1=string, 2=float3, 3=float4, 4=int, 5=float)
        u16  name length + ascii name
        value per type

Matches tools/lightmapbaker/lightmap_assets.build_mat00 / mdl2obj.parse_mat00, but
round-trips ALL value types so we can clone a real material and only swap the
diffuse path, keeping everything else byte-faithful.
"""
import struct

MAGIC = b'LTMI'
T_STRING, T_FLOAT3, T_FLOAT4, T_INT, T_FLOAT = 1, 2, 3, 4, 5

# A reasonable default if we ever have to synthesize a weapon material from scratch
# (SN_AWM300.Mat00's shader). Cloning a real material is always preferred.
DEFAULT_WEAPON_SHADER = r'shaders\skeletal\Solid\HHdiffuse_weapon.fx'


def _rd_str(d, o):
    n = struct.unpack_from('<H', d, o)[0]
    o += 2
    return d[o:o + n].decode('latin-1'), o + n


def _wr_str(s):
    b = s.encode('latin-1')
    return struct.pack('<H', len(b)) + b


def parse(data):
    """Return (version, shader, props) where props is [(name, type, value)]."""
    if data[:4] != MAGIC:
        raise ValueError('not an LTMI .Mat00')
    o = 4
    version = struct.unpack_from('<I', data, o)[0]; o += 4
    shader, o = _rd_str(data, o)
    nprops = struct.unpack_from('<I', data, o)[0]; o += 4
    props = []
    for _ in range(nprops):
        ptype = struct.unpack_from('<I', data, o)[0]; o += 4
        name, o = _rd_str(data, o)
        if ptype == T_STRING:
            val, o = _rd_str(data, o)
        elif ptype == T_FLOAT3:
            val = struct.unpack_from('<3f', data, o); o += 12
        elif ptype == T_FLOAT4:
            val = struct.unpack_from('<4f', data, o); o += 16
        elif ptype == T_INT:
            val = struct.unpack_from('<i', data, o)[0]; o += 4
        elif ptype == T_FLOAT:
            val = struct.unpack_from('<f', data, o)[0]; o += 4
        else:
            raise ValueError('unknown Mat00 prop type %d' % ptype)
        props.append((name, ptype, val))
    return version, shader, props


def build(version, shader, props):
    out = bytearray(MAGIC)
    out += struct.pack('<I', version)
    out += _wr_str(shader)
    out += struct.pack('<I', len(props))
    for name, ptype, val in props:
        out += struct.pack('<I', ptype)
        out += _wr_str(name)
        if ptype == T_STRING:
            out += _wr_str(val)
        elif ptype == T_FLOAT3:
            out += struct.pack('<3f', *val)
        elif ptype == T_FLOAT4:
            out += struct.pack('<4f', *val)
        elif ptype == T_INT:
            out += struct.pack('<i', int(val))
        elif ptype == T_FLOAT:
            out += struct.pack('<f', float(val))
        else:
            raise ValueError('unknown Mat00 prop type %d' % ptype)
    return bytes(out)


# Stock, already-compiled shaders used to make a weapon glow. Verified param sets:
#   specular.fx                  -> tDiffuseMap, tEmissiveMap, tSpecularMap, tNormalMap
#       (world / 3rd-person): true emissive bloom + specular highlight.
#   PVdiffuse_weapon_SpecColor.fx -> tDiffuseMap, tNormalMap, tSpecularMap, tEnvironmentMap
#       (first-person view): NO emissive, but a colored tSpecularMap gives a glow that
#       flares under light. PV (first-person) materials retarget at THIS one.
GLOW_SHADER = r'shaders\skeletal\Solid\specular.fx'
PV_GLOW_SHADER = r'shaders\skeletal\Solid\PVdiffuse_weapon_SpecColor.fx'
# Custom shader (shaders2\{rigid,skeletal}\Solid\specular_glow.fx) = specular.fx + an
# ANIMATED emissive glow that the engine's ScreenGlow blooms into a pulsing beam. Unlike
# GLOW_SHADER its emissive is scaled by fGlowScale and pulses via fTime, and it works for
# BOTH the world and first-person (PV) view (one shader; engine supplies mObjectToClip).
GLOW_PULSE_SHADER = r'shaders\skeletal\Solid\specular_glow.fx'
# Elemental variants (animated fire / lightning). Reuse the same scalar param NAMES as the
# glow shader, but each declares only a subset (see GLOW_PARAMS) so materials stay valid.
SPECULAR_FIRE_SHADER = r'shaders\skeletal\Solid\specular_fire.fx'
SPECULAR_ELECTRIC_SHADER = r'shaders\skeletal\Solid\specular_electric.fx'


def reskin(orig_bytes, diffuse_path, emissive_path=None, spec_path=None, shader=None,
           add_maps=None):
    """Clone an existing material, swapping tDiffuseMap (and optionally tEmissiveMap /
    tSpecularMap / shader). Paths are game-relative with backslashes; other props
    (normal map, scalars, decal) are preserved.

    A map property is only ADDED when missing if its key is in `add_maps` (lowercase,
    e.g. {'temissivemap','tspecularmap'}); otherwise it is only swapped when the
    original already declared it. This matters because a material's properties MUST
    match the parameters its shader declares — adding tEmissiveMap to a shader that
    doesn't have it (e.g. PVdiffuse_weapon_decal.fx) makes the engine reject the
    material. Only retarget the shader (GLOW_SHADER) and pass add_maps together, since
    that shader is known to declare those maps. tDiffuseMap is always safe to add."""
    version, src_shader, props = parse(orig_bytes)
    out_shader = shader if shader else src_shader
    allow = set(m.lower() for m in (add_maps or ()))
    setmap = {'tdiffusemap': diffuse_path}
    if emissive_path is not None:
        setmap['temissivemap'] = emissive_path
    if spec_path is not None:
        setmap['tspecularmap'] = spec_path
    seen = set()
    new = []
    for name, t, v in props:
        key = name.lower()
        if key in setmap:
            new.append((name, T_STRING, setmap[key])); seen.add(key)
        else:
            new.append((name, t, v))
    for key, label in (('tdiffusemap', 'tDiffuseMap'), ('temissivemap', 'tEmissiveMap'),
                       ('tspecularmap', 'tSpecularMap')):
        if key in setmap and key not in seen and (key == 'tdiffusemap' or key in allow):
            new.append((label, T_STRING, setmap[key]))
    return build(version, out_shader, new)


# EXACT declared parameter sets of the glow shaders (from the .fx MIPARAM lists).
# A material may only contain props its shader declares, so when we RETARGET the
# shader we rebuild from this whitelist (dropping e.g. tDecalMap, which neither glow
# shader has) instead of cloning the original's props.
_SCALAR_DEFAULTS = {
    'surfaceflags':      ('SurfaceFlags', T_INT, 0),
    'defaultwidth':      ('DefaultWidth', T_FLOAT, 100.0),
    'defaultheight':     ('DefaultHeight', T_FLOAT, 100.0),
    'fmaxspecularpower': ('fMaxSpecularPower', T_FLOAT, 64.0),
    'fspecularintensity': ('fSpecularIntensity', T_FLOAT, 1.0),
    # specular_glow.fx animated-glow controls (see the .fx for meaning/ranges)
    'fglowscale':       ('fGlowScale', T_FLOAT, 3.0),
    'fglowpulsespeed':  ('fGlowPulseSpeed', T_FLOAT, 0.8),
    'fglowpulseamount': ('fGlowPulseAmount', T_FLOAT, 0.4),
    'fglowblur':        ('fGlowBlur', T_FLOAT, 0.006),
    # specular_glow.fx moving-flow pattern
    'fflowamount':      ('fFlowAmount', T_FLOAT, 0.0),
    'fflowspeed':       ('fFlowSpeed', T_FLOAT, 0.3),
    'fflowfreq':        ('fFlowFreq', T_FLOAT, 3.0),
    'fflowdir':         ('fFlowDir', T_FLOAT, 1.0),
    # fresnel rim / scrolling energy / hue cycle
    'frimscale':        ('fRimScale', T_FLOAT, 0.0),
    'frimpower':        ('fRimPower', T_FLOAT, 3.0),
    'fscrollspeed':     ('fScrollSpeed', T_FLOAT, 0.0),
    'fhuespeed':        ('fHueSpeed', T_FLOAT, 0.0),
    'femissivetile':    ('fEmissiveTile', T_FLOAT, 1.0),
    'fflowspace':       ('fFlowSpace', T_FLOAT, 0.0),
    'vglowtint':        ('vGlowTint', T_FLOAT3, (1.0, 1.0, 1.0)),
}
_TEX_LABEL = {'tdiffusemap': 'tDiffuseMap', 'temissivemap': 'tEmissiveMap',
              'tspecularmap': 'tSpecularMap', 'tnormalmap': 'tNormalMap'}
GLOW_PARAMS = {
    GLOW_SHADER: {  # specular.fx  (world / 3rd-person) - has emissive, NO fSpecularIntensity
        'scalars': ['surfaceflags', 'defaultwidth', 'defaultheight', 'fmaxspecularpower'],
        'textures': ['tdiffusemap', 'temissivemap', 'tspecularmap', 'tnormalmap'],
    },
    PV_GLOW_SHADER: {  # PVdiffuse_weapon_SpecColor.fx (first-person) - NO emissive
        'scalars': ['surfaceflags', 'defaultwidth', 'defaultheight',
                    'fmaxspecularpower', 'fspecularintensity'],
        'textures': ['tdiffusemap', 'tnormalmap', 'tspecularmap'],
    },
    GLOW_PULSE_SHADER: {  # specular_glow.fx - animated emissive bloom, world + PV view
        'scalars': ['surfaceflags', 'defaultwidth', 'defaultheight', 'fmaxspecularpower',
                    'fglowscale', 'fglowpulsespeed', 'fglowpulseamount', 'fglowblur',
                    'fflowamount', 'fflowspeed', 'fflowfreq', 'fflowdir',
                    'frimscale', 'frimpower', 'fscrollspeed', 'fhuespeed', 'femissivetile',
                    'fflowspace', 'vglowtint'],
        'textures': ['tdiffusemap', 'temissivemap', 'tspecularmap', 'tnormalmap'],
    },
    SPECULAR_FIRE_SHADER: {   # specular_fire.fx (declares only this subset)
        'scalars': ['surfaceflags', 'defaultwidth', 'defaultheight', 'fmaxspecularpower',
                    'fglowscale', 'fglowpulsespeed', 'fglowpulseamount', 'fscrollspeed',
                    'femissivetile', 'vglowtint'],
        'textures': ['tdiffusemap', 'temissivemap', 'tspecularmap', 'tnormalmap'],
    },
    SPECULAR_ELECTRIC_SHADER: {   # specular_electric.fx (same subset)
        'scalars': ['surfaceflags', 'defaultwidth', 'defaultheight', 'fmaxspecularpower',
                    'fglowscale', 'fglowpulsespeed', 'fglowpulseamount', 'fscrollspeed',
                    'femissivetile', 'vglowtint'],
        'textures': ['tdiffusemap', 'temissivemap', 'tspecularmap', 'tnormalmap'],
    },
}
# All our custom effect shaders (lowercased), so the Effects panel can find & retarget them.
OUR_EFFECT_SHADERS = {s.lower() for s in (GLOW_PULSE_SHADER, SPECULAR_FIRE_SHADER, SPECULAR_ELECTRIC_SHADER)}


# A broad (low) specular power so the colored spec glow reads as a soft halo under
# light instead of a tiny pinpoint highlight (high power = tight). The spec map is
# black except on the glow areas, so this only affects those.
GLOW_SPEC_POWER = 8.0


def build_glow(shader, orig_bytes, maps, spec_power=None, scalar_overrides=None):
    """Build a CLEAN glow material for `shader` using only the params it declares.
    Scalar values + the normal map are pulled from `orig_bytes` (the material being
    reskinned) when present so the look matches; `maps` (lowercase texname -> backslash
    path) overrides the texture maps. `spec_power` overrides fMaxSpecularPower (lower =
    broader, more visible specular glow). `scalar_overrides` (lowercase scalar name ->
    value, e.g. {'fglowscale': 4.0}) forces specific scalar values, e.g. the glowanim
    pulse knobs. tDecalMap and any other unsupported props are dropped so the engine
    accepts the material."""
    spec = GLOW_PARAMS[shader]
    over = {k.lower(): v for k, v in (scalar_overrides or {}).items()}
    orig = {}
    if orig_bytes:
        try:
            _, _, props = parse(orig_bytes)
            for n, t, v in props:
                orig[n.lower()] = (n, t, v)
        except Exception:
            pass
    out = []
    for key in spec['scalars']:
        if key in over:
            label = _SCALAR_DEFAULTS[key][0]
            out.append((label, _SCALAR_DEFAULTS[key][1], over[key]))
        elif key == 'fmaxspecularpower' and spec_power is not None:
            out.append(('fMaxSpecularPower', T_FLOAT, float(spec_power)))
        else:
            out.append(orig[key] if key in orig else _SCALAR_DEFAULTS[key])
    for key in spec['textures']:
        val = maps.get(key)
        if not val and key in orig and orig[key][1] == T_STRING:
            val = orig[key][2]                       # keep original normal map, etc.
        if val:
            out.append((_TEX_LABEL[key], T_STRING, val))
    return build(1, shader, out)


def set_scalars(data, overrides, add_if_missing=True):
    """Update float/int scalar params in an existing .Mat00 IN PLACE (keeps shader,
    textures and every other prop byte-faithful). `overrides` is {lowercase name ->
    value}, e.g. {'fglowscale': 4.0}. Missing scalars listed in _SCALAR_DEFAULTS are
    appended when add_if_missing. Returns (new_bytes, shader). Used by the Glow panel to
    tweak fGlowScale/fGlowPulseSpeed/fGlowPulseAmount without rebuilding the material."""
    ver, shader, props = parse(data)
    over = {k.lower(): (tuple(float(x) for x in v) if isinstance(v, (tuple, list)) else float(v))
            for k, v in overrides.items()}
    seen, out = set(), []
    for n, t, v in props:
        k = n.lower()
        if k in over:
            out.append((n, t, over[k])); seen.add(k)
        else:
            out.append((n, t, v))
    if add_if_missing:
        for k, val in over.items():
            if k not in seen and k in _SCALAR_DEFAULTS:
                lbl, typ, _ = _SCALAR_DEFAULTS[k]
                out.append((lbl, typ, val))
    return build(ver, shader, out), shader


def synthesize(diffuse_path, shader=DEFAULT_WEAPON_SHADER, normal_path=None,
               spec_path=None, emissive_path=None):
    """Build a minimal material from scratch (used only when no original .Mat00 can
    be found to clone). Less reliable than reskin() - prefer cloning."""
    props = [('tDiffuseMap', T_STRING, diffuse_path)]
    if emissive_path:
        props.append(('tEmissiveMap', T_STRING, emissive_path))
    if normal_path:
        props.append(('tNormalMap', T_STRING, normal_path))
    if spec_path:
        props.append(('tSpecularMap', T_STRING, spec_path))
    return build(1, shader, props)

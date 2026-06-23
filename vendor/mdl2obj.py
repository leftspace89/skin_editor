#!/usr/bin/env python3
"""
mdl2obj - Export District 187 / S2 SonSilah .Model00p (Lithtech Jupiter EX, MODL v33)
geometry to Wavefront .obj + .mtl for Blender.

Format reverse-engineered from TheRaw.exe (Model_Load @0x442469) + sample models.

Header (little-endian):
  char[4]  'MODL'
  u32      version (== 0x21 / 33)
  u32[21]  header counts (hdr[9] = string-pool byte size, hdr[2] = node_count, ...)
  -> string pool starts at file offset 0x5C, hdr[9] bytes, NUL-terminated names
     referenced by offset (offset 0 == empty name).

Mesh blocks (one or more "pieces"), each:
  u32      vbuf_size  (multiple of 72)         -> vert_count = vbuf_size // 72
  u32      ibuf_size  (multiple of 2)          -> index_count = ibuf_size // 2
  vbuf     vert_count * 72-byte interleaved vertices
  ibuf     index_count * u16 triangle indices

72-byte vertex record (all floats LE):
  +0   position  float3
  +12  normal    float3   (unit)
  +24  uv0       float2
  +32  uv1       float2
  +40  tangent   float3   (unit)
  +52  binormal  float3   (unit)
  +64  diffuse   u32 (D3DCOLOR)
  +68  (4 bytes, padding/unused)
"""
import os, sys, struct

VSTRIDE = 72

# Where the game install lives (materials/textures are referenced relative to it).
# Override with env S2_GAME_ROOT or --game-root=PATH.
GAME_ROOT = os.environ.get('S2_GAME_ROOT', '')

# Directories searched (relative to GAME_ROOT) for a model's <name>.Mat00.
MAT_DIRS = ['weapons/1_main/materials', 'Materials', '']

# Texture-filename suffix -> semantic map role. The shaders bind these siblings
# by naming convention (the .Mat00 usually only spells out the diffuse).
_SUFFIX_ROLE = {
    '_D': 'diffuse', '_N': 'normal', '_R': 'roughness',
    '_C': 'spec', '_S': 'spec', '_EM': 'emissive',
}
_MAT_MAGIC = b'LTMI'


def _read_ltstring(data, pos):
    n = struct.unpack_from('<H', data, pos)[0]
    pos += 2
    return data[pos:pos + n].decode('ascii', 'replace'), pos + n


def parse_mat00(path):
    """Parse a Lithtech .Mat00 (LTMI). Return {def_name: value} for the first Fx."""
    try:
        data = open(path, 'rb').read()
    except OSError:
        return None
    if len(data) < 8 or data[:4] != _MAT_MAGIC:
        return None
    if struct.unpack_from('<I', data, 4)[0] == 0:
        return None
    pos = 8
    shader, pos = _read_ltstring(data, pos)
    def_count = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    defs = {'_shader_file': shader}
    for _ in range(def_count):
        type_id = struct.unpack_from('<I', data, pos)[0]; pos += 4
        name, pos = _read_ltstring(data, pos)
        if type_id == 1:    value, pos = _read_ltstring(data, pos)
        elif type_id == 2:  value = struct.unpack_from('<3f', data, pos); pos += 12
        elif type_id == 3:  value = struct.unpack_from('<4f', data, pos); pos += 16
        elif type_id == 4:  value = struct.unpack_from('<i', data, pos)[0]; pos += 4
        elif type_id == 5:  value = struct.unpack_from('<f', data, pos)[0]; pos += 4
        else:               return defs
        defs[name] = value
    return defs


def _discover_siblings(diffuse_abs):
    """Given an absolute path to a *_D.dds diffuse, find sibling maps by suffix
    convention (the shader binds _N/_R/_C/_EM that the .Mat00 doesn't list).
    Returns {role: abs_path}."""
    d = os.path.dirname(diffuse_abs)
    fn = os.path.basename(diffuse_abs)
    low = fn.lower()
    stem = None
    for suf in ('_d.dds.dds', '_d.dds'):
        if low.endswith(suf):
            stem = fn[:len(fn) - len(suf)]
            break
    roles = {'diffuse': diffuse_abs.replace('\\', '/')}
    if stem is None:
        return roles
    try:
        siblings = os.listdir(d)
    except OSError:
        return roles
    sl = {s.lower(): s for s in siblings}
    for suf, role in _SUFFIX_ROLE.items():
        if role == 'diffuse':
            continue
        cand = (stem + suf + '.dds').lower()
        if cand in sl and role not in roles:
            roles[role] = os.path.join(d, sl[cand]).replace('\\', '/')
    return roles


def resolve_material(model_name, game_root, skin=None, suffix=''):
    """Find <model_name>[-<skin>]<suffix>.Mat00 and return resolved texture roles.

    suffix is a piece tag like '_Scope'/'_Lens'; the skin sits in the middle so
    e.g. model=SN_AWM300 skin=SE suffix=_Scope -> 'SN_AWM300-SE_Scope.Mat00'.
    Falls back to the un-skinned material if the skinned one is absent.
    Returns {'mat_file','shader','roles':{diffuse,normal,roughness,spec,emissive}}.
    """
    candidates = []
    if skin:
        candidates.append('%s-%s%s' % (model_name, skin, suffix))
    candidates.append('%s%s' % (model_name, suffix))
    for nm in candidates:
        for md in MAT_DIRS:
            mat_path = os.path.join(game_root, md, nm + '.Mat00')
            defs = parse_mat00(mat_path)
            if defs is None:
                continue
            rel = defs.get('tDiffuseMap')
            roles = {}
            if isinstance(rel, str) and rel.lower().endswith('.dds'):
                diff = os.path.join(game_root, rel.replace('\\', '/'))
                if os.path.isfile(diff):
                    roles = _discover_siblings(diff)
            return {'mat_file': mat_path, 'shader': defs.get('_shader_file', ''),
                    'roles': roles}
    return None

def read_header(d):
    assert d[:4] == b'MODL', 'not a MODL file: %r' % d[:4]
    version = struct.unpack_from('<I', d, 4)[0]
    hdr = struct.unpack_from('<21I', d, 8)
    pool_off = 0x5C
    pool_size = hdr[9]
    pool = d[pool_off:pool_off + pool_size]
    names = {}
    o = 0
    while o < len(pool):
        e = pool.find(b'\x00', o)
        if e < 0:
            e = len(pool)
        if e > o:
            names[o] = pool[o:e].decode('ascii', 'replace')
        o = e + 1
    return version, hdr, pool_size, names


def _unit(x, y, z, tol=0.06):
    m = x * x + y * y + z * z
    return abs(m - 1.0) <= tol


def find_mesh_blocks(d, start, stride=VSTRIDE, nrm_off=12):
    """Scan for (vbuf_size, ibuf_size) headed mesh blocks from `start`.

    The pair sits immediately before the vertex buffer; the buffer is NOT
    always 4-byte aligned (e.g. MAR_PDWK101 begins at an odd offset), so we
    step byte-by-byte and rely on validation (unit normals + in-range indices).
    `stride` comes from the vertex declaration (72B weapons / 64B characters).
    """
    n = len(d)
    blocks = []
    p = start
    while p + 8 <= n:
        vsize, isize = struct.unpack_from('<II', d, p)
        ok = (vsize >= stride and vsize % stride == 0 and
              isize >= 6 and isize % 6 == 0 and
              p + 8 + vsize + isize <= n)
        if ok:
            vcount = vsize // stride
            icount = isize // 2
            vbuf = p + 8
            ibuf = vbuf + vsize
            # validate: first two vertex normals are unit, all indices in range
            nx, ny, nz = struct.unpack_from('<3f', d, vbuf + nrm_off)
            n2 = struct.unpack_from('<3f', d, vbuf + stride + nrm_off) if vcount > 1 else (nx, ny, nz)
            if _unit(nx, ny, nz) and _unit(*n2):
                idx = struct.unpack_from('<%dH' % icount, d, ibuf)
                if max(idx) < vcount:
                    blocks.append({'off': p, 'vcount': vcount, 'vbuf': vbuf,
                                   'icount': icount, 'ibuf': ibuf})
                    p = ibuf + isize
                    continue
        p += 1
    return blocks


def _largest_contiguous_start(idx, icount, vcount):
    """Pieces are contiguous vertex ranges that no triangle straddles. Return the
    start vertex of the LARGEST such range (= the body, which dominates) so we can
    split the scope/sight assembly (everything before it) from the body."""
    diff = [0] * (vcount + 2)
    for t in range(0, icount, 3):
        a, b, c = idx[t], idx[t + 1], idx[t + 2]
        lo, hi = min(a, b, c), max(a, b, c)
        diff[lo + 1] += 1
        diff[hi + 1] -= 1
    cover = 0
    cuts = [0]
    for k in range(1, vcount):
        cover += diff[k]
        if cover == 0:
            cuts.append(k)
    cuts.append(vcount)
    best = (0, 0, vcount)
    for i in range(len(cuts) - 1):
        s, e = cuts[i], cuts[i + 1]
        if e - s > best[0]:
            best = (e - s, s, e)
    return best[1]   # start vertex of the largest range


def detect_pieces(d, block, base, game_root, skin):
    """Split the single mesh into named pieces (body + scope assembly) by their
    contiguous vertex ranges, and resolve each piece's material. Only splits when
    a '<base>_Scope' material exists; otherwise returns a single body piece."""
    vcount, icount, ibuf = block['vcount'], block['icount'], block['ibuf']
    body_info = resolve_material(base, game_root, skin=skin, suffix='')
    scope_info = resolve_material(base, game_root, skin=skin, suffix='_Scope')
    if scope_info is None:
        return [{'name': base, 'lo': 0, 'hi': vcount, 'info': body_info}]
    idx = struct.unpack_from('<%dH' % icount, d, ibuf)
    body_start = _largest_contiguous_start(idx, icount, vcount)
    if body_start <= 0:
        return [{'name': base, 'lo': 0, 'hi': vcount, 'info': body_info}]
    # everything before the body's vertex range is the scope/sight assembly
    return [
        {'name': base + '_Scope', 'lo': 0, 'hi': body_start, 'info': scope_info},
        {'name': base, 'lo': body_start, 'hi': vcount, 'info': body_info},
    ]


_ROLE_MTL = {'diffuse': 'map_Kd', 'normal': 'norm', 'spec': 'map_Ks', 'emissive': 'map_Ke'}


def _write_mtl(mtl, mat_name, info):
    roles = info['roles'] if info else {}
    mtl.write('newmtl %s\n' % mat_name)
    if roles:
        if info.get('shader'):
            mtl.write('# shader: %s\n' % info['shader'])
        mtl.write('Kd 1.0 1.0 1.0\nKa 0.0 0.0 0.0\nKs 0.2 0.2 0.2\nNs 60\n')
        for role, kw in _ROLE_MTL.items():
            if role in roles:
                mtl.write('%s %s\n' % (kw, roles[role]))
        # crude bump alias too (older importers)
        if 'normal' in roles:
            mtl.write('map_Bump -bm 1.0 %s\n' % roles['normal'])
    else:
        mtl.write('Kd 0.6 0.6 0.6\n')  # fallback grey
    mtl.write('\n')


def export(path, out_dir=None, flip_winding=True, flip_v=True, flip_u=False, swap_yz=True,
           game_root=GAME_ROOT, skin=None, mat_registry=None):
    d = open(path, 'rb').read()
    version, hdr, pool_size, names = read_header(d)
    base = os.path.splitext(os.path.basename(path))[0]
    out_dir = out_dir or os.path.dirname(os.path.abspath(path))
    obj_path = os.path.join(out_dir, base + '.obj')
    mtl_path = os.path.join(out_dir, base + '.mtl')

    import model00p
    stride, voff = model00p.parse_vertex_decl(d)
    nrm_off = voff.get('NORMAL', 12)
    uv_off = voff.get('TEXCOORD0', 24)
    mesh_start = 0x5C + pool_size
    blocks = find_mesh_blocks(d, mesh_start, stride, nrm_off)
    if not blocks:
        raise SystemExit('No mesh blocks found in %s' % path)

    # split each block into named pieces (body / scope) with per-piece materials
    block_pieces = [detect_pieces(d, b, base, game_root, skin) for b in blocks]
    summary = []
    for pcs in block_pieces:
        for p in pcs:
            if mat_registry is not None and p['info'] and p['info']['roles']:
                mat_registry[p['name']] = p['info']['roles']
            summary.append('%s[%s]' % (p['name'].replace(base, '') or 'body',
                                       ','.join(sorted(p['info']['roles'])) if p['info'] and p['info']['roles'] else '-'))
    print('%s  v%d  pool=%dB  mesh-blocks=%d  pieces=%s'
          % (base, version, pool_size, len(blocks), ' '.join(summary)))

    with open(obj_path, 'w') as obj, open(mtl_path, 'w') as mtl:
        obj.write('# %s  exported from %s (MODL v%d)\n' % (base, os.path.basename(path), version))
        obj.write('mtllib %s\n' % os.path.basename(mtl_path))
        written_mtls = set()
        vbase = 0
        for bi, b in enumerate(blocks):
            vb = b['vbuf']
            for vi in range(b['vcount']):
                vo = vb + vi * stride
                px, py, pz = struct.unpack_from('<3f', d, vo + voff.get('POSITION', 0))
                nx, ny, nz = struct.unpack_from('<3f', d, vo + nrm_off)
                u0, v0 = struct.unpack_from('<2f', d, vo + uv_off)
                if swap_yz:
                    # Lithtech (LH, Y-up) -> Blender (RH, Z-up): (x,y,z) -> (-x, z, -y).
                    # This is a det = -1 transform, so it un-mirrors the model (text reads
                    # correctly). The extra X/Y' negation is a 180 deg roll about the long
                    # axis so the gun lands right-side-up instead of upside-down. Negating a
                    # single axis would flip det back to +1 and re-mirror the texture.
                    px, py, pz = -px, pz, -py
                    nx, ny, nz = -nx, nz, -ny
                obj.write('v %.6f %.6f %.6f\n' % (px, py, pz))
                obj.write('vn %.6f %.6f %.6f\n' % (nx, ny, nz))
                obj.write('vt %.6f %.6f\n' % ((1.0 - u0) if flip_u else u0,
                                              (1.0 - v0) if flip_v else v0))
            idx = struct.unpack_from('<%dH' % b['icount'], d, b['ibuf'])
            # the Y/Z swap inverts winding parity, so combine it with flip_winding (XOR)
            eff_flip = flip_winding != swap_yz
            pieces = block_pieces[bi]
            for p in pieces:
                if p['name'] not in written_mtls:
                    _write_mtl(mtl, p['name'], p['info'])
                    written_mtls.add(p['name'])
                obj.write('\no %s\n' % p['name'])
                obj.write('usemtl %s\n' % p['name'])
                lo, hi = p['lo'], p['hi']
                ntri = 0
                for t in range(0, b['icount'], 3):
                    a, c, e = idx[t], idx[t + 1], idx[t + 2]
                    mx = max(a, c, e)
                    if not (lo <= mx < hi):
                        continue
                    if eff_flip:
                        a, c, e = a, e, c
                    # OBJ is 1-based; v/vt/vn share the same index here
                    f = tuple(vbase + 1 + x for x in (a, c, e))
                    obj.write('f %d/%d/%d %d/%d/%d %d/%d/%d\n'
                              % (f[0], f[0], f[0], f[1], f[1], f[1], f[2], f[2], f[2]))
                    ntri += 1
                print('  piece %-18s %d tris (verts %d..%d)' % (p['name'], ntri, lo, hi))
            vbase += b['vcount']
    print('  -> %s' % obj_path)
    print('  -> %s' % mtl_path)


BLENDER_HEADER = '''"""Run in Blender (Scripting tab) AFTER importing the .obj(s).
Builds proper Principled BSDF node trees: diffuse + normal map + roughness +
emissive, with data maps set Non-Color. Auto-generated by mdl2obj.py.
Material names match the imported OBJ object/material names.
"""
import bpy, os

# material name -> {role: abs_dds_path}
MATERIALS = %s

INVERT_ROUGHNESS = False   # flip if the gun looks too shiny/too matte

def _img(path, non_color):
    if not path or not os.path.isfile(path):
        return None
    img = bpy.data.images.load(path, check_existing=True)
    if non_color:
        try: img.colorspace_settings.name = 'Non-Color'
        except Exception: pass
    return img

def build(name, roles):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new('ShaderNodeOutputMaterial'); out.location = (600, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled'); bsdf.location = (200, 0)
    nt.links.new(out.inputs['Surface'], bsdf.outputs['BSDF'])
    x, y = -600, 400
    d = _img(roles.get('diffuse'), False)
    if d:
        t = nt.nodes.new('ShaderNodeTexImage'); t.image = d; t.location = (x, y); y -= 320
        nt.links.new(bsdf.inputs['Base Color'], t.outputs['Color'])
    r = _img(roles.get('roughness'), True)
    if r and 'Roughness' in bsdf.inputs:
        t = nt.nodes.new('ShaderNodeTexImage'); t.image = r; t.location = (x, y); y -= 320
        src = t.outputs['Color']
        if INVERT_ROUGHNESS:
            inv = nt.nodes.new('ShaderNodeInvert'); inv.location = (x + 250, y + 320)
            nt.links.new(inv.inputs['Color'], src); src = inv.outputs['Color']
        nt.links.new(bsdf.inputs['Roughness'], src)
    n = _img(roles.get('normal'), True)
    if n:
        t = nt.nodes.new('ShaderNodeTexImage'); t.image = n; t.location = (x, y); y -= 320
        nm = nt.nodes.new('ShaderNodeNormalMap'); nm.location = (x + 250, y + 320)
        nt.links.new(nm.inputs['Color'], t.outputs['Color'])
        nt.links.new(bsdf.inputs['Normal'], nm.outputs['Normal'])
    em = _img(roles.get('emissive'), False)
    if em:
        t = nt.nodes.new('ShaderNodeTexImage'); t.image = em; t.location = (x, y); y -= 320
        inp = 'Emission Color' if 'Emission Color' in bsdf.inputs else 'Emission'
        if inp in bsdf.inputs:
            nt.links.new(bsdf.inputs[inp], t.outputs['Color'])
            if 'Emission Strength' in bsdf.inputs: bsdf.inputs['Emission Strength'].default_value = 1.0
    return mat

applied = 0
for name, roles in MATERIALS.items():
    m = bpy.data.materials.get(name)
    if m is None:
        # also match Blender's .001 suffixing
        m = next((mm for mm in bpy.data.materials if mm.name.split('.')[0] == name), None)
    target = m.name if m else name
    build(target, roles)
    applied += 1
print('built %%d weapon materials' %% applied)
'''


def write_blender_script(out_dir, registry):
    import json
    path = os.path.join(out_dir, 'apply_weapon_materials.py')
    body = BLENDER_HEADER % json.dumps(registry, indent=4)
    open(path, 'w').write(body)
    print('  -> %s  (run in Blender for normal/rough/emissive)' % path)


if __name__ == '__main__':
    game_root = GAME_ROOT
    skin = None
    opts = dict(swap_yz=True, flip_winding=True, flip_v=True, flip_u=False)
    for a in sys.argv[1:]:
        if a.startswith('--game-root='):
            game_root = a.split('=', 1)[1]
        elif a.startswith('--skin='):
            skin = a.split('=', 1)[1]
        elif a == '--no-swap-yz':       opts['swap_yz'] = False
        elif a == '--no-flip-winding':  opts['flip_winding'] = False
        elif a == '--no-flip-v':        opts['flip_v'] = False
        elif a == '--flip-u':           opts['flip_u'] = True
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    out_dir = None
    if not args:
        # default: export all models in ./S2Models
        here = os.path.dirname(os.path.abspath(__file__))
        sm = os.path.join(here, 'S2Models')
        args = [os.path.join(sm, f) for f in os.listdir(sm) if f.lower().endswith('.model00p')]
        out_dir = sm
    registry = {}
    for p in args:
        try:
            export(p, game_root=game_root, skin=skin, mat_registry=registry, **opts)
        except SystemExit as e:
            print('SKIP %s: %s' % (os.path.basename(p), e))
    if registry:
        write_blender_script(out_dir or os.path.dirname(os.path.abspath(args[0])), registry)

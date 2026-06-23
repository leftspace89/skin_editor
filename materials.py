"""materials.py - load a weapon .Model00p into editor-ready geometry + materials.

Wraps the existing tools/model00p parsers (model00p.parse, mdl2obj.resolve_material/
detect_pieces/_discover_siblings) so we don't duplicate any binary format logic.

Produces a LoadedModel with:
  * interleaved numpy vertex array (pos, normal, uv0, tangent, binormal), with the
    LithTech->right-handed transform (-x, z, -y) applied to positions and the three
    direction vectors (UVs left raw - they index the texture directly).
  * draw_groups: list of {name, faces (M,3 int32), texset} - one per material piece
    (body / scope). Geometry of all groups shares the single vertex array.
  * skins: list of available skin tags discovered from <base>-*.Mat00 files.
"""
import os
import re
import sys

import numpy as np

# Make the sibling tools/model00p package importable (mirrors how mdl2obj imports
# model00p as a sibling). __file__ -> tools/skin_edit/materials.py
_HERE = os.path.dirname(os.path.abspath(__file__))
_MODEL00P_DIR = os.path.normpath(os.path.join(_HERE, '..', 'model00p'))
if _MODEL00P_DIR not in sys.path:
    sys.path.insert(0, _MODEL00P_DIR)

import model00p          # noqa: E402
import mdl2obj           # noqa: E402

ROLES = ('diffuse', 'normal', 'roughness', 'spec', 'emissive')


def candidate_bases(model_name):
    """Material base-name candidates for a model file stem. View models carry a
    LOD digit (SN_AWM3001) while the material is SN_AWM300, so try the stem then
    progressively strip trailing digits."""
    cands = [model_name]
    stripped = re.sub(r'\d+$', '', model_name)
    if stripped and stripped != model_name:
        cands.append(stripped)
    return cands


def textures_dir(game_root):
    return os.path.join(game_root, 'weapons', '1_main', 'textures')


# Cached lowercase-filename -> abspath index of every .dds under a game root, built
# lazily on the first lookup that misses the fast (near-the-model) search. This lets
# us resolve textures for ANY weapon-category folder layout (melee, parts, ...), not
# just weapons/1_main/textures.
_DDS_INDEX = {}


def _dds_index(game_root):
    idx = _DDS_INDEX.get(game_root)
    if idx is None:
        idx = {}
        for dp, _dirs, files in os.walk(game_root):
            for f in files:
                if f.lower().endswith('.dds'):
                    idx.setdefault(f.lower(), os.path.join(dp, f))
        _DDS_INDEX[game_root] = idx
        print('skin_edit: indexed %d textures under %s' % (len(idx), game_root))
    return idx


def _search_dirs(game_root, model_dir):
    """Fast directories to probe for a texture before falling back to the index:
    the model's own dir, a sibling/child 'textures' dir, and weapons/1_main."""
    dirs = []
    if model_dir:
        dirs += [model_dir,
                 os.path.normpath(os.path.join(model_dir, '..', 'textures')),
                 os.path.join(model_dir, 'textures'),
                 os.path.normpath(os.path.join(model_dir, '..', '..', 'textures'))]
    dirs.append(textures_dir(game_root))
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d); out.append(d)
    return out


def find_texture(name, game_root, model_dir=None):
    """Locate a texture file by basename: probe near-the-model dirs first (fast),
    then the full cached index (handles any folder layout). Returns abspath or None."""
    for d in _search_dirs(game_root, model_dir):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return _dds_index(game_root).get(name.lower())


_MAT_INDEX = {}


def _mat_index(game_root):
    idx = _MAT_INDEX.get(game_root)
    if idx is None:
        idx = {}
        for dp, _dirs, files in os.walk(game_root):
            for f in files:
                if f.lower().endswith('.mat00'):
                    idx.setdefault(f.lower(), os.path.join(dp, f))
        _MAT_INDEX[game_root] = idx
    return idx


def _mat_search_dirs(game_root, model_dir):
    dirs = []
    if model_dir:
        dirs += [os.path.normpath(os.path.join(model_dir, '..', 'materials')),
                 os.path.join(model_dir, 'materials'),
                 os.path.normpath(os.path.join(model_dir, '..', '..', 'materials'))]
    for md in mdl2obj.MAT_DIRS:
        dirs.append(os.path.join(game_root, md))
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d); out.append(d)
    return out


def find_mat00(name, game_root, model_dir=None):
    """Locate a '<name>.Mat00' (name may include or omit the extension). Probes
    near-the-model material dirs then the cached index. Returns abspath or None."""
    if not name.lower().endswith('.mat00'):
        name += '.Mat00'
    for d in _mat_search_dirs(game_root, model_dir):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return _mat_index(game_root).get(name.lower())


def sibling_materials(matname, game_root, model_dir=None):
    """Find related material stems (first/third-person + piece variants) sharing a
    weapon's core name. The game uses e.g. 'Melee_BaseballBat' (3rd person),
    'PV_Melee_BaseballBat' (1st-person view) and '..._Hilt' part materials. Given one
    piece's material, return the OTHER variant stems (no extension), excluding the
    given one and any already-skinned ('<name>-<skin>') materials."""
    core = re.sub(r'^PV_', '', matname)
    dirs = []
    own = find_mat00(matname, game_root, model_dir)
    if own:
        dirs.append(os.path.dirname(own))
    dirs += _mat_search_dirs(game_root, model_dir)
    seen, out = set(), []
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for nm in names:
            if not nm.lower().endswith('.mat00'):
                continue
            stem = nm[:-6]
            if stem == matname or '-' in stem or stem.lower() in seen:
                continue
            s2 = re.sub(r'^PV_', '', stem)
            if s2 == core or s2.startswith(core + '_'):
                seen.add(stem.lower()); out.append(stem)
    return out


def enumerate_skins(base, game_root, model_dir=None):
    """Skins are encoded in the diffuse texture filenames as '<base>[-<skin>]_D.dds'.
    Scan the near-the-model dirs (and, if a base diffuse exists, its own folder) for
    those. Returns ['', <skin>, ...], '' meaning the un-skinned base."""
    skins = set()
    dirs = list(_search_dirs(game_root, model_dir))
    # also include the folder the base diffuse actually lives in (via the index)
    base_diff = find_texture('%s_D.dds' % base, game_root, model_dir)
    if base_diff:
        dirs.insert(0, os.path.dirname(base_diff))
    for b in candidate_bases(base):
        pat = re.compile(re.escape(b) + r'(?:-([^_]+))?_D\.dds$', re.IGNORECASE)
        for d in dirs:
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for nm in names:
                m = pat.match(nm)
                if m:
                    skins.add(m.group(1) or '')
    return [''] + sorted(s for s in skins if s)


def resolve_roles(base, game_root, skin='', piece_suffix='', model_dir=None):
    """Resolve {role: abs_path} for a material/piece by the
    '<base>[_piece][-<skin>]_D.dds' filename convention, searching near the model and
    then the full texture index. A requested skin's filename WINS over the .Mat00
    default. Returns {} if nothing is found."""
    def by_name(names):
        for nm in names:
            p = find_texture(nm, game_root, model_dir)
            if p:
                return mdl2obj._discover_siblings(p)
        return None

    # 1) skin requested -> the skinned filename is authoritative
    if skin:
        roles = by_name([
            '%s%s-%s_D.dds' % (base, piece_suffix, skin),   # SN_AWM300_Scope-Gold_D
            '%s-%s%s_D.dds' % (base, skin, piece_suffix),   # SN_AWM300-Gold_Scope_D
            '%s-%s_D.dds' % (base, skin),                   # SN_AWM300-Gold_D
        ])
        if roles:
            return roles
    # 2) .Mat00 (only if its named diffuse actually exists on disk)
    info = mdl2obj.resolve_material(base, game_root, skin=skin or None, suffix=piece_suffix)
    if info and info.get('roles', {}).get('diffuse'):
        return info['roles']
    # 3) skin-less filename fallback
    return by_name(['%s%s_D.dds' % (base, piece_suffix), '%s_D.dds' % base]) or {}


def _resolve_base(model_name, game_root, skin, model_dir=None):
    """Try each candidate base; return (base, roles) for the first that yields a
    diffuse texture, else (model_name, {})."""
    for b in candidate_bases(model_name):
        roles = resolve_roles(b, game_root, skin=skin, model_dir=model_dir)
        if roles.get('diffuse'):
            return b, roles
        if enumerate_skins(b, game_root, model_dir) != ['']:
            return b, roles
    return model_name, {}


class TextureSet:
    """Holds the mutable per-role pixel buffers + resolved paths for one material.
    Pixel buffers are loaded lazily by textures.load_textureset()."""
    def __init__(self, name, roles):
        self.name = name
        self.roles = dict(roles)          # role -> abs .dds path (source)
        self.buffers = {}                 # role -> numpy (H,W,4) uint8 (mutable)
        self.original = {}                # role -> pristine copy (for ref-alpha/reset)
        self.gltex = {}                   # role -> moderngl.Texture (filled by scene)
        self.dirty = {}                   # role -> (x0,y0,x1,y1) pending GPU upload
        self.default_reldir = None        # game-relative textures dir (export fallback)

    def size(self, role='diffuse'):
        b = self.buffers.get(role)
        return (b.shape[1], b.shape[0]) if b is not None else (0, 0)


class LoadedModel:
    def __init__(self):
        self.path = ''
        self.name = ''
        self.base = ''
        self.skin = ''
        self.game_root = ''
        self.vertices = None      # (N, 14) f4: pos3 nrm3 uv2 tan3 bin3
        self.positions = None     # (N,3) f4 (transformed) - for picking
        self.faces = None         # (T,3) i4 over all groups (for picking)
        self.draw_groups = []     # [{name, faces (M,3) i4, texset}]
        self.bbox_min = None
        self.bbox_max = None
        self.center = None
        self.radius = 1.0
        self.skins = []


# (x, y, z) -> (-x, -z, y). det = -1 so it un-mirrors (LithTech is left-handed),
# AND maps LithTech up (+Y) to +Z for our Z-up viewer (the older (-x,z,-y) sent
# +Y -> -Z, which rendered the weapon upside-down). Barrel stays along Y.
def _swap(a):
    a = np.asarray(a, dtype='f4')
    out = np.empty_like(a)
    out[..., 0] = -a[..., 0]
    out[..., 1] = -a[..., 2]
    out[..., 2] = a[..., 1]
    return out


def load(model_path, game_root, skin=''):
    m = model00p.parse(model_path)
    lm = LoadedModel()
    lm.path = model_path
    lm.name = m.name
    lm.game_root = game_root
    lm.skin = skin or ''

    n = len(m.vertices)
    pos = np.array([v.position for v in m.vertices], dtype='f4').reshape(n, 3)
    nrm = np.array([v.normal for v in m.vertices], dtype='f4').reshape(n, 3)
    uv0 = np.array([v.uv0 for v in m.vertices], dtype='f4').reshape(n, 2)
    tan = np.array([v.tangent for v in m.vertices], dtype='f4').reshape(n, 3)
    binr = np.array([v.binormal for v in m.vertices], dtype='f4').reshape(n, 3)

    pos = _swap(pos)
    nrm = _swap(nrm)
    tan = _swap(tan)
    binr = _swap(binr)

    lm.positions = pos
    lm.vertices = np.concatenate([pos, nrm, uv0, tan, binr], axis=1).astype('f4')

    lm.bbox_min = pos.min(axis=0)
    lm.bbox_max = pos.max(axis=0)
    lm.center = (lm.bbox_min + lm.bbox_max) * 0.5
    lm.radius = float(np.linalg.norm(lm.bbox_max - lm.bbox_min) * 0.5) or 1.0

    # All triangles, in original index space.
    all_faces = []
    for p in m.pieces:
        all_faces.extend(p.faces)
    all_faces = np.array(all_faces, dtype='i4').reshape(-1, 3) if all_faces else np.zeros((0, 3), 'i4')
    lm.faces = all_faces

    model_dir = os.path.dirname(os.path.abspath(model_path))
    lm.base, body_roles = _resolve_base(m.name, game_root, skin, model_dir)
    lm.skins = enumerate_skins(lm.base, game_root, model_dir)

    # Material pieces (body / scope) via mdl2obj's vertex-range split; resolve each
    # piece's textures by filename convention (resolve_roles), bucketing triangles
    # by the triangle's max vertex index (the rule mdl2obj itself uses).
    groups = _build_groups(model_path, lm.base, game_root, skin, all_faces, body_roles, model_dir)
    lm.draw_groups = groups

    # Export-path hint: the game-relative textures dir derived from the MODEL's own
    # location (models live in .../view_models|world_models; textures in ../textures),
    # so a melee model exports under weapons/3_melee/textures even if a role's source
    # path can't be made relative to the active game root.
    reldir = _model_textures_reldir(model_path, game_root)
    for g in lm.draw_groups:
        g['texset'].default_reldir = reldir
    return lm


def _model_textures_reldir(model_path, game_root):
    import textures as texmod
    mdir = os.path.dirname(os.path.abspath(model_path))
    rel = None
    try:
        r = os.path.relpath(mdir, os.path.abspath(game_root))
        if not r.startswith('..'):
            rel = r.replace('\\', '/')
    except ValueError:
        pass
    if rel is None:
        rel = texmod.extract_game_subpath(mdir)      # works outside game root too
    if not rel:
        return 'weapons/1_main/textures'
    parent = rel.rsplit('/', 1)[0] if '/' in rel else ''   # drop view_models/world_models
    return (parent + '/textures').lstrip('/') if parent else 'textures'


def _build_groups(model_path, base, game_root, skin, all_faces, body_roles, model_dir):
    pieces = None
    try:
        d = open(model_path, 'rb').read()
        stride, voff = model00p.parse_vertex_decl(d)
        nrm_off = voff.get('NORMAL', 12)
        mesh_start = 0x5C + _pool_size(d)
        blocks = mdl2obj.find_mesh_blocks(d, mesh_start, stride, nrm_off)
        if blocks:
            pieces = mdl2obj.detect_pieces(d, blocks[0], base, game_root, skin or None)
    except Exception:
        pieces = None

    if not pieces or len(pieces) <= 1:
        return [{'name': base, 'faces': all_faces, 'texset': TextureSet(base, body_roles)}]

    tri_max = all_faces.max(axis=1) if all_faces.shape[0] else np.zeros((0,), 'i4')
    groups = []
    for p in pieces:
        lo, hi = p['lo'], p['hi']
        faces = all_faces[(tri_max >= lo) & (tri_max < hi)]
        if faces.shape[0] == 0:
            continue
        psuffix = '_Scope' if p['name'].endswith('_Scope') else ''
        roles = resolve_roles(base, game_root, skin=skin, piece_suffix=psuffix,
                              model_dir=model_dir) or body_roles
        groups.append({'name': p['name'], 'faces': faces, 'texset': TextureSet(p['name'], roles)})
    return groups or [{'name': base, 'faces': all_faces, 'texset': TextureSet(base, body_roles)}]


def _pool_size(d):
    import struct
    return struct.unpack_from('<21I', d, 8)[9]

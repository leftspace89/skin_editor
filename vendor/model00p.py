"""
model00p.py - Full parser for District 187 / S2 SonSilah .Model00p
(Lithtech Jupiter EX, MODL v33).

Reverse-engineered from TheRaw.exe (Model::Load @0x442469) and validated against
the sample weapon models. Covers: header, string pool, skeleton (nodes/bones with
bind pose + hierarchy), and mesh pieces (interleaved vertices incl. skinning).

On-disk section order (from the engine loader):
    [4] 'MODL'  [4] u32 version(0x21)
    21 x u32 header counts            (hdr[2]=node_count, hdr[9]=string_pool_bytes)
    string pool                       (NUL-terminated names, referenced by offset)
    NODES (recursive, depth-first)    <-- right after the pool
    <node weight-map blob>
    geometry pool (vertices + indices)
    PIECES (LOD descriptors + vertex declaration)
    weight sets / child models / sockets / animations / anim bindings  (file tail)

NODE (39 bytes, byte-packed):
    u16 name_offset, u8 flags, vec3 position, f32 unknown, vec4 quaternion(x,y,z,w),
    u32 child_count
Children follow immediately (depth-first); child_count gives the subtree fan-out.

VERTEX (72 bytes, declaration in file tail confirms the offsets):
    pos f3@0, normal f3@12, uv0 f2@24, uv1 f2@32, tangent f3@40, binormal f3@52,
    blendweight D3DCOLOR@64, blendindices D3DCOLOR@68
"""
import os
import struct

VSTRIDE = 72          # default/weapon layout; real stride comes from the decl
NODE_SIZE = 39

# D3DDECLTYPE -> byte size
_DECLTYPE_SIZE = {0: 4, 1: 8, 2: 12, 3: 16, 4: 4, 5: 4, 6: 4, 7: 8,
                  8: 4, 9: 8, 15: 4, 16: 8, 17: 0}
# D3DDECLUSAGE
_USAGE = {0: 'POSITION', 1: 'BLENDWEIGHT', 2: 'BLENDINDICES', 3: 'NORMAL',
          5: 'TEXCOORD', 6: 'TANGENT', 7: 'BINORMAL'}


_DECL_END = b'\xff\x00\x00\x00\x11\x00\x00\x00'   # stream=0xFF, off=0, type=0x11


def _decode_decl_at(d, end):
    """Decode the stream-0 D3DVERTEXELEMENT9 array that ends at marker `end`.
    Each element is 8 bytes: u16 stream, u16 offset, u8 type, u8 method,
    u8 usage, u8 usageIndex. Returns (stride, offsets, usages) or None."""
    elems = []
    q = end - 8
    while q >= 0:
        es, eoff, etyp, _meth, eusage, euidx = struct.unpack_from('<HHBBBB', d, q)
        if es != 0 or etyp not in _DECLTYPE_SIZE:
            break
        elems.append((eoff, etyp, eusage, euidx))
        q -= 8
    if len(elems) < 3:
        return None
    elems.reverse()
    offsets, usages, stride = {}, set(), 0
    for eoff, etyp, eusage, euidx in elems:
        name = _USAGE.get(eusage, 'U%d' % eusage)
        if eusage in (5, 6, 7):              # TEXCOORD/TANGENT/BINORMAL indexed
            name += str(euidx)
        offsets[name] = eoff
        usages.add(eusage)
        stride = max(stride, eoff + _DECLTYPE_SIZE[etyp])
    if stride < 16:
        return None
    return stride, offsets, usages


def _find_decl_end(d):
    """Offset of the REAL vertex-declaration END marker. Character/costume meshes
    carry a SECOND false marker in their trailing animation data, so scanning from
    the file end (rfind) picks the wrong one. Choose the first marker whose element
    array forms a true vertex layout (POSITION + NORMAL + TEXCOORD all present)."""
    markers, i = [], d.find(_DECL_END)
    while i >= 0:
        markers.append(i)
        i = d.find(_DECL_END, i + 1)
    fallback = None
    for end in markers:
        r = _decode_decl_at(d, end)
        if r and {0, 3, 5} <= r[2]:           # POSITION, NORMAL, TEXCOORD
            return end
        if r and fallback is None:
            fallback = end
    return fallback if fallback is not None else (markers[-1] if markers else -1)


def parse_vertex_decl(d):
    """Parse the D3DVERTEXELEMENT9 array. Returns (stride, offsets) where offsets
    maps 'POSITION'/'NORMAL'/'TEXCOORD0'/'BLENDWEIGHT'/'BLENDINDICES'/... to byte
    offsets. Falls back to the 72-byte weapon layout if no declaration is found."""
    end = _find_decl_end(d)
    if end >= 0:
        r = _decode_decl_at(d, end)
        if r and r[0] >= 16:
            return r[0], r[1]
    return VSTRIDE, {'POSITION': 0, 'NORMAL': 12, 'TEXCOORD0': 24, 'TEXCOORD1': 32,
                     'TANGENT0': 40, 'BINORMAL0': 52, 'BLENDWEIGHT': 64, 'BLENDINDICES': 68}


class Node:
    __slots__ = ('name', 'index', 'flags', 'position', 'unknown', 'rotation',
                 'child_count', 'parent', 'children')

    def __init__(self):
        self.parent = None
        self.children = []

    def __repr__(self):
        return 'Node(%r, children=%d)' % (self.name, self.child_count)


class Vertex:
    __slots__ = ('position', 'normal', 'uv0', 'uv1', 'tangent', 'binormal',
                 'bone_indices', 'bone_weights')


class Piece:
    __slots__ = ('name', 'vstart', 'vend', 'faces')

    def __init__(self):
        self.faces = []


class Model:
    def __init__(self):
        self.path = ''
        self.name = ''
        self.version = 0
        self.header = ()
        self.names = {}          # pool offset -> name
        self.nodes = []          # skeleton, depth-first order
        self.vertices = []       # shared vertex buffer
        self.pieces = []         # named index-range pieces
        self.sockets = []
        self.animations = []
        self.sections = []       # render-section ranges (the model's render 'objects')
        self.vstride = VSTRIDE
        self.voffsets = {}

    @property
    def node_count(self):
        return self.header[2]

    @property
    def pool_size(self):
        return self.header[9]


def _name(pool, off):
    e = pool.find(b'\x00', off)
    return pool[off:e if e >= 0 else len(pool)].decode('ascii', 'replace')


def _read_nodes(d, off, pool, count):
    """Read `count` nodes (depth-first) and wire up the hierarchy."""
    nodes = []
    for i in range(count):
        n = Node()
        # NODE (39 bytes): u32 name_offset @0, flags @4, vec3 pos @7, vec4 quat @19,
        # u32 child_count @35. (Name is a u32 pool offset, NOT u16 — verified against
        # FEAR Playerbase.Model00a absolute matrices: pos/quat reproduce them exactly.)
        n.name = _name(pool, struct.unpack_from('<I', d, off)[0])
        n.flags = d[off + 4]
        n.position = struct.unpack_from('<3f', d, off + 7)
        n.unknown = 0.0
        n.rotation = struct.unpack_from('<4f', d, off + 19)   # x,y,z,w
        n.child_count = struct.unpack_from('<I', d, off + 35)[0]
        n.index = i
        nodes.append(n)
        off += NODE_SIZE
    # rebuild tree from depth-first child counts
    _link_tree(nodes)
    return nodes, off


def _link_tree(nodes):
    it = iter(range(len(nodes)))

    def visit(parent):
        i = next(it)
        n = nodes[i]
        n.parent = parent
        if parent is not None:
            parent.children.append(n)
        for _ in range(n.child_count):
            visit(n)
    try:
        while True:
            visit(None)
    except StopIteration:
        pass


def _unit(x, y, z, tol=0.06):
    m = x * x + y * y + z * z
    return abs(m - 1.0) <= tol


def _find_mesh_blocks(d, start, stride=VSTRIDE, nrm_off=12):
    """Locate (vbuf_size, ibuf_size)-headed mesh blocks (byte-aligned scan)."""
    n = len(d)
    blocks = []
    p = start
    while p + 8 <= n:
        vsize, isize = struct.unpack_from('<II', d, p)
        if (vsize >= stride and vsize % stride == 0 and isize >= 6 and isize % 6 == 0
                and p + 8 + vsize + isize <= n):
            vcount, icount = vsize // stride, isize // 2
            vbuf, ibuf = p + 8, p + 8 + vsize
            nx, ny, nz = struct.unpack_from('<3f', d, vbuf + nrm_off)
            n2 = struct.unpack_from('<3f', d, vbuf + stride + nrm_off) if vcount > 1 else (nx, ny, nz)
            if _unit(nx, ny, nz) and _unit(*n2):
                idx = struct.unpack_from('<%dH' % icount, d, ibuf)
                if max(idx) < vcount:
                    blocks.append({'vbuf': vbuf, 'vcount': vcount, 'ibuf': ibuf, 'icount': icount})
                    p = ibuf + isize
                    continue
        p += 1
    return blocks


def parse_sections(d):
    """Parse the post-geometry RENDER-SECTION table. The skinned mesh is split
    into sections, each covering a contiguous VERTEX range and carrying its own
    BONE-SET (the per-section palette of node indices, <=24 for shader limits).
    A vertex's BLENDINDICES are slots INTO its section's bone-set, NOT direct node
    indices. Returns a per-vertex list of bone-sets (node-index lists), or None.

    Layout (right after the vertex declaration's END marker ff 00 00 00 11 00 00 00):
        u32 section_count
        per section: 9 u32 fields [_, vert_count, stride, _, _, face_count, _,
                     bone_count, _]  then  bone_count bytes (the bone-set)."""
    end = _find_decl_end(d)                                 # vertex-decl END marker
    if end < 0:
        return None
    o = end + 8
    if o + 4 > len(d):
        return None
    cnt = struct.unpack_from('<I', d, o)[0]
    o += 4
    if not (1 <= cnt <= 64):
        return None
    vsec = []
    for _ in range(cnt):
        if o + 36 > len(d):
            return None
        f = struct.unpack_from('<9I', d, o)
        o += 36
        vcount, bcount = f[1], f[7]
        if bcount > 255 or o + bcount > len(d):
            return None
        boneset = list(d[o:o + bcount])
        o += bcount
        vsec.extend([boneset] * vcount)
    return vsec


def parse_sockets(d, pool_off, pool, nodes):
    """Parse the SOCKET table (LithTech attach points). Each socket is a 40-byte
    record: u32 parent_node, u32 name_offset(pool-relative), vec3 pos, quat(wxyz),
    f32 scale. Sockets are named attach points (gun01/RightHand/PVAttach/...) the
    engine looks up via GetSocket; they're stored on the BASE SKELETON model
    (animations/characters/base.Model00p), NOT on mesh models. Returns a list of
    dicts {name, parent, parent_name, pos, quat, scale}, or [].

    The table is located by finding a run of records whose name_offset points to a
    valid pooled string and whose parent index is in range."""
    REC = 0x28
    n = len(d)

    def valid(q):
        # one socket record: parent in range, name_off -> a pooled string, scale~1,
        # non-degenerate quat (rejects all-zero anim-name false positives).
        if q + REC > n:
            return None
        parent, noff = struct.unpack_from('<II', d, q)
        if parent >= len(nodes) or not (0 < noff < len(pool)):
            return None
        if pool[noff - 1:noff] != b'\x00':            # must start a pooled string
            return None
        e = pool.find(b'\x00', noff)
        nm = pool[noff:e if e >= 0 else noff].decode('ascii', 'replace')
        if not nm or not (nm[0].isalpha() or nm[0] == '_'):
            return None
        f = struct.unpack_from('<8f', d, q + 8)
        if not (0.5 <= f[7] <= 2.0):                  # scale ~1
            return None
        if abs(f[3]) < 1e-6 and abs(f[4]) < 1e-6 and abs(f[5]) < 1e-6 and abs(f[6]) < 1e-6:
            return None                               # degenerate quat
        return nm

    best = None
    p = pool_off + len(pool)
    while p + REC <= n:
        if valid(p):
            cnt = 0
            q = p
            while valid(q):
                cnt += 1
                q += REC
            if cnt >= 4 and (best is None or cnt > best[1]):
                best = (p, cnt)
            p = q
        else:
            p += 1
    if not best:
        return []
    start, cnt = best
    out = []
    for i in range(cnt):
        o = start + i * REC
        parent, noff = struct.unpack_from('<II', d, o)
        f = struct.unpack_from('<8f', d, o + 8)
        e = pool.find(b'\x00', noff)
        out.append({'name': pool[noff:e].decode('ascii', 'replace'), 'parent': parent,
                    'parent_name': nodes[parent].name if parent < len(nodes) else None,
                    'pos': f[0:3], 'quat': f[3:7], 'scale': f[7]})
    return out


def parse_section_ranges(d):
    """Return the raw RENDER-SECTION records: list of dicts with vstart, vcount,
    istart (index, = field*... see below), fcount, matidx, boneset. These are the
    model's render 'objects' (one per original source object/material). Returns []
    if no section table."""
    end = _find_decl_end(d)
    if end < 0:
        return []
    o = end + 8
    if o + 4 > len(d):
        return []
    cnt = struct.unpack_from('<I', d, o)[0]
    o += 4
    if not (1 <= cnt <= 64):
        return []
    out = []
    for _ in range(cnt):
        if o + 36 > len(d):
            return []
        f = struct.unpack_from('<9I', d, o)
        o += 36
        bcount = f[7]
        if bcount > 255 or o + bcount > len(d):
            return []
        boneset = list(d[o:o + bcount])
        o += bcount
        out.append({'vstart': f[0], 'vcount': f[1], 'istart': f[3], 'fcount': f[5],
                    'matidx': f[6], 'boneset': boneset})
    return out


def _read_vertex(d, o, off):
    v = Vertex()
    def f3(k): return struct.unpack_from('<3f', d, o + off[k]) if k in off else (0.0, 0.0, 0.0)
    def f2(k): return struct.unpack_from('<2f', d, o + off[k]) if k in off else (0.0, 0.0)
    v.position = f3('POSITION')
    v.normal = f3('NORMAL')
    v.uv0 = f2('TEXCOORD0')
    v.uv1 = f2('TEXCOORD1')
    v.tangent = f3('TANGENT0')
    v.binormal = f3('BINORMAL0')
    bw, bi = off.get('BLENDWEIGHT'), off.get('BLENDINDICES')
    if bw is not None:
        w = d[o + bw:o + bw + 4]
        # BLENDWEIGHT is a D3DCOLOR: the GPU expands the BGRA bytes to an RGBA float4,
        # so weight component k = byte [2,1,0,3][k]. BLENDINDICES stays as raw bytes
        # (slots into the section bone-set). This pairs the dominant weight with the
        # varying index byte 0 -> e.g. magazine verts get full weight on the reload bone.
        v.bone_weights = (w[2], w[1], w[0], w[3])
    else:
        v.bone_weights = (255, 0, 0, 0)
    v.bone_indices = tuple(d[o + bi:o + bi + 4]) if bi is not None else (0, 0, 0, 0)
    return v


def _contiguous_pieces(idx, icount, vcount):
    """Split the shared index buffer into the model's contiguous vertex-range
    pieces (no triangle straddles a piece boundary)."""
    diff = [0] * (vcount + 2)
    for t in range(0, icount, 3):
        lo = min(idx[t], idx[t + 1], idx[t + 2])
        hi = max(idx[t], idx[t + 1], idx[t + 2])
        diff[lo + 1] += 1
        diff[hi + 1] -= 1
    cover, cuts = 0, [0]
    for k in range(1, vcount):
        cover += diff[k]
        if cover == 0:
            cuts.append(k)
    cuts.append(vcount)
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]


def parse(path):
    d = open(path, 'rb').read()
    m = Model()
    m.path = path
    m.name = os.path.splitext(os.path.basename(path))[0]
    if d[:4] != b'MODL':
        raise ValueError('not a MODL file: %r' % d[:4])
    m.version = struct.unpack_from('<I', d, 4)[0]
    m.header = struct.unpack_from('<21I', d, 8)
    pool_off = 0x5C
    pool = d[pool_off:pool_off + m.pool_size]
    o = 0
    while o < len(pool):
        e = pool.find(b'\x00', o)
        if e < 0:
            e = len(pool)
        if e > o:
            m.names[o] = pool[o:e].decode('ascii', 'replace')
        o = e + 1

    # skeleton, right after the pool
    m.nodes, _ = _read_nodes(d, pool_off + m.pool_size, pool, m.node_count)

    # vertex layout from the declaration in the file tail (stride varies: weapons
    # are 72B with 2 UV sets, character meshes 64B with one).
    m.vstride, m.voffsets = parse_vertex_decl(d)
    nrm_off = m.voffsets.get('NORMAL', 12)

    m.sections = parse_section_ranges(d)

    # mesh (one shared VB/IB), split into contiguous-range pieces
    blocks = _find_mesh_blocks(d, pool_off + m.pool_size, m.vstride, nrm_off)
    for b in blocks:
        m.vertices = [_read_vertex(d, b['vbuf'] + i * m.vstride, m.voffsets) for i in range(b['vcount'])]
        # Remap BLENDINDICES (palette slots) -> real node indices via the per-section
        # bone-sets. Without this, verts attach to the wrong bones (slot 0 != node 0).
        sections = parse_sections(d)
        if sections and len(sections) >= len(m.vertices):
            for vi, v in enumerate(m.vertices):
                bs = sections[vi]
                v.bone_indices = tuple(bs[s] if s < len(bs) else (bs[0] if bs else 0)
                                       for s in v.bone_indices)
        idx = struct.unpack_from('<%dH' % b['icount'], d, b['ibuf'])
        for (lo, hi) in _contiguous_pieces(idx, b['icount'], b['vcount']):
            p = Piece()
            p.vstart, p.vend = lo, hi
            for t in range(0, b['icount'], 3):
                tri = (idx[t], idx[t + 1], idx[t + 2])
                if lo <= max(tri) < hi:
                    p.faces.append(tri)
            m.pieces.append(p)
        break   # weapon models have a single shared block
    return m


def _print_tree(node, depth=0):
    print('  ' * depth + '%s  pos=(%.1f,%.1f,%.1f)' % (node.name, *node.position))
    for c in node.children:
        _print_tree(c, depth + 1)


if __name__ == '__main__':
    import sys
    paths = sys.argv[1:] or [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'S2Models', f)
        for f in ('SN_AWM300.Model00p', 'MAR_PDWK101.Model00p')]
    for p in paths:
        m = parse(p)
        print('=== %s  v%d  nodes=%d verts=%d pieces=%d ==='
              % (m.name, m.version, len(m.nodes), len(m.vertices), len(m.pieces)))
        roots = [n for n in m.nodes if n.parent is None]
        for r in roots:
            _print_tree(r)
        for pc in m.pieces:
            print('  piece verts[%d:%d]  %d tris' % (pc.vstart, pc.vend, len(pc.faces)))

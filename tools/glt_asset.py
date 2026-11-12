"""Reader/writer for Gluttony .glt_* asset container files (mirrors asset_registry_default compose_asset_file)."""
import struct

MAGIC = 0x544C4741
HDR = struct.Struct('<IHHI4xQI4xQQQQQQII Q')  # see asset/header.h
assert HDR.size == 0x60, HDR.size

def align8(x): return (x + 7) & ~7

def fnv1a64(data: bytes, h=0xcbf29ce484222325):
    for b in data:
        h ^= b; h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h

def payload_hash(chunks):
    h = 0xcbf29ce484222325
    for cid, data in chunks:
        h = fnv1a64(struct.pack('<I', cid), h)
        h = fnv1a64(data, h)
    return h

def read(path):
    b = open(path, 'rb').read()
    (magic, fv, mv, flags, uid, atype, hsh, name_off, src_off, ctab, dtab, stab, ccount, dcount, total) = HDR.unpack_from(b, 0)
    assert magic == MAGIC
    cstr = lambda o: b[o:b.index(b'\0', o)].decode()
    deps = []
    for i in range(dcount):
        did, poff, ttype, _ = struct.unpack_from('<QQII', b, dtab + 24 * i)
        deps.append((did, cstr(poff) if poff else '', ttype))
    chunks = []
    for i in range(ccount):
        cid, comp, off, sz, szd = struct.unpack_from('<IIQQQ', b, ctab + 32 * i)
        chunks.append((cid, b[off:off + sz]))
    return dict(id=uid, type=atype, hash=hsh, name=cstr(name_off), source=cstr(src_off), deps=deps, chunks=chunks, raw=b)

def compose(uid, atype, name, chunks, deps=(), source='', hsh=None):
    """deps: list of (uuid, virtual_path or '', target_type). chunks: list of (chunk_id, bytes)."""
    if hsh is None: hsh = payload_hash(chunks)
    strtab_off = align8(HDR.size)
    strtab = bytearray()
    def push(s):
        off = strtab_off + len(strtab); strtab.extend(s.encode() + b'\0'); return off
    name_off = push(name); src_off = push(source)
    dep_rows = []
    for did, vp, tt in deps:
        dep_rows.append(struct.pack('<QQII', did, push(vp) if vp else 0, tt, 0))
    deptab_off = align8(strtab_off + len(strtab))
    chunktab_off = align8(deptab_off + 24 * len(dep_rows))
    cursor = align8(chunktab_off + 32 * len(chunks))
    rows, offs = [], []
    for cid, data in chunks:
        cursor = align8(cursor); offs.append(cursor)
        rows.append(struct.pack('<IIQQQ', cid, 0, cursor, len(data), len(data)))
        cursor += len(data)
    total = align8(cursor)
    out = bytearray(total)
    out[0:HDR.size] = HDR.pack(MAGIC, 1, 1, 0, uid, atype, hsh, name_off, src_off, chunktab_off, deptab_off, strtab_off,
                               len(chunks), len(dep_rows), total)
    out[strtab_off:strtab_off + len(strtab)] = strtab
    for i, r in enumerate(dep_rows): out[deptab_off + 24 * i: deptab_off + 24 * (i + 1)] = r
    for i, r in enumerate(rows): out[chunktab_off + 32 * i: chunktab_off + 32 * (i + 1)] = r
    for (cid, data), o in zip(chunks, offs): out[o:o + len(data)] = data
    return bytes(out)

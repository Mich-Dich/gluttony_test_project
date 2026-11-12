#!/usr/bin/env python3
"""Re-parse every generated stress world / material and cross-check references. Usage: validate_stress_worlds.py [content_dir]"""
import sys, os, struct, glob, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import glt_asset as g, glt_codec as codec
root = proj = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / 'content')
existing={os.path.relpath(p,proj) for p in glob.glob(proj+'/**/*.glt_*',recursive=True)} | {os.path.relpath(p,root) for p in glob.glob(root+'/**/*.glt_*',recursive=True)}
bad=0
def err(*a):
    global bad; bad+=1; print('ERROR',*a)
for wp in sorted(glob.glob(root+'/world/stress/*.glt_world')):
    raw=open(wp,'rb').read(); w=g.read(wp); ch=dict(w['chunks'])
    if g.payload_hash(w['chunks'])!=w['hash']: err(wp,'hash')
    if g.compose(w['id'],w['type'],w['name'],w['chunks'],w['deps'],w['source'],w['hash'])!=raw: err(wp,'not canonical')
    rows=[struct.unpack_from('<Q8fB7x',ch[0x300],i*48) for i in range(len(ch[0x300])//48)]
    if len(rows)!=len(w['deps']): err(wp,'deps/regions mismatch')
    ids={}; parents=[]; cams=set(); n=0
    cell=struct.unpack('<3f',ch[0x304])[0]
    for row,dep in zip(rows,w['deps']):
        if row[0]!=dep[0] or dep[2]!=2: err(wp,'dep mismatch')
        rp=os.path.join(root,dep[1]); r=g.read(rp)
        if r['id']!=row[0]: err(rp,'region uuid != world row')
        if os.path.splitext(os.path.basename(rp))[0]!=r['name']: err(rp,'name')
        rc=dict(r['chunks']); b=struct.unpack('<8f',rc[0x200])
        if b[:3]!=row[1:4] or b[4:7]!=row[5:8]: err(rp,'bounds mismatch')
        if abs((b[4]-b[0])-cell)>1e-3: err(rp,'bounds != cell size')
        if 0x201 not in rc: continue
        assert struct.unpack_from('<II',rc[0x201])==(1,0)
        ents=codec.decode(rc[0x201][8:])
        local={e[0] for e in ents}
        for idx,gen,comps in ents:
            n+=1
            if idx in ids: err('dup entity id',idx)
            ids[idx]=rp
            for k,v in comps.items():
                if k.startswith('0x'): err('unknown comp',k)
            t=struct.unpack('<9f',comps['glt.transform'])
            if not all(math.isfinite(x) for x in t): err('nan transform')
            if 'glt.camera' in comps: cams.add(idx)
            if 'glt.mesh_renderer' in comps:
                v=comps['glt.mesh_renderer']; L=struct.unpack_from('<I',v)[0]; path=v[4:4+L].decode(); o=4+L
                if path not in existing: err('missing mesh',path)
                cnt=struct.unpack_from('<I',v,o)[0]; o+=4
                for _ in range(cnt):
                    L=struct.unpack_from('<I',v,o)[0]; mp=v[o+4:o+4+L].decode(); o+=4+L
                    if mp not in existing: err('missing material',mp)
                if len(v)!=o+19: err('mesh payload size',len(v),o+19)
            if 'glt.light' in comps and len(comps['glt.light'])!=32: err('light size')
            if 'glt.hierarchy' in comps:
                pi,pg=struct.unpack('<II',comps['glt.hierarchy'])
                if pi not in local: err('parent not in same region',idx,pi)
    cam=struct.unpack('<II',ch[0x302])
    if cam[0] not in cams: err(wp,'active camera missing')
    we=codec.decode(ch[0x307][4:])
    if we[0][0]!=0 or 0 in ids: err('world entity idx')
    print(f"OK {os.path.basename(wp):34s} regions={len(rows):3d} entities={n:6d} size={sum(os.path.getsize(os.path.join(root,d[1])) for d in w['deps'])/1e6:6.2f}MB")
for mp in sorted(glob.glob(root+'/material/stress/*.glt_material')):
    m=g.read(mp)
    if g.payload_hash(m['chunks'])!=m['hash']: err(mp,'hash')
mats=[g.read(p) for p in glob.glob(root+'/material/stress/*')]
print('materials',len(mats),'unique ids',len({m['id'] for m in mats}),'param sample',struct.unpack('<4f',dict(mats[0]['chunks'])[0x300]))
print('ERRORS',bad)

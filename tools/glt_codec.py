import struct
def h(name):
    x=0xcbf29ce484222325
    for c in name.encode(): x^=c; x=(x*0x100000001b3)&0xFFFFFFFFFFFFFFFF
    return x
NAMES=['glt.transform','glt.no_inherit_transform','glt.camera','glt.light','glt.sky','glt.ambient_light','glt.mesh_renderer','glt.audio_source','glt.name','glt.hierarchy']
BYH={h(n):n for n in NAMES}
def decode(blob):
    off=0; ver,n=struct.unpack_from('<II',blob,off); off+=8; ents=[]
    for _ in range(n):
        idx,gen,cc=struct.unpack_from('<III',blob,off); off+=12; comps={}
        for _ in range(cc):
            hh,sz=struct.unpack_from('<QI',blob,off); off+=12; comps[BYH.get(hh,hex(hh))]=blob[off:off+sz]; off+=sz
        ents.append((idx,gen,comps))
    assert off==len(blob); return ents

#!/usr/bin/env python3
"""
Generate stress-test worlds for the Gluttony test project.

Writes .glt_world / .glt_region files (and a set of material variants) in the exact on-disk format produced by
asset_registry_default::compose_asset_file + world_ecs_entt's world_asset_handler / entity_codec (CODEC_ENTT_V1).

Usage:
    python3 tools/gen_stress_worlds.py [--content <path to content dir>] [--only name1,name2] [--list]

Output (relative to content/):
    world/stress/<world>.glt_world
    world/stress/<world>_r_<x>_<y>_<z>.glt_region
    material/stress/color_<nn>.glt_material        (clones of material/simple_color with different params)

Everything is deterministic (seeded by world name), so re-running produces identical files and stable UUIDs.
If the engine's codec / component layouts change, update the "component encoders" section below.
"""
import argparse, math, os, random, struct, sys, zlib, colorsys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glt_asset as G  # noqa: E402

# ---- engine constants ------------------------------------------------------------------------------------------------
TYPE_WORLD, TYPE_REGION = 1, 2
CODEC_ENTT_V1 = 1
CHUNK_REGION_BOUNDS, CHUNK_REGION_ENTITIES = 0x0200, 0x0201
CHUNK_WORLD_REGIONS, CHUNK_WORLD_ACTIVE_CAMERA, CHUNK_WORLD_ACTIVE_CAMERA_POSITION = 0x0300, 0x0302, 0x0306
CHUNK_WORLD_REGION_CELL_SIZE, CHUNK_WORLD_STREAMING_RADIUS, CHUNK_WORLD_ENTITY = 0x0304, 0x0305, 0x0307
CHUNK_MATERIAL_PARAMS = 0x0300
LIGHT_DIRECTIONAL, LIGHT_POINT, LIGHT_SPOT = 0, 1, 2
MOTION_STATIC, MOTION_DYNAMIC = 0, 1

# Engine limits worth knowing when reading the worlds below (renderer_vk_rasteriser/src/scene/type.h, shared/type.h)
MAX_LIGHTS, MAX_LIGHTS_PER_CLUSTER, MAX_SHADOW_VIEWS, MAX_MATERIALS = 1024, 256, 48, 1024

# ---- assets available in the test project -----------------------------------------------------------------------------
M_BALL = 'static_mesh_assets/basic/ball.glt_static_mesh'          # r=1, ~320 tris, has collider
M_CUBE = 'static_mesh_assets/basic/cube.glt_static_mesh'          # 2x2x2, 12 tris, has collider
M_CYL = 'static_mesh_assets/basic/cylinder.glt_static_mesh'       # r=1 h=2, ~124 tris, has collider
M_TORUS = 'static_mesh_assets/basic/torus.glt_static_mesh'        # r=0.04 (!), 552 tris, no collider
M_PLANE = 'static_mesh_assets/basic/ubdic_plane.glt_static_mesh'  # 2x2 XZ, 7200 tris, no collider
M_SHIP = 'static_mesh_assets/basic/stealth_ship.glt_static_mesh'  # ~4.7m, ~272k tris, 31 submeshes, LODs, no collider
SINGLE_SUBMESH = {M_BALL, M_CUBE, M_CYL, M_TORUS, M_PLANE}
TORUS_UNIT = 25.0                                                 # scale that makes the torus ~1m across
MAT_TRANSLUCENT = 'material/translucent.glt_material'
MAT_BASE = 'material/simple_color.glt_material'
N_COLOR_MATERIALS = 128


def color_material(i):
    return f'material/stress/color_{i:03d}.glt_material'


# ---- helpers ----------------------------------------------------------------------------------------------------------
def stable_uuid(*parts):
    """Deterministic non-zero 64-bit UUID from a string key."""
    key = '/'.join(map(str, parts)).encode()
    h = G.fnv1a64(key)
    h ^= (zlib.crc32(key) << 17) & 0xFFFFFFFFFFFFFFFF
    return h or 1


def look_rotation(eye, target):
    """Euler (pitch, yaw, 0) so that -Z points from eye to target. Matches ecs_world: M = T * Ry * Rx * Rz * S."""
    d = [t - e for t, e in zip(target, eye)]
    n = math.sqrt(sum(c * c for c in d)) or 1.0
    d = [c / n for c in d]
    return (math.asin(max(-1.0, min(1.0, d[1]))), math.atan2(-d[0], -d[2]), 0.0)


def hsv(h, s=0.8, v=1.0):
    return colorsys.hsv_to_rgb(h % 1.0, s, v)


# ---- component encoders (must match ecs_world_plugin::on_load registration) -------------------------------------------
H = {n: G.fnv1a64(n.encode()) for n in ['glt.transform', 'glt.camera', 'glt.light', 'glt.sky', 'glt.ambient_light',
                                         'glt.mesh_renderer', 'glt.name', 'glt.hierarchy']}


def c_transform(pos=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    if isinstance(scale, (int, float)):
        scale = (scale,) * 3
    return 'glt.transform', struct.pack('<9f', *pos, *rot, *scale)


def c_name(s):
    b = s.encode()
    return 'glt.name', struct.pack('<I', len(b)) + b


def c_camera(fov=60.0, near=0.1, far=2000.0):
    return 'glt.camera', struct.pack('<3f', fov, near, far)


def c_light(kind, color=(1, 1, 1), intensity=1.0, radius=10.0, inner=0.3, outer=0.5):
    return 'glt.light', struct.pack('<7fB3x', *color, intensity, radius, inner, outer, kind)


def c_sky(intensity=0.72, haze=0.0, ground_albedo=0.3, sun_disk=0.63):
    return 'glt.sky', struct.pack('<4f', intensity, haze, ground_albedo, sun_disk)


def c_ambient(intensity=0.4, sky=(0.04, 0.045, 0.055), ground=(0.02, 0.018, 0.016)):
    return 'glt.ambient_light', struct.pack('<7f', intensity, *sky, *ground)


def c_hierarchy(parent_index, parent_gen=1):
    return 'glt.hierarchy', struct.pack('<II', parent_index, parent_gen)


def asset_path(p):
    b = p.encode() if p else b''
    return struct.pack('<I', len(b)) + b


def c_mesh(mesh, material=None, motion=MOTION_STATIC, visible=True, friction=0.5, restitution=0.0, density=1000.0):
    overrides = b''
    if material is not None:
        assert mesh in SINGLE_SUBMESH, 'override count must equal submesh count; only single-submesh meshes supported'
        overrides = struct.pack('<I', 1) + asset_path(material)
    else:
        overrides = struct.pack('<I', 0)
    data = (asset_path(mesh) + overrides + bytes([1 if visible else 0, motion])
            + struct.pack('<fffI', friction, restitution, density, 0) + b'\x01')
    return 'glt.mesh_renderer', data


def encode_entities(entities):
    """entities: list of (index, generation, [(component_name, bytes), ...]) -> CODEC_ENTT_V1 blob"""
    out = bytearray(struct.pack('<II', CODEC_ENTT_V1, len(entities)))
    for idx, gen, comps in entities:
        out += struct.pack('<III', idx, gen, len(comps))
        for name, data in comps:
            out += struct.pack('<QI', H[name], len(data)) + data
    return bytes(out)


# ---- world builder ----------------------------------------------------------------------------------------------------
class World:
    def __init__(self, name, description, cell=256.0, streaming_radius=1024.0, always_loaded=True, seed=None):
        self.name, self.description = name, description
        self.cell, self.streaming_radius, self.always_loaded = cell, streaming_radius, always_loaded
        self.rng = random.Random(seed if seed is not None else name)
        self.next_index = 1                     # 0 is the world entity
        self.regions = {}                       # cell -> [entities]
        self.camera = None
        self.environment = environment_day()
        self.stats = dict(entities=0, meshes=0, lights=0, point=0, spot=0, translucent=0, dynamic=0, ships=0)
        self.forced_cells = set()
        self.always_loaded_cells = set()        # per-region override (e.g. keep the camera's region resident)

    def cell_of(self, pos):
        return tuple(int(math.floor(p / self.cell + 0.5)) for p in pos)

    def add(self, pos, *comps, name=None, parent=None, cell=None):
        """Adds an entity. Returns its index (generation is always 1). Children go into the parent's region."""
        idx = self.next_index
        self.next_index += 1
        comps = [c_transform(pos) if c is None else c for c in comps]
        if not any(c[0] == 'glt.transform' for c in comps):
            comps.insert(0, c_transform(pos))
        if name:
            comps.append(c_name(name))
        if parent is not None:
            comps.append(c_hierarchy(parent[0]))
            cell = parent[1]
        if cell is None:
            cell = self.cell_of(pos)
        self.regions.setdefault(cell, []).append((idx, 1, comps))
        st = self.stats
        st['entities'] += 1
        for cname, data in comps:
            if cname == 'glt.mesh_renderer':
                st['meshes'] += 1
                if MAT_TRANSLUCENT.encode() in data: st['translucent'] += 1
                if M_SHIP.encode() in data: st['ships'] += 1
                if data[-18] == MOTION_DYNAMIC: st['dynamic'] += 1
            if cname == 'glt.light':
                st['lights'] += 1
                kind = data[28]
                st['point'] += kind == LIGHT_POINT
                st['spot'] += kind == LIGHT_SPOT
        return (idx, cell)

    # convenience ------------------------------------------------------------------------------------------------------
    def mesh(self, mesh, pos, rot=(0, 0, 0), scale=1.0, material=None, motion=MOTION_STATIC, name=None, parent=None, **kw):
        return self.add(pos, c_transform(pos, rot, scale), c_mesh(mesh, material, motion, **kw), name=name, parent=parent)

    def point(self, pos, color, intensity, radius, name=None, parent=None):
        return self.add(pos, c_transform(pos), c_light(LIGHT_POINT, color, intensity, radius), name=name, parent=parent)

    def spot(self, pos, target, color, intensity, radius, inner=0.25, outer=0.45, name=None, parent=None):
        return self.add(pos, c_transform(pos, look_rotation(pos, target)),
                        c_light(LIGHT_SPOT, color, intensity, radius, inner, outer), name=name, parent=parent)

    def set_camera(self, eye, target, fov=60.0, far=2000.0):
        self.camera = self.add(eye, c_transform(eye, look_rotation(eye, target)), c_camera(fov, 0.1, far), name='camera')
        self.camera_pos = eye

    # serialization ----------------------------------------------------------------------------------------------------
    def write(self, content_dir):
        assert self.camera, f'{self.name}: no camera'
        out_dir = Path(content_dir) / 'world' / 'stress'
        out_dir.mkdir(parents=True, exist_ok=True)
        world_vpath = f'world/stress/{self.name}.glt_world'

        region_rows, deps = [], []
        for cell in sorted(set(self.regions) | self.forced_cells):
            ents = self.regions.get(cell, [])
            rname = f'{self.name}_r_{cell[0]}_{cell[1]}_{cell[2]}'
            rvpath = f'world/stress/{rname}.glt_region'
            ruuid = stable_uuid('region', self.name, *cell)
            half = self.cell / 2
            mn = [c * self.cell - half for c in cell]
            mx = [c * self.cell + half for c in cell]
            bounds = struct.pack('<3f4x3f4x', *mn, *mx)
            chunks = [(CHUNK_REGION_BOUNDS, bounds)]
            if ents:
                chunks.append((CHUNK_REGION_ENTITIES, struct.pack('<II', CODEC_ENTT_V1, 0) + encode_entities(ents)))
            (out_dir / f'{rname}.glt_region').write_bytes(G.compose(ruuid, TYPE_REGION, rname, chunks))
            pinned = self.always_loaded or cell in self.always_loaded_cells
            region_rows.append(struct.pack('<Q', ruuid) + bounds + bytes([1 if pinned else 0]) + b'\0' * 7)
            deps.append((ruuid, rvpath, TYPE_REGION))

        world_entity = encode_entities([(0, 1, self.environment)])
        chunks = [
            (CHUNK_WORLD_REGIONS, b''.join(region_rows)),
            (CHUNK_WORLD_ENTITY, struct.pack('<I', CODEC_ENTT_V1) + world_entity),
            (CHUNK_WORLD_ACTIVE_CAMERA, struct.pack('<II', self.camera[0], 1)),
            (CHUNK_WORLD_ACTIVE_CAMERA_POSITION, struct.pack('<3f', *self.camera_pos)),
            (CHUNK_WORLD_REGION_CELL_SIZE, struct.pack('<3f', self.cell, self.cell, self.cell)),
            (CHUNK_WORLD_STREAMING_RADIUS, struct.pack('<f', self.streaming_radius)),
        ]
        (out_dir / f'{self.name}.glt_world').write_bytes(
            G.compose(stable_uuid('world', self.name), TYPE_WORLD, self.name, chunks, deps))
        self.stats['regions'] = len(region_rows)
        return world_vpath


# ---- environments (world entity: transform + sun + sky + ambient) -----------------------------------------------------
def environment(sun_pitch_deg, sun_yaw_deg, sun_intensity, sun_color=(1, 1, 1), sky=0.72, ambient=0.4, haze=0.0):
    rot = (math.radians(sun_pitch_deg), math.radians(sun_yaw_deg), 0.0)
    return [c_transform((0, 0, 0), rot), c_light(LIGHT_DIRECTIONAL, sun_color, sun_intensity, 10.0, 0.1, 0.3),
            c_sky(sky, haze), c_ambient(ambient)]


def environment_day():   return environment(-52.7, 88.8, 0.82)
def environment_dusk():  return environment(-8.0, 120.0, 0.25, (1.0, 0.55, 0.3), sky=0.35, ambient=0.15, haze=1.5)
def environment_night(): return environment(-35.0, 40.0, 0.02, (0.6, 0.7, 1.0), sky=0.03, ambient=0.05)


# ---- shared scene pieces ----------------------------------------------------------------------------------------------
def floor(w, size, y=-1.0, material=None, center=(0, 0)):
    """Single cube slab (top surface at y=0 when y=-1)."""
    return w.mesh(M_CUBE, (center[0], y, center[1]), scale=(size / 2, 1.0, size / 2), material=material, name='floor')


def pillar_grid(w, extent, spacing, height=4.0, radius=0.4, material=None):
    n = 0
    k = int(extent // spacing)
    for i in range(-k, k + 1):
        for j in range(-k, k + 1):
            w.mesh(M_CYL, (i * spacing, height / 2, j * spacing), scale=(radius, height / 2, radius), material=material,
                   name=f'pillar_{i}_{j}')
            n += 1
    return n


# ======================================================================================================================
# World definitions
# ======================================================================================================================
WORLDS = {}


def world(fn):
    WORLDS[fn.__name__] = fn
    return fn


@world
def baseline_empty():
    w = World('baseline_empty', 'Floor + camera + sun only. Measures fixed per-frame overhead (sky, shadows, post, UI).')
    floor(w, 100)
    w.set_camera((12, 8, 12), (0, 0, 0))
    return w


def _instancing(name, n_side, spacing, scale):
    w = World(name, f'{n_side * n_side:,} identical cubes (same mesh + same material). Tests the instancing / '
                    'batching path, per-instance upload, frustum culling and per-entity CPU cost (ECS iteration, '
                    'transform walk). Cubes carry a collider, so this also creates one static Jolt body each.')
    half = (n_side - 1) * spacing / 2
    floor(w, n_side * spacing + 10)
    for i in range(n_side):
        for j in range(n_side):
            x, z = i * spacing - half, j * spacing - half
            h = 0.5 + 0.45 * (math.sin(x * 0.15) + math.cos(z * 0.13))   # rolling wave so depth/occlusion vary
            w.mesh(M_CUBE, (x, h, z), scale=(scale, max(0.1, h), scale), name=f'cube_{i}_{j}')
    eye = (half * 0.9, max(15.0, half * 0.55), half * 0.9)
    w.set_camera(eye, (0, 0, 0))
    return w


@world
def instancing_1k():  return _instancing('instancing_1k', 32, 2.0, 0.7)
@world
def instancing_10k(): return _instancing('instancing_10k', 100, 2.0, 0.7)
@world
def instancing_50k(): return _instancing('instancing_50k', 224, 1.1, 0.4)


def _lights(name, count, extent, desc_extra=''):
    w = World(name, f'{count} dynamic lights (~80% point, ~20% spot, random hues, radius 5-12m) over a {2 * extent}m '
                    f'floor with a pillar grid, at night. Tests light culling / clustered shading and light upload. '
                    f'MAX_LIGHTS = {MAX_LIGHTS}.{desc_extra}')
    w.environment = environment_night()
    floor(w, extent * 2 + 10)
    pillar_grid(w, extent, 6.0)
    r = w.rng
    for i in range(count):
        p = (r.uniform(-extent, extent), r.uniform(0.6, 5.0), r.uniform(-extent, extent))
        c = hsv(r.random(), 0.75)
        if r.random() < 0.2:
            w.spot(p, (p[0] + r.uniform(-3, 3), 0, p[2] + r.uniform(-3, 3)), c, 4.0, r.uniform(8, 14), name=f'spot_{i}')
        else:
            w.point(p, c, 2.5, r.uniform(5, 12), name=f'point_{i}')
    w.set_camera((extent * 0.8, extent * 0.45 + 6, extent * 0.8), (0, 0, 0))
    return w


@world
def lights_64():   return _lights('lights_64', 64, 24)
@world
def lights_256():  return _lights('lights_256', 256, 48)
@world
def lights_1024(): return _lights('lights_1024', 1024, 96, ' Exactly at the light cap.')
@world
def lights_over_cap_1500():
    return _lights('lights_over_cap_1500', 1500, 96, ' 476 OVER the cap: the renderer must drop lights gracefully '
                                                    '(no crash / buffer overrun / validation error).')


@world
def lights_cluster_overflow():
    w = World('lights_cluster_overflow', f'512 point lights with 40m radius packed into a 20m box, so EVERY cluster '
                                         f'is touched by ~512 lights (MAX_LIGHTS_PER_CLUSTER = {MAX_LIGHTS_PER_CLUSTER}). '
                                         'Tests per-cluster list clamping and the global index-list capacity. Expect '
                                         'visible clamping, not corruption.')
    w.environment = environment_night()
    floor(w, 80)
    pillar_grid(w, 18, 6.0)
    r = w.rng
    for i in range(512):
        w.point((r.uniform(-10, 10), r.uniform(0.5, 6), r.uniform(-10, 10)), hsv(r.random(), 0.6), 0.15, 40.0,
                name=f'point_{i}')
    w.set_camera((22, 14, 22), (0, 1, 0))
    return w


@world
def shadows_budget():
    w = World('shadows_budget', f'16 point + 32 spot lights, all close to the camera, around occluders. Needs '
                                f'16*6 + 32 = 128 shadow views vs MAX_SHADOW_VIEWS = {MAX_SHADOW_VIEWS}. Tests shadow '
                                'candidate ranking, atlas packing / tile downsizing, and shadow-cache invalidation. '
                                'Orbit the camera and watch for shadows popping or flickering between lights.')
    w.environment = environment_night()
    floor(w, 80)
    r = w.rng
    for i in range(60):   # occluders
        p = (r.uniform(-18, 18), 0, r.uniform(-18, 18))
        kind = r.choice([M_CUBE, M_CYL, M_BALL])
        s = r.uniform(0.4, 1.2)
        w.mesh(kind, (p[0], s, p[2]), rot=(0, r.uniform(0, math.pi), 0), scale=s, name=f'occluder_{i}')
    for i in range(16):
        a = i / 16 * 2 * math.pi
        w.point((math.cos(a) * 10, 2.5, math.sin(a) * 10), hsv(i / 16, 0.4), 3.0, 14.0, name=f'shadow_point_{i}')
    for i in range(32):
        a = i / 32 * 2 * math.pi
        p = (math.cos(a) * 17, 7.0, math.sin(a) * 17)
        w.spot(p, (math.cos(a) * 4, 0, math.sin(a) * 4), hsv(i / 32 + 0.5, 0.3), 6.0, 25.0, 0.2, 0.4, name=f'shadow_spot_{i}')
    w.set_camera((0, 16, 28), (0, 0, 0))
    return w


@world
def material_variety():
    w = World('material_variety', f'4,096 objects with a random mix of 5 meshes x {N_COLOR_MATERIALS} material variants '
                                  '(+ ~5% translucent), placed in random order. Worst case for state sorting, material '
                                  f'descriptor binding and draw batching. MAX_MATERIALS = {MAX_MATERIALS}.')
    floor(w, 140)
    r = w.rng
    meshes = [(M_CUBE, 0.6), (M_BALL, 0.6), (M_CYL, 0.6), (M_TORUS, 0.8 * TORUS_UNIT), (M_PLANE, 0.7)]
    n = 64
    for i in range(n):
        for j in range(n):
            mesh, s = r.choice(meshes)
            mat = MAT_TRANSLUCENT if r.random() < 0.05 else color_material(r.randrange(N_COLOR_MATERIALS))
            pos = ((i - n / 2) * 2.0, 1.0 + r.uniform(0, 0.5), (j - n / 2) * 2.0)
            rot = (r.uniform(-0.5, 0.5), r.uniform(0, math.pi * 2), 0) if mesh != M_PLANE else (math.pi / 2, r.uniform(0, 6.28), 0)
            w.mesh(mesh, pos, rot=rot, scale=s, material=mat, name=f'obj_{i}_{j}')
    w.set_camera((50, 30, 50), (0, 0, 0))
    return w


@world
def heavy_geometry_ships():
    w = World('heavy_geometry_ships', '100 stealth ships (~272k tris and 31 submeshes each, ~27M tris at LOD0) in a '
                                      'formation that recedes to ~600m. Tests vertex/index throughput, LOD selection, '
                                      'per-submesh draw count (3,100 submesh draws) and shadow-pass cost for heavy casters.')
    floor(w, 900, center=(0, -300))
    r = w.rng
    for i in range(100):
        row, col = divmod(i, 10)
        x = (col - 4.5) * 9.0 + r.uniform(-1, 1)
        z = -row * 60.0 - r.uniform(0, 10)
        y = 4.0 + r.uniform(0, 6) + row * 1.5
        w.mesh(M_SHIP, (x, y, z), rot=(r.uniform(-0.1, 0.1), math.pi / 2 + r.uniform(-0.2, 0.2), r.uniform(-0.2, 0.2)),
               scale=1.0, name=f'ship_{i}')
    w.set_camera((0, 14, 22), (0, 4, -60))
    return w


@world
def translucency_overdraw():
    w = World('translucency_overdraw', '3,000 translucent cubes / balls / planes packed into a 30m volume in front of '
                                       '~500 opaque objects. Tests back-to-front sorting cost and stability (sort '
                                       'popping), blending overdraw and the translucent pass in general.')
    floor(w, 80)
    r = w.rng
    for i in range(500):
        w.mesh(r.choice([M_CUBE, M_CYL, M_BALL]), (r.uniform(-25, 25), 0.5, r.uniform(-25, 25)),
               rot=(0, r.uniform(0, 6.28), 0), scale=r.uniform(0.3, 0.8),
               material=color_material(r.randrange(N_COLOR_MATERIALS)), name=f'opaque_{i}')
    for i in range(3000):
        mesh = r.choice([M_CUBE, M_BALL, M_PLANE])
        rot = (r.uniform(0, 6.28), r.uniform(0, 6.28), r.uniform(0, 6.28))
        w.mesh(mesh, (r.uniform(-15, 15), r.uniform(0.5, 20), r.uniform(-15, 15)), rot=rot,
               scale=r.uniform(0.3, 1.5), material=MAT_TRANSLUCENT, name=f'glass_{i}')
    w.set_camera((0, 10, 32), (0, 8, 0))
    return w


@world
def physics_pile():
    w = World('physics_pile', '1,500 dynamic bodies (balls / cubes / cylinders) dropped from a grid into a walled pit. '
                              'Tests Jolt broadphase, contact solving under heavy stacking, physics->transform '
                              'write-back and render of 1.5k moving instances. Only active in play / simulate mode.')
    floor(w, 60)
    for k, (x, z, sx, sz) in enumerate([(0, 15, 15, 0.5), (0, -15, 15, 0.5), (15, 0, 0.5, 15), (-15, 0, 0.5, 15)]):
        w.mesh(M_CUBE, (x, 3, z), scale=(sx, 3, sz), name=f'wall_{k}')
    for k in range(12):   # static obstacles inside the pit
        a = k / 12 * 2 * math.pi
        w.mesh(M_CYL, (math.cos(a) * 7, 1.5, math.sin(a) * 7), scale=(0.6, 1.5, 0.6), name=f'post_{k}')
    r = w.rng
    i = 0
    for layer in range(15):
        for gx in range(10):
            for gz in range(10):
                mesh = (M_BALL, M_CUBE, M_CYL)[i % 3]
                pos = (gx * 2.4 - 10.8 + r.uniform(-0.3, 0.3), 8 + layer * 2.4, gz * 2.4 - 10.8 + r.uniform(-0.3, 0.3))
                w.mesh(mesh, pos, rot=(r.uniform(0, 6), r.uniform(0, 6), r.uniform(0, 6)), scale=0.5,
                       material=color_material(i % N_COLOR_MATERIALS), motion=MOTION_DYNAMIC, restitution=0.2,
                       name=f'body_{i}')
                i += 1
    w.set_camera((26, 22, 26), (0, 6, 0))
    return w


@world
def hierarchy_deep():
    w = World('hierarchy_deep', '96 spiral arms, each a parent->child chain 32 deep (each link offset + rotated '
                                'relative to its parent), plus one 256-deep tower. 3,328 linked entities. Tests '
                                'hierarchical transform evaluation cost (world transform walks the parent chain) and '
                                'correctness: if composition order is wrong the spirals visibly break.')
    floor(w, 120)
    for arm in range(96):
        a = arm / 96 * 2 * math.pi
        start = (math.cos(a) * 6, 1.0 + (arm % 8) * 0.5, math.sin(a) * 6)
        parent = w.add(start, c_transform(start, (0, -a, 0), 1.0), c_mesh(M_CUBE, color_material(arm % N_COLOR_MATERIALS)),
                       name=f'arm_{arm}_0')
        for d in range(1, 32):
            # local transform relative to parent: step forward and turn a bit; scale shrinks slowly
            parent = w.add((2.2, 0.15, 0), c_transform((2.2, 0.15, 0), (0, 0.18, 0.02), 0.97),
                           c_mesh(M_CUBE, color_material((arm + d) % N_COLOR_MATERIALS)), name=f'arm_{arm}_{d}',
                           parent=parent)
    parent = w.add((0, 0.5, 0), c_transform((0, 0.5, 0), (0, 0, 0), 1.0), c_mesh(M_CYL), name='tower_0')
    for d in range(1, 256):
        parent = w.add((0, 1.0, 0), c_transform((0, 1.0, 0), (0, 0.05, 0), 1.0),
                       c_mesh(M_CUBE, color_material(d % N_COLOR_MATERIALS)), name=f'tower_{d}', parent=parent)
    w.set_camera((45, 45, 45), (0, 5, 0))
    return w


@world
def streaming_grid():
    cell = 64.0
    w = World('streaming_grid', '8 x 8 regions of 64m (512m x 512m), streaming radius 48m and regions NOT always-loaded, '
                                'so only the regions near the camera are resident. ~420 objects + 4 lights per region. '
                                'Fly across the map to test region activate/deactivate hitches, entity slot reuse and '
                                'physics body churn.', cell=cell, streaming_radius=48.0, always_loaded=False)
    r = w.rng
    for cx in range(-4, 4):
        for cz in range(-4, 4):
            ox, oz = cx * cell, cz * cell
            mat = color_material(((cx + 4) * 8 + cz + 4) * 2 % N_COLOR_MATERIALS)
            w.mesh(M_CUBE, (ox, -1, oz), scale=(cell / 2, 1, cell / 2), material=mat, name=f'tile_{cx}_{cz}')
            for i in range(420):
                p = (ox + r.uniform(-cell / 2 + 1, cell / 2 - 1), 0, oz + r.uniform(-cell / 2 + 1, cell / 2 - 1))
                mesh = r.choice([M_CUBE, M_CYL, M_BALL, M_TORUS])
                s = r.uniform(0.3, 1.2) * (TORUS_UNIT if mesh == M_TORUS else 1)
                w.mesh(mesh, (p[0], (0.3 if mesh == M_TORUS else s), p[2]), rot=(0, r.uniform(0, 6.28), 0), scale=s,
                       material=color_material(r.randrange(N_COLOR_MATERIALS)), name=f'r{cx}_{cz}_obj_{i}')
            for i in range(4):
                w.point((ox + r.uniform(-20, 20), 3, oz + r.uniform(-20, 20)), hsv(r.random(), 0.5), 2.0, 14.0,
                        name=f'r{cx}_{cz}_light_{i}')
    w.set_camera((0.5, 18, 30.5), (0.5, 0, 0.5))
    w.always_loaded_cells.add(w.camera[1])      # don't let the active camera entity stream out with its region
    return w


@world
def complex_city():
    w = World('complex_city', 'A mixed "real" scene spanning 3 x 3 regions of 256m (~540m across): city blocks of stacked buildings '
                              'with translucent glass facades, rooftop tanks, street lamps (pole + child point light), '
                              'trees (trunk + child canopy), parked / hovering stealth ships, torus sculptures with spot '
                              'lights, and a high-poly ground. Everything at once: many meshes and materials, ~900 '
                              'lights, translucency, heavy meshes, hierarchies, dusk lighting.', cell=256.0)
    w.environment = environment_dusk()
    r = w.rng
    # high-poly ground: 4 x 4 tiles of the subdivided plane (each 7.2k tris), one per 128m
    for i in range(4):
        for j in range(4):
            x, z = (i - 1.5) * 128, (j - 1.5) * 128
            w.mesh(M_PLANE, (x, 0, z), scale=64.0, material=color_material(80), name=f'ground_{i}_{j}')
    block, street = 40.0, 14.0
    pitch = block + street
    n = 10                                          # 10 x 10 blocks ~ 540m
    lamps = 0
    for bi in range(n):
        for bj in range(n):
            cx, cz = (bi - (n - 1) / 2) * pitch, (bj - (n - 1) / 2) * pitch
            kind = r.random()
            if kind < 0.12:                         # park block: trees + sculpture
                for t in range(18):
                    p = (cx + r.uniform(-17, 17), 0, cz + r.uniform(-17, 17))
                    h = r.uniform(2.5, 5)
                    trunk = w.mesh(M_CYL, (p[0], h / 2, p[2]), scale=(0.25, h / 2, 0.25), material=color_material(10),
                                   name=f'tree_{bi}_{bj}_{t}')
                    # child canopy: local offset is in the trunk's scaled space -> compensate
                    w.mesh(M_BALL, (0, 1.0 + 1.6 / (h / 2), 0), scale=(6.0, 1.6 / (h / 2) * 0.9, 6.0),
                           material=color_material(40 + t % 8), name=f'canopy_{bi}_{bj}_{t}', parent=trunk)
                w.mesh(M_TORUS, (cx, 4, cz), rot=(math.pi / 2, r.uniform(0, 6), 0), scale=6 * TORUS_UNIT,
                       material=color_material(r.randrange(N_COLOR_MATERIALS)), name=f'sculpture_{bi}_{bj}')
                for s in range(4):
                    a = s * math.pi / 2
                    w.spot((cx + math.cos(a) * 12, 0.5, cz + math.sin(a) * 12), (cx, 5, cz), (1, 0.85, 0.6), 8.0, 20.0,
                           name=f'sculpture_spot_{bi}_{bj}_{s}')
                continue
            if kind < 0.2:                          # landing pad with ships
                w.mesh(M_CYL, (cx, 0.15, cz), scale=(17, 0.15, 17), material=color_material(5), name=f'pad_{bi}_{bj}')
                for s in range(r.randint(2, 4)):
                    w.mesh(M_SHIP, (cx + r.uniform(-9, 9), 0.3 + r.choice([0, 0, 6, 14]), cz + r.uniform(-9, 9)),
                           rot=(0, r.uniform(0, 6.28), 0), scale=2.0, name=f'ship_{bi}_{bj}_{s}')
                continue
            # building block: 2 x 2 lots
            for li in range(2):
                for lj in range(2):
                    lx, lz = cx + (li - 0.5) * 19, cz + (lj - 0.5) * 19
                    floors = r.randint(2, 12) if abs(bi - n / 2) + abs(bj - n / 2) > 3 else r.randint(8, 30)
                    w_, d_ = r.uniform(6, 8.5), r.uniform(6, 8.5)
                    mat = color_material(r.randrange(N_COLOR_MATERIALS))
                    y = 0.0
                    for f in range(floors):          # one mesh per storey -> lots of entities
                        fh = 3.2
                        shrink = 1.0 - 0.015 * f
                        w.mesh(M_CUBE, (lx, y + fh / 2, lz), scale=(w_ * shrink, fh / 2 - 0.05, d_ * shrink), material=mat,
                               name=f'bld_{bi}_{bj}_{li}{lj}_f{f}')
                        if f % 3 == 1:              # glass band on some floors
                            w.mesh(M_CUBE, (lx, y + fh / 2, lz), scale=(w_ * shrink + 0.15, fh / 2 - 0.6, d_ * shrink + 0.15),
                                   material=MAT_TRANSLUCENT, name=f'glass_{bi}_{bj}_{li}{lj}_f{f}')
                        y += fh
                    if r.random() < 0.5:
                        w.mesh(M_CYL, (lx + r.uniform(-2, 2), y + 1.5, lz + r.uniform(-2, 2)), scale=(1.5, 1.5, 1.5),
                               material=color_material(3), name=f'tank_{bi}_{bj}_{li}{lj}')
                    if r.random() < 0.15:
                        w.point((lx, y + 1.0, lz), (1, 0.2, 0.2), 4.0, 8.0, name=f'beacon_{bi}_{bj}_{li}{lj}')
            # street lamps along two sides of the block
            for s in range(5):
                t = (s + 0.5) / 5 * block - block / 2
                for (px, pz) in ((cx + t, cz - block / 2 - 2), (cx - block / 2 - 2, cz + t)):
                    pole = w.mesh(M_CYL, (px, 3, pz), scale=(0.12, 3, 0.12), material=color_material(0),
                                  name=f'lamp_{bi}_{bj}_{lamps}')
                    w.point((0, 1.1, 0), (1.0, 0.75, 0.45), 3.0, 11.0, name=f'lamp_light_{bi}_{bj}_{lamps}', parent=pole)
                    lamps += 1
    # traffic: balls along streets
    for i in range(400):
        along, across = r.uniform(-n / 2 * pitch, n / 2 * pitch), (r.randint(0, n) - n / 2) * pitch
        p = (along, 0.5, across) if r.random() < 0.5 else (across, 0.5, along)
        w.mesh(M_BALL, p, scale=0.5, material=color_material(r.randrange(N_COLOR_MATERIALS)), name=f'traffic_{i}')
    w.set_camera((150, 70, 190), (0, 10, 0), far=3000.0)
    return w


# ---- material variants ------------------------------------------------------------------------------------------------
def write_materials(content_dir):
    base = G.read(Path(content_dir) / MAT_BASE)
    out = Path(content_dir) / 'material' / 'stress'
    out.mkdir(parents=True, exist_ok=True)
    for i in range(N_COLOR_MATERIALS):
        h = i / N_COLOR_MATERIALS * 7.0          # several hue cycles with varying saturation / value / roughness
        rgb = hsv(h, 0.35 + 0.6 * ((i * 37) % 11) / 10, 0.35 + 0.6 * ((i * 53) % 7) / 6)
        rough = 0.1 + 0.85 * ((i * 29) % 13) / 12
        params = struct.pack('<4f', *rgb, rough)
        chunks = [(cid, params if cid == CHUNK_MATERIAL_PARAMS else data) for cid, data in base['chunks']]
        name = f'color_{i:03d}'
        (out / f'{name}.glt_material').write_bytes(
            G.compose(stable_uuid('material', name), base['type'], name, chunks, base['deps'], base['source']))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--content', default=str(Path(__file__).resolve().parent.parent / 'content'))
    ap.add_argument('--only', default='')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    if a.list:
        for k in WORLDS: print(k)
        return
    names = [n for n in a.only.split(',') if n] or list(WORLDS)
    write_materials(a.content)
    rows = []
    for n in names:
        w = WORLDS[n]()
        path = w.write(a.content)
        s = w.stats
        rows.append((n, s, w.description))
        print(f'{path:48s} entities={s["entities"]:>6} meshes={s["meshes"]:>6} lights={s["lights"]:>5} '
              f'regions={s["regions"]:>3} translucent={s["translucent"]:>5} dynamic={s["dynamic"]:>5} ships={s["ships"]:>3}')
    return rows


if __name__ == '__main__':
    main()

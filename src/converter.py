"""
GLB/GLTF to Minecraft Schematic Converter
Supports multi-block output by reading UV coordinates and sampling
the terrain atlas texture to determine the correct block per voxel.
"""

import argparse
import base64
import gzip
import io
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path


# --- NBT Writer --------------------------------------------------------------

def _pack_tag_type_and_name(tag_type: int, name: str) -> bytes:
    encoded = name.encode("utf-8")
    return struct.pack(">bH", tag_type, len(encoded)) + encoded

def _write_compound(payload: dict) -> bytes:
    out = b""
    for k, (t, v) in payload.items():
        out += _pack_tag_type_and_name(t, k)
        out += _write_value(t, v)
    out += b"\x00"
    return out

def _write_value(tag_type: int, value) -> bytes:
    if tag_type == 1:  return struct.pack(">b", value)
    if tag_type == 2:  return struct.pack(">h", value)
    if tag_type == 3:  return struct.pack(">i", value)
    if tag_type == 4:  return struct.pack(">q", value)
    if tag_type == 5:  return struct.pack(">f", value)
    if tag_type == 6:  return struct.pack(">d", value)
    if tag_type == 7:
        arr = bytes([b & 0xFF for b in value])
        return struct.pack(">i", len(arr)) + arr
    if tag_type == 8:
        enc = value.encode("utf-8")
        return struct.pack(">H", len(enc)) + enc
    if tag_type == 9:
        el_type, items = value
        out = struct.pack(">bi", el_type, len(items))
        for item in items:
            out += _write_value(el_type, item)
        return out
    if tag_type == 10: return _write_compound(value)
    if tag_type == 11:
        out = struct.pack(">i", len(value))
        return out + struct.pack(f">{len(value)}i", *value)
    raise ValueError(f"Unknown NBT tag type: {tag_type}")

def write_schematic(width, height, length, blocks, data, output_path):
    payload = {
        "Height":       (2, height),
        "Width":        (2, width),
        "Length":       (2, length),
        "Materials":    (8, "Alpha"),
        "Blocks":       (7, blocks),
        "Data":         (7, data),
        "Entities":     (9, (10, [])),
        "TileEntities": (9, (10, [])),
    }
    root_bytes = _pack_tag_type_and_name(10, "Schematic")
    root_bytes += _write_compound(payload)
    with gzip.open(output_path, "wb") as f:
        f.write(root_bytes)
    print(f"[OK] Schematic written -> {output_path}")
    print(f"    Size: {width}W x {height}H x {length}L  ({len(blocks)} blocks)")


# --- GLB/GLTF Loader ---------------------------------------------------------

def load_glb(path):
    with open(path, "rb") as f:
        magic, version, total_length = struct.unpack("<III", f.read(12))
        if magic != 0x46546C67:
            raise ValueError("Not a valid GLB file")
        chunks = []
        while f.tell() < total_length:
            chunk_length, chunk_type = struct.unpack("<II", f.read(8))
            chunks.append((chunk_type, f.read(chunk_length)))
    json_data = next((d for t, d in chunks if t == 0x4E4F534A), None)
    bin_data  = next((d for t, d in chunks if t == 0x004E4942), b"")
    if json_data is None:
        raise ValueError("GLB has no JSON chunk")
    return json.loads(json_data.decode("utf-8")), bin_data

def load_gltf(path):
    p = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        gltf = json.load(f)
    bin_data = b""
    buffers  = gltf.get("buffers", [])
    if buffers:
        uri = buffers[0].get("uri", "")
        if uri.startswith("data:"):
            _, encoded = uri.split(",", 1)
            bin_data = base64.b64decode(encoded)
        elif uri:
            with open(p.parent / uri, "rb") as f:
                bin_data = f.read()
    return gltf, bin_data


# --- Accessor Reader ---------------------------------------------------------

_COMP = {5120:("<b",1),5121:("<B",1),5122:("<h",2),5123:("<H",2),5125:("<I",4),5126:("<f",4)}
_NCOMP = {"SCALAR":1,"VEC2":2,"VEC3":3,"VEC4":4,"MAT2":4,"MAT3":9,"MAT4":16}

def read_accessor(gltf, bin_data, idx):
    acc   = gltf["accessors"][idx]
    bv    = gltf["bufferViews"][acc["bufferView"]]
    base  = bv.get("byteOffset",0) + acc.get("byteOffset",0)
    count = acc["count"]
    fmt_le, csz = _COMP[acc["componentType"]]
    n     = _NCOMP[acc["type"]]
    stride= bv.get("byteStride", csz*n)
    fmt   = "<" + fmt_le[1]*n
    res   = []
    for i in range(count):
        vals = struct.unpack_from(fmt, bin_data, base + i*stride)
        res.append(vals if n > 1 else vals[0])
    return res

def apply_node_transform(node, verts):
    if "matrix" in node:
        m = node["matrix"]
        return [(m[0]*x+m[4]*y+m[8]*z+m[12],
                 m[1]*x+m[5]*y+m[9]*z+m[13],
                 m[2]*x+m[6]*y+m[10]*z+m[14]) for x,y,z in verts]
    tx,ty,tz = node.get("translation",[0,0,0])
    rx,ry,rz,rw = node.get("rotation",[0,0,0,1])
    sx,sy,sz = node.get("scale",[1,1,1])
    def qrot(x,y,z):
        ix= rw*x+ry*z-rz*y; iy= rw*y+rz*x-rx*z; iz= rw*z+rx*y-ry*x; iw=-rx*x-ry*y-rz*z
        return ix*rw+iw*(-rx)+iy*(-rz)-iz*(-ry), iy*rw+iw*(-ry)+iz*(-rx)-ix*(-rz), iz*rw+iw*(-rz)+ix*(-ry)-iy*(-rx)
    out=[]
    for x,y,z in verts:
        x*=sx; y*=sy; z*=sz
        x,y,z=qrot(x,y,z)
        out.append((x+tx,y+ty,z+tz))
    return out


# --- Atlas Texture Loader ----------------------------------------------------

def load_atlas_from_glb(gltf, bin_data, image_index=0):
    try:
        from PIL import Image as PILImage
    except ImportError:
        return None
    images = gltf.get("images", [])
    if image_index >= len(images):
        return None
    img_info = images[image_index]
    bv_idx   = img_info.get("bufferView")
    if bv_idx is None:
        return None
    bv  = gltf["bufferViews"][bv_idx]
    raw = bin_data[bv.get("byteOffset",0) : bv.get("byteOffset",0)+bv["byteLength"]]
    try:
        return PILImage.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None


# --- UV -> Block ID Mapping --------------------------------------------------

# Bedrock terrain atlas: 2048x1024, 64 tiles wide, 32 tiles tall, each 32x32 px
ATLAS_W, ATLAS_H = 2048, 1024
ATLAS_COLS, ATLAS_ROWS = 64, 32
TILE_W, TILE_H = 32, 32

# (col, row) -> classic block ID
TILE_TO_BLOCK = {
    (13, 0): 1,  (33, 3): 1,  (32, 3): 1,   # stone
    (3,  0): 2,                               # grass
    (0,  0): 3,  (47,10): 3,  (4, 13): 3,   # dirt
    (55, 5): 12, (54, 5): 12, (20, 1): 12,  # sand
    (30, 3): 5,  (61, 9): 5,                 # oak planks
    (21, 3): 17, (22, 3): 17,                # oak log
    (18, 0): 18, (20, 0): 18, (42,10): 18,  # leaves
    (3,  1): 13,                              # gravel
    (1,  3): 20,                              # glass
    (0,  1): 4,                               # cobblestone
    (4,  2): 48,                              # mossy cobblestone
    (7,  0): 45,                              # brick
    (5,  2): 49,                              # obsidian
    (8, 10): 85, (15, 1): 85,                # fence
    (44,18): 1,                               # fallback
}

# Color -> block fallback
_COLOR_BLOCKS = [
    ((136,135,136), 1),  ((181,181,181), 1),   # stone
    ((97, 68, 46),  5),  ((191,142,107), 5),   # planks
    ((116, 90, 54), 3),  ((136,101, 57), 3),   # dirt
    ((218,207,163), 12), ((215,203,141), 12),  # sand
    ((80, 105, 44), 18), ((127,204, 25), 18),  # leaves
    ((104, 99,104), 85), ((111,108,111), 85),  # fence
    ((62,  68, 83), 1),                         # dark stone
]

def _color_to_block(r, g, b, a):
    if a < 30: return 0
    best_id, best_dist = 1, float("inf")
    for (cr,cg,cb), bid in _COLOR_BLOCKS:
        d = (r-cr)**2+(g-cg)**2+(b-cb)**2
        if d < best_dist:
            best_dist, best_id = d, bid
    return best_id

def get_block_id_from_uv(u, v, atlas_img):
    tile = (int((u%1.0)*ATLAS_COLS)%ATLAS_COLS, int((v%1.0)*ATLAS_ROWS)%ATLAS_ROWS)
    if tile in TILE_TO_BLOCK:
        return TILE_TO_BLOCK[tile]
    if atlas_img:
        px = int((u%1.0)*ATLAS_W)%ATLAS_W
        py = int((v%1.0)*ATLAS_H)%ATLAS_H
        return _color_to_block(*atlas_img.getpixel((px, py)))
    return 1


# --- Geometry + UV Extraction ------------------------------------------------

def extract_all_geometry(gltf, bin_data, atlas_img):
    """Returns list of (x, y, z, block_id)."""
    all_verts = []
    nodes_map = {i: n for i, n in enumerate(gltf.get("nodes", []))}

    def process_node(node_idx):
        node = nodes_map.get(node_idx, {})
        if "mesh" in node:
            mesh = gltf["meshes"][node["mesh"]]
            for prim in mesh.get("primitives", []):
                attrs = prim.get("attributes", {})
                if "POSITION" not in attrs: continue
                verts = [(v[0],v[1],v[2]) for v in read_accessor(gltf, bin_data, attrs["POSITION"])]
                verts = apply_node_transform(node, verts)
                uvs = read_accessor(gltf, bin_data, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else None
                if "indices" in prim:
                    indices = read_accessor(gltf, bin_data, prim["indices"])
                    for i in range(0, len(indices)-2, 3):
                        a,b,c = indices[i],indices[i+1],indices[i+2]
                        bid = get_block_id_from_uv(uvs[a][0],uvs[a][1],atlas_img) if uvs and a<len(uvs) else 1
                        all_verts += [(*verts[a],bid),(*verts[b],bid),(*verts[c],bid)]
                else:
                    for i,v in enumerate(verts):
                        bid = get_block_id_from_uv(uvs[i][0],uvs[i][1],atlas_img) if uvs and i<len(uvs) else 1
                        all_verts.append((*v, bid))
        for child in node.get("children", []):
            process_node(child)

    scene_idx = gltf.get("scene", 0)
    scenes    = gltf.get("scenes", [])
    if scenes:
        for r in scenes[scene_idx].get("nodes", []): process_node(r)
    else:
        for i in nodes_map: process_node(i)
    return all_verts

def extract_all_vertices(gltf, bin_data):
    """Compatibility wrapper for tests."""
    return [(x,y,z) for x,y,z,_ in extract_all_geometry(gltf, bin_data, None)]


# --- Voxelization ------------------------------------------------------------

def _bounds_and_scale(points, resolution):
    xs=[p[0] for p in points]; ys=[p[1] for p in points]; zs=[p[2] for p in points]
    mn=(min(xs),min(ys),min(zs)); mx=(max(xs),max(ys),max(zs))
    span=max(mx[i]-mn[i] for i in range(3)) or 1.0
    scale=(resolution-1)/span
    print(f"[i] Model bounds: X[{mn[0]:.2f},{mx[0]:.2f}] Y[{mn[1]:.2f},{mx[1]:.2f}] Z[{mn[2]:.2f},{mx[2]:.2f}]")
    return mn, mx, scale

def _wv(p, mn, scale):
    return tuple(int(round((p[i]-mn[i])*scale)) for i in range(3))

def _lerp(a,b,t): return tuple(a[i]+(b[i]-a[i])*t for i in range(len(a)))

def voxelize(vertices, resolution, fill=True):
    """For tests: vertices are (x,y,z)."""
    if not vertices: raise ValueError("No geometry found in the GLB/GLTF file")
    mn, mx, scale = _bounds_and_scale(vertices, resolution)
    grid = set()
    steps = resolution*2
    for i in range(0, len(vertices)-2, 3):
        tri = vertices[i:i+3]
        for s in range(steps+1):
            t1=s/steps
            p1=_lerp(tri[0],tri[1],t1); p2=_lerp(tri[0],tri[2],t1)
            for u in range(steps+1):
                grid.add(_wv(_lerp(p1,p2,u/steps), mn, scale))
    w=int(round((mx[0]-mn[0])*scale))+1
    h=int(round((mx[1]-mn[1])*scale))+1
    l=int(round((mx[2]-mn[2])*scale))+1
    if fill and grid:
        print("[i] Filling interior voxels...")
        vd=_flood_fill(grid,w,h,l)
    else:
        vd={v:1 for v in grid}
    return vd, w, h, l

def voxelize_with_colors(geo_verts, resolution, fill=True):
    """geo_verts: (x,y,z,block_id)."""
    if not geo_verts: raise ValueError("No geometry found")
    pts=[(v[0],v[1],v[2]) for v in geo_verts]
    mn, mx, scale = _bounds_and_scale(pts, resolution)
    print(f"[i] Scale factor: {scale:.4f} -> resolution {resolution}")
    surface={}
    steps=resolution*2
    for i in range(0, len(geo_verts)-2, 3):
        tri=geo_verts[i:i+3]
        bid=tri[0][3]
        t0=tri[0][:3]; t1=tri[1][:3]; t2=tri[2][:3]
        for s in range(steps+1):
            r=s/steps
            p1=_lerp(t0,t1,r); p2=_lerp(t0,t2,r)
            for u in range(steps+1):
                vox=_wv(_lerp(p1,p2,u/steps), mn, scale)
                if vox not in surface or surface[vox]==1:
                    surface[vox]=bid
    w=int(round((mx[0]-mn[0])*scale))+1
    h=int(round((mx[1]-mn[1])*scale))+1
    l=int(round((mx[2]-mn[2])*scale))+1
    if fill and surface:
        print("[i] Filling interior voxels...")
        vd=_flood_fill(set(surface.keys()),w,h,l)
        for vox,bid in surface.items(): vd[vox]=bid
    else:
        vd=dict(surface)
    return vd, w, h, l

def _flood_fill(surface_voxels, width, height, length):
    from collections import deque
    visited=set(); queue=deque([(-1,-1,-1)]); visited.add((-1,-1,-1))
    w,h,l=width,height,length
    dirs=[(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]
    while queue:
        x,y,z=queue.popleft()
        for dx,dy,dz in dirs:
            nb=(x+dx,y+dy,z+dz)
            if nb in visited or nb in surface_voxels: continue
            if nb[0]<-1 or nb[1]<-1 or nb[2]<-1 or nb[0]>w or nb[1]>h or nb[2]>l: continue
            visited.add(nb); queue.append(nb)
    result={v:1 for v in surface_voxels}
    for x in range(w):
        for y in range(h):
            for z in range(l):
                vox=(x,y,z)
                if vox not in surface_voxels and vox not in visited:
                    result[vox]=4
    return result


# --- Block Mapping -----------------------------------------------------------

BLOCK_MAP = {
    "stone":1,"grass":2,"dirt":3,"cobblestone":4,"planks":5,
    "sand":12,"gravel":13,"gold_ore":14,"iron_ore":15,"coal_ore":16,
    "wood":17,"leaves":18,"glass":20,"lapis_ore":21,"sandstone":24,
    "wool":35,"gold_block":41,"iron_block":42,"smooth_stone":43,
    "brick":45,"tnt":46,"bookshelf":47,"mossy_cobblestone":48,
    "obsidian":49,"fence":85,"snow":80,"clay":82,"quartz_block":155,
}


# --- Main Conversion ---------------------------------------------------------

def convert(input_path, output_path, resolution=64, block="stone",
            fill=True, scale=1.0, multi_block=True):
    p=Path(input_path); ext=p.suffix.lower()
    print(f"[i] Loading {ext} file: {input_path}")
    if ext==".glb":   gltf,bin_data=load_glb(input_path)
    elif ext==".gltf": gltf,bin_data=load_gltf(input_path)
    else: raise ValueError(f"Unsupported: {ext}")

    atlas_img=None
    if multi_block:
        atlas_img=load_atlas_from_glb(gltf,bin_data,0)
        if atlas_img: print(f"[i] Loaded terrain atlas ({atlas_img.size[0]}x{atlas_img.size[1]})")
        else: print("[i] No atlas found, using single-block mode")

    print(f"[i] Extracting geometry...")
    if multi_block and atlas_img:
        geo=extract_all_geometry(gltf,bin_data,atlas_img)
        print(f"[i] Found {len(geo)} vertex positions")
        if scale!=1.0: geo=[(x*scale,y*scale,z*scale,b) for x,y,z,b in geo]
        print(f"[i] Voxelizing at resolution {resolution} (multi-block)...")
        vd,w,h,l=voxelize_with_colors(geo,resolution,fill)
    else:
        verts=extract_all_vertices(gltf,bin_data)
        print(f"[i] Found {len(verts)} vertex positions")
        if scale!=1.0: verts=[(x*scale,y*scale,z*scale) for x,y,z in verts]
        print(f"[i] Voxelizing at resolution {resolution}...")
        vd,w,h,l=voxelize(verts,resolution,fill)

    print(f"[i] Voxel count: {len(vd)}")
    id2name={v:k for k,v in BLOCK_MAP.items()}
    bc=Counter(vd.values())
    print(f"[i] Block distribution:")
    for bid,cnt in sorted(bc.items(),key=lambda x:-x[1])[:10]:
        print(f"    {id2name.get(bid,f'id:{bid}'):20s} x{cnt}")

    default_id=BLOCK_MAP.get(block,1); interior_id=BLOCK_MAP.get("cobblestone",4)
    total=w*h*l; blocks_arr=[0]*total; data_arr=[0]*total
    for (x,y,z),bid in vd.items():
        if 0<=x<w and 0<=y<h and 0<=z<l:
            idx=y*l*w+z*w+x
            if multi_block and atlas_img:
                blocks_arr[idx]=bid if bid!=4 else interior_id
            else:
                blocks_arr[idx]=default_id if bid==1 else interior_id
            data_arr[idx]=0
    write_schematic(w,h,l,blocks_arr,data_arr,output_path)


# --- CLI ---------------------------------------------------------------------

def main():
    parser=argparse.ArgumentParser(description="Convert GLB/GLTF to Minecraft .schematic")
    parser.add_argument("input");  parser.add_argument("output")
    parser.add_argument("--resolution","-r",type=int,default=64)
    parser.add_argument("--block","-b",type=str,default="stone")
    parser.add_argument("--fill",action="store_true",default=True)
    parser.add_argument("--no-fill",dest="fill",action="store_false")
    parser.add_argument("--scale","-s",type=float,default=1.0)
    parser.add_argument("--no-multi-block",dest="multi_block",action="store_false",default=True)
    args=parser.parse_args()
    convert(args.input,args.output,resolution=args.resolution,block=args.block,
            fill=args.fill,scale=args.scale,multi_block=args.multi_block)

if __name__=="__main__":
    main()

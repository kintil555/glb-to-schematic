"""
GLB/GLTF to Minecraft Schematic Converter
For use with Mine-imator or MCEdit/Schematica
"""

import argparse
import gzip
import json
import math
import struct
import sys
from pathlib import Path


# ─── NBT Writer ──────────────────────────────────────────────────────────────

def _pack_tag_type_and_name(tag_type: int, name: str) -> bytes:
    encoded = name.encode("utf-8")
    return struct.pack(">bH", tag_type, len(encoded)) + encoded


def _write_compound(payload: dict) -> bytes:
    out = b""
    for k, (t, v) in payload.items():
        out += _pack_tag_type_and_name(t, k)
        out += _write_value(t, v)
    out += b"\x00"  # TAG_End
    return out


def _write_value(tag_type: int, value) -> bytes:
    if tag_type == 1:   return struct.pack(">b", value)          # byte
    if tag_type == 2:   return struct.pack(">h", value)          # short
    if tag_type == 3:   return struct.pack(">i", value)          # int
    if tag_type == 4:   return struct.pack(">q", value)          # long
    if tag_type == 5:   return struct.pack(">f", value)          # float
    if tag_type == 6:   return struct.pack(">d", value)          # double
    if tag_type == 7:                                             # byte array
        arr = bytes([b & 0xFF for b in value])
        return struct.pack(">i", len(arr)) + arr
    if tag_type == 8:                                             # string
        enc = value.encode("utf-8")
        return struct.pack(">H", len(enc)) + enc
    if tag_type == 9:                                             # list
        el_type, items = value
        out = struct.pack(">bi", el_type, len(items))
        for item in items:
            out += _write_value(el_type, item)
        return out
    if tag_type == 10:  return _write_compound(value)            # compound
    if tag_type == 11:                                            # int array
        out = struct.pack(">i", len(value))
        return out + struct.pack(f">{len(value)}i", *value)
    raise ValueError(f"Unknown NBT tag type: {tag_type}")


def write_schematic(width: int, height: int, length: int,
                    blocks: list, data: list, output_path: str):
    """
    Write a classic Minecraft .schematic file (Alpha/MCEdit format).
    blocks and data are flat byte arrays indexed as [y * length * width + z * width + x].
    """
    payload = {
        "Height":         (2, height),
        "Width":          (2, width),
        "Length":         (2, length),
        "Materials":      (8, "Alpha"),
        "Blocks":         (7, blocks),
        "Data":           (7, data),
        "Entities":       (9, (10, [])),
        "TileEntities":   (9, (10, [])),
    }

    # Root compound
    root_bytes = _pack_tag_type_and_name(10, "Schematic")
    root_bytes += _write_compound(payload)

    with gzip.open(output_path, "wb") as f:
        f.write(root_bytes)

    print(f"[✓] Schematic written → {output_path}")
    print(f"    Size: {width}W × {height}H × {length}L  ({len(blocks)} blocks)")


# ─── GLTF/GLB Loader ─────────────────────────────────────────────────────────

def load_glb(path: str):
    """Parse a GLB (binary GLTF) file and return (gltf_json, binary_chunk)."""
    with open(path, "rb") as f:
        magic, version, total_length = struct.unpack("<III", f.read(12))
        if magic != 0x46546C67:
            raise ValueError("Not a valid GLB file (bad magic number)")

        chunks = []
        while f.tell() < total_length:
            chunk_length, chunk_type = struct.unpack("<II", f.read(8))
            chunk_data = f.read(chunk_length)
            chunks.append((chunk_type, chunk_data))

    # chunk_type 0x4E4F534A = JSON, 0x004E4942 = BIN
    json_data = next((d for t, d in chunks if t == 0x4E4F534A), None)
    bin_data  = next((d for t, d in chunks if t == 0x004E4942), b"")

    if json_data is None:
        raise ValueError("GLB has no JSON chunk")

    return json.loads(json_data.decode("utf-8")), bin_data


def load_gltf(path: str):
    """Parse a .gltf (JSON) file and load its binary buffer."""
    p = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        gltf = json.load(f)

    bin_data = b""
    buffers = gltf.get("buffers", [])
    if buffers:
        uri = buffers[0].get("uri", "")
        if uri.startswith("data:"):
            import base64
            _, encoded = uri.split(",", 1)
            bin_data = base64.b64decode(encoded)
        elif uri:
            bin_path = p.parent / uri
            with open(bin_path, "rb") as f:
                bin_data = f.read()

    return gltf, bin_data


# ─── Geometry Extraction ──────────────────────────────────────────────────────

ACCESSOR_COMPONENT_TYPES = {
    5120: (">b", 1), 5121: (">B", 1),
    5122: (">h", 2), 5123: (">H", 2),
    5125: (">I", 4), 5126: (">f", 4),
}
ACCESSOR_COMPONENT_TYPES_LE = {
    5120: ("<b", 1), 5121: ("<B", 1),
    5122: ("<h", 2), 5123: ("<H", 2),
    5125: ("<I", 4), 5126: ("<f", 4),
}
ACCESSOR_COUNTS = {
    "SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
    "MAT2": 4, "MAT3": 9, "MAT4": 16,
}


def read_accessor(gltf, bin_data, accessor_idx):
    """Read a GLTF accessor and return a list of tuples."""
    acc  = gltf["accessors"][accessor_idx]
    bv   = gltf["bufferViews"][acc["bufferView"]]

    buf_offset  = bv.get("byteOffset", 0)
    acc_offset  = acc.get("byteOffset", 0)
    count       = acc["count"]
    comp_type   = acc["componentType"]
    elem_type   = acc["type"]
    n_comps     = ACCESSOR_COUNTS[elem_type]
    fmt_char = ACCESSOR_COMPONENT_TYPES_LE[comp_type][0][1]  # strip endian prefix
    comp_size = ACCESSOR_COMPONENT_TYPES_LE[comp_type][1]
    stride      = bv.get("byteStride", comp_size * n_comps)
    fmt = "<" + fmt_char * n_comps

    results = []
    base = buf_offset + acc_offset
    for i in range(count):
        pos = base + i * stride
        vals = struct.unpack_from(fmt, bin_data, pos)
        results.append(vals if n_comps > 1 else vals[0])

    return results


def apply_node_transform(node, vertices):
    """Apply TRS or matrix transform from a GLTF node to a list of (x,y,z) vertices."""
    import math

    if "matrix" in node:
        m = node["matrix"]
        out = []
        for x, y, z in vertices:
            nx = m[0]*x + m[4]*y + m[8]*z  + m[12]
            ny = m[1]*x + m[5]*y + m[9]*z  + m[13]
            nz = m[2]*x + m[6]*y + m[10]*z + m[14]
            out.append((nx, ny, nz))
        return out

    tx, ty, tz = node.get("translation", [0, 0, 0])
    rx, ry, rz, rw = node.get("rotation", [0, 0, 0, 1])
    sx, sy, sz     = node.get("scale",    [1, 1, 1])

    # Quaternion → rotation matrix
    def quat_rotate(x, y, z):
        # Apply quaternion rotation
        ix =  rw*x + ry*z - rz*y
        iy =  rw*y + rz*x - rx*z
        iz =  rw*z + rx*y - ry*x
        iw = -rx*x - ry*y - rz*z
        nx = ix*rw + iw*(-rx) + iy*(-rz) - iz*(-ry)
        ny = iy*rw + iw*(-ry) + iz*(-rx) - ix*(-rz)
        nz = iz*rw + iw*(-rz) + ix*(-ry) - iy*(-rx)
        return nx, ny, nz

    out = []
    for x, y, z in vertices:
        # Scale
        x *= sx; y *= sy; z *= sz
        # Rotate
        x, y, z = quat_rotate(x, y, z)
        # Translate
        out.append((x + tx, y + ty, z + tz))

    return out


def extract_all_vertices(gltf, bin_data):
    """Walk all nodes and meshes, collecting all world-space triangles."""
    all_verts = []
    nodes_map = {i: n for i, n in enumerate(gltf.get("nodes", []))}

    def process_node(node_idx, parent_transform=None):
        node = nodes_map.get(node_idx, {})

        if "mesh" in node:
            mesh = gltf["meshes"][node["mesh"]]
            for prim in mesh.get("primitives", []):
                attrs = prim.get("attributes", {})
                if "POSITION" not in attrs:
                    continue

                verts = read_accessor(gltf, bin_data, attrs["POSITION"])
                verts = [(v[0], v[1], v[2]) for v in verts]

                # Apply this node's local transform
                verts = apply_node_transform(node, verts)

                if "indices" in prim:
                    indices = read_accessor(gltf, bin_data, prim["indices"])
                    for i in range(0, len(indices) - 2, 3):
                        a, b, c = indices[i], indices[i+1], indices[i+2]
                        all_verts.extend([verts[a], verts[b], verts[c]])
                else:
                    all_verts.extend(verts)

        for child_idx in node.get("children", []):
            process_node(child_idx)

    # Start from scene roots
    scene_idx = gltf.get("scene", 0)
    scenes = gltf.get("scenes", [])
    if scenes:
        for root_idx in scenes[scene_idx].get("nodes", []):
            process_node(root_idx)
    else:
        for i in nodes_map:
            process_node(i)

    return all_verts


# ─── Voxelization ─────────────────────────────────────────────────────────────

def voxelize(vertices, resolution: int, fill: bool = True):
    """
    Convert a triangle soup into a voxel grid.
    resolution = number of voxels along the longest axis.
    Returns (grid_dict, width, height, length) where grid_dict is {(x,y,z): block_id}.
    """
    if not vertices:
        raise ValueError("No geometry found in the GLB/GLTF file")

    # Bounds
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)

    span = max(max_x - min_x, max_y - min_y, max_z - min_z)
    if span == 0:
        span = 1.0
    scale = (resolution - 1) / span

    print(f"[i] Model bounds: X[{min_x:.2f},{max_x:.2f}] Y[{min_y:.2f},{max_y:.2f}] Z[{min_z:.2f},{max_z:.2f}]")
    print(f"[i] Scale factor: {scale:.4f} → resolution {resolution}")

    def world_to_vox(wx, wy, wz):
        return (
            int(round((wx - min_x) * scale)),
            int(round((wy - min_y) * scale)),
            int(round((wz - min_z) * scale)),
        )

    def lerp3(a, b, t):
        return (
            a[0] + (b[0]-a[0])*t,
            a[1] + (b[1]-a[1])*t,
            a[2] + (b[2]-a[2])*t,
        )

    grid = set()

    # Rasterize each triangle by walking along edges
    for i in range(0, len(vertices) - 2, 3):
        tri = [vertices[i], vertices[i+1], vertices[i+2]]
        tri_v = [world_to_vox(*v) for v in tri]

        # Sample along triangle surface
        steps = resolution * 2
        for s in range(steps + 1):
            t1 = s / steps
            p1 = lerp3(tri[0], tri[1], t1)
            p2 = lerp3(tri[0], tri[2], t1)
            for u in range(steps + 1):
                t2 = u / steps
                p = lerp3(p1, p2, t2)
                grid.add(world_to_vox(*p))

    width  = int(round((max_x - min_x) * scale)) + 1
    height = int(round((max_y - min_y) * scale)) + 1
    length = int(round((max_z - min_z) * scale)) + 1

    # Optionally flood-fill interior (solid model)
    voxel_dict = {}
    if fill and len(grid) > 0:
        print("[i] Filling interior voxels...")
        voxel_dict = flood_fill_interior(grid, width, height, length)
    else:
        for vox in grid:
            voxel_dict[vox] = 1  # stone

    return voxel_dict, width, height, length


def flood_fill_interior(surface_voxels, width, height, length):
    """Mark shell as block 1 (stone), detect interior via flood fill from outside."""
    OUTSIDE = 0
    SURFACE = 1
    INSIDE  = 2

    grid = {}
    for v in surface_voxels:
        grid[v] = SURFACE

    # BFS from (−1,−1,−1) to mark exterior
    from collections import deque
    visited = set()
    queue   = deque()

    start = (-1, -1, -1)
    queue.append(start)
    visited.add(start)

    w, h, l = width, height, length

    while queue:
        x, y, z = queue.popleft()
        for dx, dy, dz in [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]:
            nx, ny, nz = x+dx, y+dy, z+dz
            if (nx, ny, nz) in visited:
                continue
            if nx < -1 or ny < -1 or nz < -1 or nx > w or ny > h or nz > l:
                continue
            if (nx, ny, nz) in surface_voxels:
                continue
            visited.add((nx, ny, nz))
            queue.append((nx, ny, nz))

    result = {}
    for v in surface_voxels:
        result[v] = 1  # stone (surface)

    for x in range(w):
        for y in range(h):
            for z in range(l):
                vox = (x, y, z)
                if vox not in surface_voxels and vox not in visited:
                    result[vox] = 4  # cobblestone (interior)

    return result


# ─── Block Mapping ────────────────────────────────────────────────────────────

# Classic Minecraft block IDs (1.12 and below / Alpha format)
BLOCK_MAP = {
    "stone":      1,
    "grass":      2,
    "dirt":       3,
    "cobblestone":4,
    "planks":     5,
    "sand":       12,
    "gravel":     13,
    "gold_ore":   14,
    "iron_ore":   15,
    "coal_ore":   16,
    "wood":       17,
    "leaves":     18,
    "glass":      20,
    "lapis_ore":  21,
    "sandstone":  24,
    "wool":       35,
    "gold_block":  41,
    "iron_block":  42,
    "smooth_stone":43,
    "brick":      45,
    "tnt":        46,
    "bookshelf":  47,
    "mossy_cobblestone": 48,
    "obsidian":   49,
    "snow":       80,
    "clay":       82,
    "quartz_block":155,
}


# ─── Main Conversion Logic ────────────────────────────────────────────────────

def convert(input_path: str, output_path: str,
            resolution: int = 64,
            block: str = "stone",
            fill: bool = True,
            scale: float = 1.0):
    """Full conversion pipeline: GLB/GLTF → Schematic"""

    p = Path(input_path)
    ext = p.suffix.lower()

    print(f"[i] Loading {ext} file: {input_path}")

    if ext == ".glb":
        gltf, bin_data = load_glb(input_path)
    elif ext == ".gltf":
        gltf, bin_data = load_gltf(input_path)
    else:
        raise ValueError(f"Unsupported format: {ext}. Use .glb or .gltf")

    print(f"[i] Extracting geometry...")
    vertices = extract_all_vertices(gltf, bin_data)
    print(f"[i] Found {len(vertices)} vertex positions")

    if scale != 1.0:
        vertices = [(x*scale, y*scale, z*scale) for x, y, z in vertices]

    print(f"[i] Voxelizing at resolution {resolution}...")
    voxel_dict, width, height, length = voxelize(vertices, resolution, fill)
    print(f"[i] Voxel count: {len(voxel_dict)}")

    block_id = BLOCK_MAP.get(block, 1)
    interior_id = BLOCK_MAP.get("cobblestone", 4)

    # Flatten to arrays
    total = width * height * length
    blocks_arr = [0] * total
    data_arr   = [0] * total

    for (x, y, z), bid in voxel_dict.items():
        if 0 <= x < width and 0 <= y < height and 0 <= z < length:
            idx = y * length * width + z * width + x
            blocks_arr[idx] = block_id if bid == 1 else interior_id
            data_arr[idx]   = 0

    write_schematic(width, height, length, blocks_arr, data_arr, output_path)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Convert GLB/GLTF 3D models to Minecraft .schematic format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python converter.py model.glb output.schematic
  python converter.py model.gltf output.schematic --resolution 128 --block wool --fill
  python converter.py model.glb output.schematic --resolution 32 --no-fill --scale 2.0

Block options:
  stone, grass, dirt, cobblestone, planks, sand, gravel, glass, wool,
  gold_block, iron_block, brick, obsidian, snow, clay, quartz_block, ...
        """
    )
    parser.add_argument("input",       help="Input .glb or .gltf file")
    parser.add_argument("output",      help="Output .schematic file")
    parser.add_argument("--resolution","-r", type=int,   default=64,
                        help="Max voxel resolution along longest axis (default: 64)")
    parser.add_argument("--block",     "-b", type=str,   default="stone",
                        help="Block type for shell (default: stone)")
    parser.add_argument("--fill",      action="store_true",  default=True,
                        help="Fill interior (default: True)")
    parser.add_argument("--no-fill",   dest="fill", action="store_false",
                        help="Only voxelize surface (hollow)")
    parser.add_argument("--scale",     "-s", type=float, default=1.0,
                        help="Scale factor for the model (default: 1.0)")

    args = parser.parse_args()
    convert(args.input, args.output,
            resolution=args.resolution,
            block=args.block,
            fill=args.fill,
            scale=args.scale)


if __name__ == "__main__":
    main()

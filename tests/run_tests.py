"""
Standalone test runner — zero external dependencies.
Run: python tests/run_tests.py
"""
import gzip
import json
import os
import struct
import sys
import tempfile

# Make sure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from converter import (
    BLOCK_MAP,
    _write_value,
    apply_node_transform,
    convert,
    voxelize,
    write_schematic,
)

PASS = 0
FAIL = 0


def ok(name):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}")


def fail(name, reason):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name} — {reason}")


def section(title):
    print(f"\n=== {title} ===")


# ─── NBT Writer ──────────────────────────────────────────────────────────────

section("NBT Writer")

try:
    r = _write_value(2, 23)
    assert r == struct.pack(">h", 23)
    ok("write short")
except Exception as e:
    fail("write short", e)

try:
    r = _write_value(8, "Alpha")
    assert r == struct.pack(">H", 5) + b"Alpha"
    ok("write string")
except Exception as e:
    fail("write string", e)

try:
    r = _write_value(7, [1, 2, 3, 4])
    assert r == struct.pack(">i", 4) + bytes([1, 2, 3, 4])
    ok("write byte array")
except Exception as e:
    fail("write byte array", e)

try:
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "test.schematic")
        write_schematic(2, 2, 2, [1, 0, 0, 0, 0, 0, 0, 0], [0] * 8, out)
        with gzip.open(out, "rb") as f:
            raw = f.read()
        assert raw[0] == 10
        nl = struct.unpack(">H", raw[1:3])[0]
        assert raw[3:3 + nl].decode() == "Schematic"
    ok("schematic roundtrip (root=Schematic)")
except Exception as e:
    fail("schematic roundtrip", e)


# ─── Voxelizer ───────────────────────────────────────────────────────────────

section("Voxelizer")

try:
    tri = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    vx, w, h, l = voxelize(tri, resolution=8, fill=False)
    assert len(vx) > 0 and w > 0 and h > 0 and l > 0
    ok(f"single triangle -> {len(vx)} voxels, {w}x{h}x{l}")
except Exception as e:
    fail("single triangle", e)

try:
    voxelize([], 8)
    fail("empty raises ValueError", "no exception raised")
except ValueError:
    ok("empty vertices raises ValueError")
except Exception as e:
    fail("empty raises ValueError", e)

try:
    node = {"translation": [10.0, 0.0, 0.0]}
    res = apply_node_transform(node, [(1.0, 0.0, 0.0)])
    assert abs(res[0][0] - 11.0) < 1e-5
    ok("translation transform")
except Exception as e:
    fail("translation transform", e)

try:
    node = {"scale": [2.0, 3.0, 4.0]}
    res = apply_node_transform(node, [(1.0, 1.0, 1.0)])
    assert abs(res[0][0] - 2.0) < 1e-5
    assert abs(res[0][1] - 3.0) < 1e-5
    assert abs(res[0][2] - 4.0) < 1e-5
    ok("scale transform")
except Exception as e:
    fail("scale transform", e)

try:
    # identity matrix (column-major) + translation x+5
    m = [1, 0, 0, 0,  0, 1, 0, 0,  0, 0, 1, 0,  5, 0, 0, 1]
    node = {"matrix": m}
    res = apply_node_transform(node, [(0.0, 0.0, 0.0)])
    assert abs(res[0][0] - 5.0) < 1e-5
    ok("matrix transform (translate x+5)")
except Exception as e:
    fail("matrix transform", e)


# ─── Block Map ───────────────────────────────────────────────────────────────

section("Block Map")

try:
    assert BLOCK_MAP["stone"] == 1
    ok("stone == 1")
except Exception as e:
    fail("stone == 1", e)

try:
    ids = list(BLOCK_MAP.values())
    assert len(ids) == len(set(ids))
    ok("no duplicate IDs")
except Exception as e:
    fail("no duplicate IDs", e)

try:
    assert all(0 < v < 256 for v in BLOCK_MAP.values())
    ok("all IDs in valid range 1-255")
except Exception as e:
    fail("all IDs in valid range", e)


# ─── Integration ─────────────────────────────────────────────────────────────

section("Integration: Full Pipeline GLB -> Schematic")


def make_minimal_glb() -> bytes:
    positions = struct.pack("<9f", 0., 0., 0., 1., 0., 0., 0., 1., 0.)
    indices   = struct.pack("<3H", 0, 1, 2) + b"\x00\x00"
    bin_chunk = positions + indices

    pos_bv  = {"buffer": 0, "byteOffset": 0,  "byteLength": 36}
    idx_bv  = {"buffer": 0, "byteOffset": 36, "byteLength": 6}
    pos_acc = {"bufferView": 0, "componentType": 5126, "count": 3,
               "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 0]}
    idx_acc = {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"}

    g = {
        "asset":       {"version": "2.0"},
        "scene":       0,
        "scenes":      [{"nodes": [0]}],
        "nodes":       [{"mesh": 0}],
        "meshes":      [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "accessors":   [pos_acc, idx_acc],
        "bufferViews": [pos_bv, idx_bv],
        "buffers":     [{"byteLength": len(bin_chunk)}],
    }
    jb = json.dumps(g).encode()
    while len(jb) % 4:
        jb += b" "

    jc = struct.pack("<II", len(jb), 0x4E4F534A) + jb
    bc = struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    return struct.pack("<III", 0x46546C67, 2, 12 + len(jc) + len(bc)) + jc + bc


try:
    with tempfile.TemporaryDirectory() as d:
        glb = os.path.join(d, "model.glb")
        out = os.path.join(d, "model.schematic")

        with open(glb, "wb") as f:
            f.write(make_minimal_glb())

        convert(glb, out, resolution=16, fill=False)

        assert os.path.exists(out), "output file not created"
        assert os.path.getsize(out) > 0, "output file is empty"

        with gzip.open(out, "rb") as f:
            raw = f.read()
        assert raw[0] == 10, f"expected TAG_Compound (10), got {raw[0]}"

    ok("GLB -> .schematic: valid gzip+NBT output")
except Exception as e:
    fail("GLB -> .schematic", e)


# ─── Summary ─────────────────────────────────────────────────────────────────

total = PASS + FAIL
print(f"\n{'='*40}")
print(f"Results: {PASS}/{total} passed", end="")
if FAIL:
    print(f"  ({FAIL} FAILED)")
    sys.exit(1)
else:
    print(" OK")
    sys.exit(0)

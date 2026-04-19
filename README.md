# GLB/GLTF → Minecraft Schematic Converter

Convert any 3D model in **GLB or GLTF** format into a Minecraft `.schematic` file,
ready to import into **Mine-imator**, MCEdit, Schematica, or Litematica.

> **Zero external dependencies** — pure Python stdlib (`gzip`, `struct`, `json`).

---

## Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/glb-to-schematic.git
cd glb-to-schematic

# Run (no install needed)
python src/converter.py model.glb output.schematic
```

Or download a pre-built binary from [Releases](../../releases).

---

## Usage

```
python src/converter.py <input.glb|gltf> <output.schematic> [options]

Options:
  --resolution, -r INT    Voxel grid size along longest axis (default: 64)
  --block, -b NAME        Block type for the model shell (default: stone)
  --fill                  Fill interior with cobblestone — default behaviour
  --no-fill               Surface only (hollow model)
  --scale, -s FLOAT       Uniform scale multiplier (default: 1.0)
```

### Examples

```bash
# Simple conversion
python src/converter.py house.glb house.schematic

# Higher detail (128 voxels), glass blocks, hollow
python src/converter.py statue.glb statue.schematic -r 128 -b glass --no-fill

# Scale up model 3×, filled with wool
python src/converter.py car.gltf car.schematic -r 64 -b wool -s 3.0
```

---

## Supported Block Types

| Name | Block ID | Notes |
|------|----------|-------|
| `stone` | 1 | default |
| `grass` | 2 | |
| `dirt` | 3 | |
| `cobblestone` | 4 | used for interior fill |
| `planks` | 5 | |
| `sand` | 12 | |
| `glass` | 20 | great for transparent models |
| `wool` | 35 | |
| `brick` | 45 | |
| `obsidian` | 49 | |
| `quartz_block` | 155 | |
| … and more in `BLOCK_MAP` |

---

## Importing into Mine-imator

1. Open **Mine-imator**
2. Go to **Project → Import Schematic**
3. Select your `.schematic` file
4. The model appears as a block structure in your scene

> ⚠️ Mine-imator supports classic `.schematic` format (Alpha/MCEdit).
> This tool writes exactly that format.

---

## How It Works

```
GLB/GLTF file
     │
     ▼
Parse JSON + binary buffer
     │
     ▼
Extract triangle soup (all mesh primitives)
Apply node transforms (TRS / matrix)
     │
     ▼
Voxelization (rasterize triangles → voxel grid)
Optional interior flood-fill
     │
     ▼
Write Minecraft NBT .schematic (gzip compressed)
```

---

## Build from Source

```bash
pip install pyinstaller
pyinstaller --onefile --name glb-to-schematic src/converter.py
# binary → dist/glb-to-schematic
```

Or let **GitHub Actions** build it for all platforms automatically (see `.github/workflows/build.yml`).

---

## Running Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

---

## Resolution Guide

| `--resolution` | Voxels | Detail | File size |
|---------------|--------|--------|-----------|
| 32 | ~32K | Low | ~50 KB |
| 64 | ~262K | Medium | ~400 KB |
| 128 | ~2M | High | ~3 MB |
| 256 | ~16M | Very high | ~25 MB+ |

> Large resolutions may take minutes and create very large schematics.
> Start with 64 and go up as needed.

---

## License

MIT

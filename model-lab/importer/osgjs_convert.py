#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pure-Python Sketchfab OSGJS → OBJ (+ MTL) converter.

Decode path mirrors the Sketchfab web viewer:
  varint (+ zigzag) → triangle_mode → prediction → dequant → normals(nphi)
"""

from __future__ import annotations

import json
import math
import re
import struct
from pathlib import Path
from typing import Any, Optional

# Attribute flags in UserData "attributes"
ATTR = {
    "Vertex": 1,
    "Normal": 2,
    "TexCoord": 4,
    "Color": 8,
    "Triangle": 16,
    "Tangent": 32,
}

MODE_QUANT = 1  # dequant via bbl/h
MODE_PARALLELOGRAM = 2

TRI_DELTA = 1
TRI_HIGHWATER = 2
TRI_IMPLICIT = 4

IMPLICIT_HEADER_PRIMITIVE_LENGTH = 0
IMPLICIT_HEADER_MASK_LENGTH = 1
IMPLICIT_HEADER_EXPECTED_INDEX = 2
IMPLICIT_HEADER_LENGTH = 3


def _zigzag_decode(n: int) -> int:
    return (n >> 1) ^ (-(n & 1))


def _to_i32(n: int) -> int:
    """Match JS ToInt32."""
    n &= 0xFFFFFFFF
    if n >= 0x80000000:
        n -= 0x100000000
    return n


def _to_u32(n: int) -> int:
    return n & 0xFFFFFFFF


def decode_varint(buf: bytes | memoryview, count: int, type_name: str) -> list[int]:
    """JS-compatible varint reader (H+3W). Bit ops are 32-bit like JS."""
    out = [0] * count
    o = 0
    signed = not type_name.startswith("U")
    for a in range(count):
        s = 0
        shift = 0
        while True:
            if o >= len(buf):
                raise ValueError(f"varint overflow at element {a}/{count}")
            b = buf[o]
            o += 1
            s |= (b & 127) << shift
            # JS bitwise is 32-bit
            s = _to_u32(s)
            shift += 7
            if (b & 128) == 0:
                break
            if shift > 35:
                raise ValueError("varint too long")
        if signed:
            # zigzag with 32-bit arithmetic shift semantics
            c = _to_i32(s)
            out[a] = (c >> 1) ^ (-(c & 1))
        else:
            out[a] = s
    return out


def delta_decode(values: list[int], start: int = 0) -> list[int]:
    """JS function a: prefix-sum of zigzag deltas (32-bit)."""
    if start >= len(values):
        return values
    r = _to_i32(int(values[start]))
    values[start] = r
    for i in range(start + 1, len(values)):
        o = _to_i32(int(values[i]))
        # o>>1 ^ -(o&1) with JS 32-bit shifts
        delta = (o >> 1) ^ (-(o & 1))
        r = _to_i32(r + delta)
        values[i] = r
    return values


def highwater_decode(values: list[int], state: list[int]) -> list[int]:
    """JS function h."""
    n = int(state[0])
    out = [0] * len(values)
    for a, e in enumerate(values):
        o = n - int(e)
        out[a] = o
        if n <= o:
            n = o + 1
    state[0] = n
    return out


def implicit_strip_decode(
    src: list[int], prim_len: int, data_index: int, use_watermark_seed: bool
) -> list[int]:
    """
    Expand implicit triangle-strip indices (viewer function o/s).

    Critical JS loop semantics:
      for (c=s[u], h=32*u, d=(last?leftover:0); d<32; ++d, ++h)
        t[h] = (c & (-2147483648 >>> d)) ? e[i++] : (n ? r : r++);

    h starts at 32*u even when d starts at leftover (bits below leftover are skipped).
    """
    expected = int(src[IMPLICIT_HEADER_EXPECTED_INDEX])
    mask_len = int(src[IMPLICIT_HEADER_MASK_LENGTH])
    masks = [
        _to_u32(int(x))
        for x in src[IMPLICIT_HEADER_LENGTH : IMPLICIT_HEADER_LENGTH + mask_len]
    ]
    leftover = 32 * mask_len - prim_len
    dest = [0] * prim_len
    i = data_index
    r = expected
    for u in range(mask_len):
        c = masks[u]
        d_start = leftover if u == mask_len - 1 else 0
        h = 32 * u
        for d in range(d_start, 32):
            if h >= prim_len:
                break
            # JS: c & ((-2147483648) >>> d)
            bit_sel = _to_u32(0x80000000 >> d) if d < 32 else 0
            # In Python >> on 0x80000000 works as unsigned for d>0 if we mask:
            bit_sel = (0x80000000 >> d) & 0xFFFFFFFF
            if c & bit_sel:
                dest[h] = int(src[i])
                i += 1
            else:
                dest[h] = r
                if not use_watermark_seed:
                    r += 1
            h += 1
    return dest


def parallelogram_predict(values: list[int], item_size: int, indices: list[int]) -> list[int]:
    n_verts = len(values) // item_size
    if n_verts <= 0 or len(indices) < 3:
        return values
    seen = [0] * n_verts
    for k in range(3):
        if 0 <= indices[k] < n_verts:
            seen[indices[k]] = 1
    a = len(indices) - 1
    for o in range(2, a):
        s = o - 2
        l_i, u_i, c_i, h_i = indices[s], indices[s + 1], indices[s + 2], indices[s + 3]
        if not (0 <= h_i < n_verts):
            continue
        if seen[h_i] == 1:
            continue
        if not (0 <= l_i < n_verts and 0 <= u_i < n_verts and 0 <= c_i < n_verts):
            continue
        seen[h_i] = 1
        bl, bu, bc, bh = l_i * item_size, u_i * item_size, c_i * item_size, h_i * item_size
        for d in range(item_size):
            values[bh + d] = values[bh + d] + values[bu + d] + values[bc + d] - values[bl + d]
    return values


def dequant(values: list[int], bbl: list[float], step: list[float], item_size: int) -> list[float]:
    out = [0.0] * len(values)
    n = len(values) // item_size
    for o in range(n):
        s = o * item_size
        for l in range(item_size):
            out[s + l] = bbl[l] + float(values[s + l]) * step[l]
    return out


def decode_normals(
    packed: list[int],
    item_size_out: int,
    epsilon: float = 0.25,
    nphi: int = 720,
    is_tangent: bool = False,
) -> list[float]:
    pi = math.pi
    u = math.cos(math.radians(epsilon))
    d = pi / max(nphi - 1, 1)
    g = (math.pi / 2) / max(nphi - 1, 1)
    f = 3 if is_tangent else 2
    p = len(packed) // f
    out = [0.0] * (p * item_size_out)
    for c in range(p):
        v = c * item_size_out
        base = c * f
        S = int(packed[base])
        x = int(packed[base + 1])
        if item_size_out == 4 and not is_tangent:
            out[v + 3] = -1.0 if (S & 1024) else 1.0
            S &= ~1024
        A = S * d
        R = math.cos(A)
        w = math.sin(A)
        A2 = A + g
        denom = max(1e-5, w * math.sin(A2))
        E = (u - R * math.cos(A2)) / denom
        E = max(-1.0, min(1.0, E))
        P = (2 * math.pi) * x / max(1.0, math.ceil(pi / max(1e-5, math.acos(E))))
        C = w * math.cos(P)
        T = w * math.sin(P)
        M = R
        if is_tangent and f >= 3:
            N = 4.7938362584151635e-5 * float(packed[base + 2])
            O = math.sin(N)
            out[v] = O * C
            out[v + 1] = O * T
            out[v + 2] = O * M
            out[v + 3] = math.cos(N)
        else:
            out[v] = C
            out[v + 1] = T
            out[v + 2] = M
    return out


def parse_user_data(udc: Optional[dict]) -> dict[str, Any]:
    if not udc:
        return {}
    out: dict[str, Any] = {}
    special = {
        "wireframe", "epsilon", "nphi", "attributes", "vertex_mode", "triangle_mode",
        "rigIndex", "framerate", "bx", "by", "bz", "hx", "hy", "hz",
        "ox", "oy", "oz", "ow", "ot", "channel_mode",
    }
    for item in udc.get("Values") or []:
        name = item.get("Name")
        val = item.get("Value")
        if name is None:
            continue
        if (
            name in special
            or "_bbl_" in name
            or "_h_" in name
            or name.endswith("_mode")
            or name.endswith("_bits")
        ):
            try:
                out[name] = json.loads(val)
            except Exception:
                try:
                    out[name] = float(val)
                except Exception:
                    out[name] = val
        else:
            out[name] = val
    return out


def _attr_flag(name: str) -> int:
    if "TexCoord" in name:
        return ATTR["TexCoord"]
    return ATTR.get(name, 0)


def _get_array_desc(node: dict) -> tuple[str, dict]:
    arr = node.get("Array") or {}
    if not arr:
        raise ValueError("empty Array descriptor")
    type_name = next(iter(arr.keys()))
    return type_name, arr[type_name]


def read_buffer_array(
    bin_files: dict[str, bytes],
    node: dict,
    *,
    element_count_mode: str = "components",
) -> tuple[list, str, int]:
    """
    Read a BufferArray-like node.

    Viewer uses count = ItemSize * Size for attribute streams.
    In Sketchfab JSON, Size is typically the vertex/element count for attributes,
    so total scalars = ItemSize * Size.
    For indices ItemSize is 1 → Size values.
    """
    item_size = int(node.get("ItemSize") or 1)
    tname, meta = _get_array_desc(node)
    offset = int(meta.get("Offset") or 0)
    size = int(meta.get("Size") or 0)
    fname = meta.get("File") or "model_file.bin"
    encoding = meta.get("Encoding")
    # Resolve decrypted buffers even if osgjs still says .binz / .bin.gz
    data = bin_files.get(fname) or bin_files.get(Path(fname).name)
    if data is None:
        alt = Path(fname).name
        for cand in (
            alt,
            alt.replace(".binz", ".bin"),
            alt.replace(".bin.gz", ".bin"),
            alt.replace(".gz", ""),
            re.sub(r"\.binz$", ".bin", alt),
            re.sub(r"\.bin\.gz$", ".bin", alt),
        ):
            if cand in bin_files:
                data = bin_files[cand]
                break
    if data is None:
        for k, v in bin_files.items():
            if Path(k).name.split(".")[0] == Path(fname).name.split(".")[0] and "wireframe" not in k:
                # model_file* match
                if Path(k).stem.startswith(Path(fname).stem.split(".")[0][:10]):
                    data = v
                    break
    if data is None:
        raise FileNotFoundError(f"Binary buffer not found: {fname}")

    # Match viewer: count = itemSize * Size
    count = size * item_size if element_count_mode == "viewer" else size
    if count <= 0:
        return [], tname, item_size

    if encoding == "varint":
        values = decode_varint(memoryview(data)[offset:], count, tname)
        return values, tname, item_size

    nbytes = {
        "Uint8Array": 1, "Int8Array": 1,
        "Uint16Array": 2, "Int16Array": 2,
        "Uint32Array": 4, "Int32Array": 4,
        "Float32Array": 4,
    }.get(tname)
    if nbytes is None:
        raise ValueError(f"Unsupported array type {tname}")
    raw = data[offset : offset + count * nbytes]
    if len(raw) < count * nbytes:
        # fallback: Size already means total components
        count = size
        raw = data[offset : offset + count * nbytes]
    fmt = {
        "Uint8Array": "B", "Int8Array": "b",
        "Uint16Array": "H", "Int16Array": "h",
        "Uint32Array": "I", "Int32Array": "i",
        "Float32Array": "f",
    }[tname]
    values = list(struct.unpack("<" + fmt * (len(raw) // nbytes), raw[: (len(raw) // nbytes) * nbytes]))
    return values, tname, item_size


def decode_indices(
    prim: dict,
    user: dict,
    bin_files: dict[str, bytes],
) -> tuple[list[int], str]:
    key = next(k for k in prim if k.startswith("DrawElements") or k.startswith("DrawArrays"))
    pe = prim[key]
    mode = pe.get("Mode") or "TRIANGLES"

    if key.startswith("DrawArrays"):
        first = int(pe.get("First") or 0)
        count = int(pe.get("Count") or 0)
        return list(range(first, first + count)), mode

    indices_node = pe.get("Indices") or {}
    # indices: ItemSize usually 1, Size = stream length
    values, tname, item_size = read_buffer_array(
        bin_files, indices_node, element_count_mode="viewer"
    )
    values = [int(v) for v in values]

    attrs = int(user.get("attributes") or 0)
    tri_mode = int(user.get("triangle_mode") or 0)
    is_strip = mode == "TRIANGLE_STRIP"

    if (attrs & ATTR["Triangle"]) == 0 or (not is_strip and mode != "TRIANGLES"):
        return values, mode

    b = 0
    if (tri_mode & TRI_IMPLICIT) and is_strip:
        b = IMPLICIT_HEADER_LENGTH + int(values[IMPLICIT_HEADER_MASK_LENGTH])
        prim_len = int(values[IMPLICIT_HEADER_PRIMITIVE_LENGTH])
    else:
        prim_len = len(values)

    if tri_mode & TRI_DELTA:
        delta_decode(values, b)

    if (tri_mode & TRI_IMPLICIT) and is_strip:
        values = implicit_strip_decode(
            values, prim_len, b, use_watermark_seed=bool(tri_mode & TRI_HIGHWATER)
        )

    if tri_mode & TRI_HIGHWATER:
        values = highwater_decode(values, [0])

    return [int(v) for v in values], mode


def strip_to_triangles(indices: list[int]) -> list[int]:
    """
    Expand GL TRIANGLE_STRIP to TRIANGLES.
    Winding flips every index (including over degenerates), matching GPU behaviour.
    """
    tris: list[int] = []
    n = len(indices)
    for i in range(n - 2):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        if a == b or b == c or a == c:
            continue
        if (i & 1) == 0:
            tris.extend((a, b, c))
        else:
            tris.extend((a, c, b))
    return tris


def recalc_vertex_normals(positions: list[float], indices: list[int]) -> list[float]:
    """Area-weighted smooth normals from triangle soup (fixes bad packed normals)."""
    n_verts = len(positions) // 3
    acc = [0.0] * (n_verts * 3)
    for i in range(0, len(indices) - 2, 3):
        ia, ib, ic = indices[i], indices[i + 1], indices[i + 2]
        if min(ia, ib, ic) < 0 or max(ia, ib, ic) >= n_verts:
            continue
        ax, ay, az = positions[ia * 3 : ia * 3 + 3]
        bx, by, bz = positions[ib * 3 : ib * 3 + 3]
        cx, cy, cz = positions[ic * 3 : ic * 3 + 3]
        abx, aby, abz = bx - ax, by - ay, bz - az
        acx, acy, acz = cx - ax, cy - ay, cz - az
        nx = aby * acz - abz * acy
        ny = abz * acx - abx * acz
        nz = abx * acy - aby * acx
        for vi in (ia, ib, ic):
            acc[vi * 3] += nx
            acc[vi * 3 + 1] += ny
            acc[vi * 3 + 2] += nz
    for vi in range(n_verts):
        x, y, z = acc[vi * 3], acc[vi * 3 + 1], acc[vi * 3 + 2]
        ln = math.sqrt(x * x + y * y + z * z)
        if ln > 1e-12:
            acc[vi * 3] = x / ln
            acc[vi * 3 + 1] = y / ln
            acc[vi * 3 + 2] = z / ln
        else:
            acc[vi * 3 : vi * 3 + 3] = [0.0, 1.0, 0.0]
    return acc


def filter_spike_tris(
    positions: list[float], indices: list[int], *, max_edge_factor: float = 12.0
) -> list[int]:
    """
    Drop triangles with an edge much longer than the mesh median edge.
    Removes rare decode spikes without harming normal topology.
    """
    if len(indices) < 3 or len(positions) < 9:
        return indices
    # sample edges for median
    samples: list[float] = []
    step = max(3, (len(indices) // 3) // 2000 * 3)  # limit work
    for i in range(0, len(indices) - 2, step):
        for a, b in (
            (indices[i], indices[i + 1]),
            (indices[i + 1], indices[i + 2]),
            (indices[i + 2], indices[i]),
        ):
            ax, ay, az = positions[a * 3 : a * 3 + 3]
            bx, by, bz = positions[b * 3 : b * 3 + 3]
            dx, dy, dz = ax - bx, ay - by, az - bz
            samples.append(math.sqrt(dx * dx + dy * dy + dz * dz))
    if not samples:
        return indices
    samples.sort()
    med = samples[len(samples) // 2] or 1e-6
    limit = med * max_edge_factor
    out: list[int] = []
    for i in range(0, len(indices) - 2, 3):
        tri = indices[i : i + 3]
        ok = True
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            ax, ay, az = positions[a * 3 : a * 3 + 3]
            bx, by, bz = positions[b * 3 : b * 3 + 3]
            dx, dy, dz = ax - bx, ay - by, az - bz
            if math.sqrt(dx * dx + dy * dy + dz * dz) > limit:
                ok = False
                break
        if ok:
            out.extend(tri)
    return out if len(out) >= 3 else indices


def weld_mesh_by_position(
    positions: list[float],
    indices: list[int],
    uvs: Optional[list[float]] = None,
    *,
    eps: float = 1e-5,
) -> tuple[list[float], list[int], Optional[list[float]]]:
    """
    Merge vertices that share the same position (UV islands).

    Sketchfab meshes are heavily UV-split: same XYZ appears many times with
    different UVs. Exporting without welding leaves false boundary edges and
    can look like a 'sieve' in some DCC tools. We keep one UV per welded vertex
    (first seen) — good enough for solid shading; textures may soft-seam.
    """
    n_verts = len(positions) // 3
    if n_verts == 0:
        return positions, indices, uvs

    key_to_new: dict[tuple[int, int, int], int] = {}
    remap = [0] * n_verts
    new_pos: list[float] = []
    new_uv: Optional[list[float]] = [] if uvs is not None else None

    inv = 1.0 / eps
    for i in range(n_verts):
        x, y, z = positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]
        key = (int(round(x * inv)), int(round(y * inv)), int(round(z * inv)))
        if key not in key_to_new:
            key_to_new[key] = len(new_pos) // 3
            new_pos.extend((x, y, z))
            if new_uv is not None and uvs is not None and len(uvs) >= (i + 1) * 2:
                new_uv.extend((uvs[i * 2], uvs[i * 2 + 1]))
            elif new_uv is not None:
                new_uv.extend((0.0, 0.0))
        remap[i] = key_to_new[key]

    new_idx: list[int] = []
    for i in range(0, len(indices) - 2, 3):
        a, b, c = remap[indices[i]], remap[indices[i + 1]], remap[indices[i + 2]]
        if a == b or b == c or a == c:
            continue
        new_idx.extend((a, b, c))
    if len(new_idx) < 3:
        return positions, indices, uvs
    return new_pos, new_idx, new_uv


def is_transparent_geom(geom: dict) -> bool:
    ss = geom.get("StateSet") or {}
    if "osg.StateSet" in ss:
        ss = ss["osg.StateSet"]
    hint = str(ss.get("RenderingHint") or "")
    if "TRANSPARENT" in hint.upper():
        return True
    for entry in ss.get("AttributeList") or []:
        if isinstance(entry, dict) and "osg.BlendFunc" in entry:
            return True
    return False


def make_double_sided(indices: list[int]) -> list[int]:
    """Append flipped winding of every triangle (for cards/transparent shells)."""
    out = list(indices)
    for i in range(0, len(indices) - 2, 3):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        out.extend((a, c, b))
    return out


def decode_vertex_attr(
    name: str,
    node: dict,
    user: dict,
    bin_files: dict[str, bytes],
    indices_for_predict: Optional[list[int]] = None,
) -> tuple[list[float], int]:
    attrs = int(user.get("attributes") or 0)
    flag = _attr_flag(name)
    values, tname, item_size = read_buffer_array(
        bin_files, node, element_count_mode="viewer"
    )
    values_list = list(values)

    if name == "Normal" and (attrs & ATTR["Normal"]):
        epsilon = float(user.get("epsilon") or 0.25)
        nphi = int(user.get("nphi") or 720)
        out = decode_normals([int(x) for x in values_list], 3, epsilon, nphi, False)
        return out, 3

    if name == "Tangent" and (attrs & ATTR["Tangent"]):
        epsilon = float(user.get("epsilon") or 0.25)
        nphi = int(user.get("nphi") or 720)
        out = decode_normals([int(x) for x in values_list], 4, epsilon, nphi, True)
        return out, 4

    if name == "Color":
        # often uint8 0..255
        if tname == "Uint8Array":
            return [float(x) / 255.0 for x in values_list], item_size
        return [float(x) for x in values_list], item_size

    if name == "Vertex":
        mode = int(user.get("vertex_mode") or 0)
        prefix = "vtx_"
    elif name.startswith("TexCoord"):
        idx = name.replace("TexCoord", "")
        mode_key = f"uv_{idx}_mode"
        mode = int(
            user[mode_key]
            if mode_key in user and user[mode_key] is not None
            else user.get("vertex_mode") or 0
        )
        prefix = f"uv_{idx}_"
    else:
        return [float(x) for x in values_list], item_size

    if (attrs & flag) == 0:
        return [float(x) for x in values_list], item_size

    ints = [int(x) for x in values_list]
    if (mode & MODE_PARALLELOGRAM) and indices_for_predict:
        parallelogram_predict(ints, item_size, indices_for_predict)

    if mode & MODE_QUANT:
        bbl_key = prefix + "bbl_x"
        if bbl_key in user:
            bbl = [float(user.get(prefix + "bbl_x") or 0.0), float(user.get(prefix + "bbl_y") or 0.0)]
            step = [float(user.get(prefix + "h_x") or 1.0), float(user.get(prefix + "h_y") or 1.0)]
            if item_size >= 3:
                bbl.append(float(user.get(prefix + "bbl_z") or 0.0))
                step.append(float(user.get(prefix + "h_z") or 1.0))
            while len(bbl) < item_size:
                bbl.append(0.0)
                step.append(1.0)
            return dequant(ints, bbl, step, item_size), item_size

    return [float(x) for x in ints], item_size


def _load_bins(work_dir: Path) -> dict[str, bytes]:
    """Load .bin buffers and alias .binz / .bin.gz names to decrypted .bin."""
    bins: dict[str, bytes] = {}
    for p in work_dir.iterdir():
        if p.suffix.lower() == ".bin" and p.is_file():
            data = p.read_bytes()
            bins[p.name] = data
            # aliases used inside file.osgjs before/after rename
            bins[p.name + "z"] = data  # model_file.binz
            bins[p.stem + ".binz"] = data
            bins[p.name + ".gz"] = data
            bins[p.stem + ".bin.gz"] = data
    return bins


def _is_wireframe_geom(g: dict) -> bool:
    """Skip wireframe helper meshes (lines as triangles = 'sieve' artifacts)."""
    # File refs
    blob = json.dumps(g, ensure_ascii=False)
    if "wireframe" in blob.lower():
        return True
    valist = g.get("VertexAttributeList") or {}
    for node in valist.values():
        if not isinstance(node, dict):
            continue
        arr = node.get("Array") or {}
        for meta in arr.values():
            if isinstance(meta, dict):
                f = str(meta.get("File") or "").lower()
                if "wireframe" in f:
                    return True
    return False


IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
# Sketchfab's viewer scene is Z-up (Y-up uploads get a GLTF_SceneRootNode rotation);
# glTF is Y-up: (x, y, z) -> (x, z, -y)
Z_UP_TO_Y_UP = [1.0, 0, 0, 0, 0, 0, -1.0, 0, 0, 1.0, 0, 0, 0, 0, 0, 1.0]


def _mat_mul(a: list[float], b: list[float]) -> list[float]:
    """Column-major 4x4 multiply (osgjs and glTF both store column-major)."""
    return [
        sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
        for c in range(4)
        for r in range(4)
    ]


def _walk_geometries(
    node: Any,
    out: list[dict],
    *,
    _seen: Optional[set] = None,
    ctx: Optional[dict] = None,
    _mat: Optional[list[float]] = None,
    _ss: Any = None,
    _xform_name: Optional[str] = None,
    _local: Optional[list[float]] = None,
    _parent_mat: Optional[list[float]] = None,
) -> None:
    """Collect unique renderable geometries; prefer RigGeometry.SourceGeometry once.

    ctx (optional) receives per-geometry context keyed by id(geom):
      ctx["matrix"][id] — accumulated MatrixTransform (column-major)
      ctx["stateset"][id] — nearest StateSet (own or inherited from a parent node)
      ctx["xform"][id] — nearest MatrixTransform name, its local matrix, and the
        parent matrix above it (so animation can target that node)
    """
    if _seen is None:
        _seen = set()

    def note(geom: dict) -> None:
        if ctx is not None:
            ctx.setdefault("matrix", {})[id(geom)] = _mat
            ctx.setdefault("stateset", {})[id(geom)] = geom.get("StateSet") or _ss
            ctx.setdefault("xform", {})[id(geom)] = {
                "name": _xform_name or "",
                "local": list(_local) if _local else None,
                "parent": list(_parent_mat) if _parent_mat else None,
            }

    def walk(v: Any) -> None:
        _walk_geometries(
            v, out, _seen=_seen, ctx=ctx, _mat=_mat, _ss=_ss,
            _xform_name=_xform_name, _local=_local, _parent_mat=_parent_mat,
        )

    if isinstance(node, dict):
        m = node.get("Matrix")
        if isinstance(m, list) and len(m) == 16 and "Children" in node:
            local = [float(x) for x in m]
            _parent_mat = list(_mat) if _mat else None
            _local = local
            if node.get("Name"):
                _xform_name = str(node["Name"])
            _mat = _mat_mul(_mat, local) if _mat else local
        if node.get("StateSet") and "VertexAttributeList" not in node:
            _ss = node["StateSet"]

        if "osg.RigGeometry" in node:
            rg = node["osg.RigGeometry"]
            src = rg.get("SourceGeometry")
            geom = None
            if isinstance(src, dict) and "osg.Geometry" in src:
                geom = src["osg.Geometry"]
            elif isinstance(src, dict):
                for k, v in src.items():
                    if k.startswith("osg.") and isinstance(v, dict) and v.get("VertexAttributeList"):
                        geom = v
                        break
            if geom is not None:
                gid = id(geom)
                if gid not in _seen and not _is_wireframe_geom(geom):
                    _seen.add(gid)
                    out.append(geom)
                    note(geom)
                    if ctx is not None and not geom.get("StateSet") and rg.get("StateSet"):
                        ctx["stateset"][gid] = rg["StateSet"]
            # still walk non-source children of rig (animations etc.) but skip re-adding source
            for k, v in rg.items():
                if k == "SourceGeometry":
                    continue
                walk(v)
            for k, v in node.items():
                if k != "osg.RigGeometry":
                    walk(v)
            return

        if "osg.Geometry" in node:
            geom = node["osg.Geometry"]
            gid = id(geom)
            if gid not in _seen and geom.get("VertexAttributeList") and not _is_wireframe_geom(geom):
                _seen.add(gid)
                out.append(geom)
                note(geom)

        for v in node.values():
            walk(v)
    elif isinstance(node, list):
        for v in node:
            walk(v)


def _extract_diffuse_texture(geom: dict) -> Optional[str]:
    """Return texture File path from StateSet if present."""
    ss = geom.get("StateSet") or {}
    if "osg.StateSet" in ss:
        ss = ss["osg.StateSet"]
    tal = ss.get("TextureAttributeList") or []
    for unit in tal:
        if not isinstance(unit, list):
            unit = [unit]
        for entry in unit:
            if not isinstance(entry, dict):
                continue
            tex = entry.get("osg.Texture") or entry.get("osg.Texture2D")
            if isinstance(tex, dict) and tex.get("File"):
                return str(tex["File"])
    # AttributeList material name only — no file
    return None


def _extract_material_name(geom: dict, fallback: str) -> str:
    ss = geom.get("StateSet") or {}
    if "osg.StateSet" in ss:
        ss = ss["osg.StateSet"]
    for entry in ss.get("AttributeList") or []:
        if isinstance(entry, dict) and "osg.Material" in entry:
            mat = entry["osg.Material"]
            if mat.get("Name"):
                return str(mat["Name"])
    return fallback


def _rel_tex(path: Path, work_dir: Path) -> str:
    try:
        return str(path.relative_to(work_dir)).replace("\\", "/")
    except ValueError:
        return path.name


def _resolve_texture_file(
    tex_ref: str,
    work_dir: Path,
    tex_map: Optional[dict[str, Path]],
    *,
    material_name: str = "",
) -> Optional[str]:
    """Return path relative to work_dir for MTL map_Kd, or None."""
    candidates: list[Path] = []
    tex_ref = (tex_ref or "").replace("\\", "/")
    uid_m = re.search(r"([a-f0-9]{32})", (tex_ref or "").replace("\\", "/"))

    # An exact viewer image must win over API variants and name heuristics.
    if tex_ref:
        direct = work_dir / tex_ref
        if direct.is_file():
            return _rel_tex(direct, work_dir)
    if tex_map:
        if tex_ref in tex_map and Path(tex_map[tex_ref]).is_file():
            return _rel_tex(Path(tex_map[tex_ref]), work_dir)
        if uid_m and uid_m.group(1) in tex_map:
            candidates.append(Path(tex_map[uid_m.group(1)]))
        base = Path(tex_ref).name if tex_ref else ""
        if base and base in tex_map:
            candidates.append(Path(tex_map[base]))
        if uid_m:
            for k, p in tex_map.items():
                if uid_m.group(1) in k:
                    candidates.append(Path(p))

    tex_dir = work_dir / "textures"
    if tex_dir.is_dir():
        if uid_m:
            for p in tex_dir.glob(f"{uid_m.group(1)}*"):
                candidates.append(p)
    for p in candidates:
        if p.is_file():
            return _rel_tex(p, work_dir)

    # Use names only after all identity-based lookups failed.
    candidates = []
    if tex_dir.is_dir():
        # Heuristic: material name → diffuse map (*_D*, *Diffuse*, *Albedo*, *BaseColor*)
        mat = re.sub(r"[^a-z0-9]+", "", (material_name or "").lower())
        if mat:
            for p in tex_dir.iterdir():
                if not p.is_file():
                    continue
                n = p.name.lower()
                if mat[:8] and mat[:8] in re.sub(r"[^a-z0-9]+", "", n):
                    if any(tag in n for tag in ("_d.", "_d_", "diffuse", "albedo", "basecolor", "_a.")):
                        candidates.insert(0, p)
                    else:
                        candidates.append(p)

    for p in candidates:
        if p.is_file():
            return _rel_tex(p, work_dir)
    return None


def _filter_tris(indices: list[int], n_verts: int) -> list[int]:
    """Drop triangles with out-of-range indices (prevents Blender IndexError)."""
    out: list[int] = []
    for i in range(0, len(indices) - 2, 3):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        if min(a, b, c) < 0 or max(a, b, c) >= n_verts:
            continue
        if a == b or b == c or a == c:
            continue
        out.extend([a, b, c])
    return out


# ---------------------------------------------------------------------------
# Materials (PBR) — StateSet texture units → glTF metallic-roughness
# ---------------------------------------------------------------------------
#
# Sketchfab keeps one texture per StateSet "TextureAttributeList" unit. Which
# unit plays which role is stored in the StateSet UserData as s<Role>=<unit>
# (sDiffuse=1, sMetallic=7, sRoughness=8 …). Each unit samples its own UV set
# TexCoord<unit>, quantized and then mapped by the texture's offset/scale.
# Models without s* hints fall back to /i/models/{uid}/options materials
# (channel → texture uid), then to "first texture = base color".

_ROLE_HINTS = {
    "diffuse": "base", "albedo": "base", "basecolor": "base", "color": "base",
    "metallic": "metal", "metalness": "metal",
    "roughness": "rough", "glossiness": "gloss",
    "normal": "normal", "normalmap": "normal",
    "emissive": "emissive", "emission": "emissive", "emit": "emissive",
    "occlusion": "ao", "ao": "ao", "ambientocclusion": "ao",
    "opacity": "opacity", "alpha": "opacity",
}

_OPTIONS_CHANNELS = {
    "AlbedoPBR": "base", "DiffuseColor": "base", "DiffusePBR": "base",
    "MetalnessPBR": "metal", "RoughnessPBR": "rough", "GlossinessPBR": "gloss",
    "NormalMap": "normal", "EmitColor": "emissive", "AOPBR": "ao", "Opacity": "opacity",
}

_WRAP = {"REPEAT": 10497, "CLAMP_TO_EDGE": 33071, "CLAMP": 33071, "MIRRORED_REPEAT": 33648}
_FILTER = {
    "NEAREST": 9728, "LINEAR": 9729,
    "NEAREST_MIPMAP_NEAREST": 9984, "LINEAR_MIPMAP_NEAREST": 9985,
    "NEAREST_MIPMAP_LINEAR": 9986, "LINEAR_MIPMAP_LINEAR": 9987,
}


def _ud_map(container: Optional[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in (container or {}).get("Values") or []:
        if isinstance(item, dict) and item.get("Name") is not None:
            out[str(item["Name"])] = str(item.get("Value"))
    return out


def _json_or(val: Optional[str], default: Any) -> Any:
    if val is None:
        return default
    try:
        return json.loads(val)
    except Exception:
        return default


def _index_statesets(scene: Any) -> dict[int, dict]:
    """UniqueID → full StateSet (later references carry only {"UniqueID": n})."""
    index: dict[int, dict] = {}

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            ss = n.get("osg.StateSet")
            if isinstance(ss, dict) and len(ss) > 1 and "UniqueID" in ss:
                index.setdefault(int(ss["UniqueID"]), ss)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(scene)
    return index


def _resolve_stateset(ref: Any, index: dict[int, dict]) -> dict:
    if not isinstance(ref, dict):
        return {}
    ss = ref.get("osg.StateSet", ref)
    if isinstance(ss, dict) and len(ss) == 1 and "UniqueID" in ss:
        return index.get(int(ss["UniqueID"]), ss)
    return ss if isinstance(ss, dict) else {}


def _index_textures(scene: Any) -> dict[int, dict]:
    """Resolve shared osg.Texture objects, including definitions in other StateSets."""
    index: dict[int, dict] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for kind in ("osg.Texture", "osg.Texture2D"):
                tex = node.get(kind)
                if isinstance(tex, dict) and tex.get("File") and "UniqueID" in tex:
                    index.setdefault(int(tex["UniqueID"]), tex)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(scene)
    return index


def _texture_units(ss: dict, texture_index: Optional[dict[int, dict]] = None) -> dict[int, dict]:
    units: dict[int, dict] = {}
    for unit, entries in enumerate(ss.get("TextureAttributeList") or []):
        if not isinstance(entries, list):
            entries = [entries]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            tex = entry.get("osg.Texture") or entry.get("osg.Texture2D")
            if isinstance(tex, dict) and not tex.get("File") and "UniqueID" in tex:
                tex = (texture_index or {}).get(int(tex["UniqueID"]), tex)
            if isinstance(tex, dict) and tex.get("File"):
                units[unit] = tex
                break
    return units


def _tex_uid(tex: dict) -> Optional[str]:
    m = re.search(r"([a-f0-9]{32})", str(tex.get("File") or ""))
    return m.group(1) if m else None


def _options_material(
    materials: Optional[dict], ss_ud: dict[str, str], tex_uids: set[str]
) -> Optional[dict]:
    if not materials:
        return None
    mats = [m for m in materials.values() if isinstance(m, dict) and m.get("channels")]
    sid = ss_ud.get("UniqueID")
    for m in mats:
        if sid is not None and str(m.get("stateSetID")) == sid:
            return m
    for m in mats:
        for ch in m["channels"].values():
            if ((ch or {}).get("texture") or {}).get("uid") in tex_uids:
                return m
    return None


def _slot_from_channel(
    ch: dict,
    work_dir: Path,
    tex_map: Optional[dict[str, Path]],
) -> Optional[dict]:
    """Texture slot for an options channel that has no osgjs File.

    Older models keep the image only in ``/i/models/{uid}/options``. The mesh
    still has TexCoordN; the channel says which unit and which texture uid.
    """
    tex = (ch or {}).get("texture") or {}
    tuid = str(tex.get("uid") or "")
    if not ch.get("enable") or len(tuid) < 8:
        return None
    path = None
    if tex_map and tuid in tex_map:
        path = Path(tex_map[tuid])
    else:
        rel = _resolve_texture_file(tuid, work_dir, tex_map)
        path = (work_dir / rel) if rel else None
    if path is None or not path.is_file():
        return None
    try:
        unit = int(tex.get("texCoordUnit") or 0)
    except (TypeError, ValueError):
        unit = 0
    return {
        "unit": unit,
        "file": path,
        "channel": None,
        "scale": [1.0, 1.0],
        "offset": [0.0, 0.0],
        "wrap": (
            _WRAP.get(str(tex.get("wrapS") or "REPEAT"), 10497),
            _WRAP.get(str(tex.get("wrapT") or "REPEAT"), 10497),
        ),
        "mag": _FILTER.get(str(tex.get("magFilter") or "LINEAR"), 9729),
        "min": _FILTER.get(str(tex.get("minFilter") or "LINEAR_MIPMAP_LINEAR"), 9987),
    }


def build_material(
    ss: dict,
    work_dir: Path,
    tex_map: Optional[dict[str, Path]],
    materials: Optional[dict],
    fallback_name: str,
    *,
    texture_index: Optional[dict[int, dict]] = None,
) -> dict:
    """Describe a glTF material for a StateSet: factors + textures by role."""
    ud = _ud_map(ss.get("UserDataContainer"))
    units = _texture_units(ss, texture_index)
    uid_by_unit = {u: _tex_uid(t) for u, t in units.items()}

    roles: dict[str, int] = {}
    for name, val in ud.items():
        if len(name) > 1 and name[0] == "s" and name[1].isupper():
            role = _ROLE_HINTS.get(name[1:].lower())
            try:
                unit = int(float(val))
            except ValueError:
                continue
            if role and unit in units:
                roles.setdefault(role, unit)

    opt = _options_material(materials, ud, {u for u in uid_by_unit.values() if u})
    if opt:
        for ch_name, role in _OPTIONS_CHANNELS.items():
            ch = opt["channels"].get(ch_name) or {}
            tuid = (ch.get("texture") or {}).get("uid")
            if not ch.get("enable") or not tuid or role in roles:
                continue
            for unit, uid in uid_by_unit.items():
                if uid == tuid:
                    roles[role] = unit
                    break
    if not roles and units:
        roles["base"] = min(units)

    textures: dict[str, dict] = {}
    for role, unit in roles.items():
        tex = units[unit]
        uid = uid_by_unit.get(unit)
        # The path stored in osgjs is the viewer's atlas. A same-uid file from the
        # public texture API is a different image and must not replace it.
        rel_file = str(tex.get("File") or "").replace("\\", "/").lstrip("/")
        direct = work_dir / rel_file if rel_file else None
        path = None
        if direct is not None and direct.is_file():
            path = direct
        elif tex_map and uid and uid in tex_map:
            path = Path(tex_map[uid])
        else:
            rel = _resolve_texture_file(rel_file, work_dir, tex_map)
            path = work_dir / rel if rel else None
        if not path or not path.is_file():
            continue
        tud = _ud_map(tex.get("UserDataContainer"))
        textures[role] = {
            "unit": unit,
            "file": path,
            "channel": (tud.get("channels") or "").strip().upper() or None,
            "scale": [float(x) for x in _json_or(tud.get("scale"), [1.0, 1.0])],
            "offset": [float(x) for x in _json_or(tud.get("offset"), [0.0, 0.0])],
            "wrap": (
                _WRAP.get(str(tex.get("WrapS") or "REPEAT"), 10497),
                _WRAP.get(str(tex.get("WrapT") or "REPEAT"), 10497),
            ),
            "mag": _FILTER.get(str(tex.get("MagFilter") or "LINEAR"), 9729),
            "min": _FILTER.get(str(tex.get("MinFilter") or "LINEAR_MIPMAP_LINEAR"), 9987),
        }

    if opt:
        for ch_name, role in _OPTIONS_CHANNELS.items():
            if role in textures:
                continue
            slot = _slot_from_channel(
                (opt.get("channels") or {}).get(ch_name) or {}, work_dir, tex_map
            )
            if slot:
                textures[role] = slot

    def ch_factor(name: str, default: float) -> float:
        ch = (opt or {}).get("channels", {}).get(name) or {}
        try:
            return float(ch.get("factor", default))
        except (TypeError, ValueError):
            return default

    base = _json_or(ud.get("DiffuseColor"), None)
    if not isinstance(base, list) or len(base) < 3:
        col = ((opt or {}).get("channels", {}).get("AlbedoPBR") or {}).get("color")
        base = list(col) if isinstance(col, list) and len(col) >= 3 else [1.0, 1.0, 1.0]
    base = [float(x) for x in base[:4]] + [1.0] * (4 - len(base[:4]))
    base[3] *= float(_json_or(ud.get("OpacityFactor"), ch_factor("Opacity", 1.0)))

    has_pbr_hint = "MetallicFactor" in ud or (opt and "MetalnessPBR" in opt.get("channels", {}))
    metal = float(_json_or(ud.get("MetallicFactor"), ch_factor("MetalnessPBR", 1.0 if "metal" in textures else 0.0)))
    if not has_pbr_hint and "metal" not in textures:
        metal = 0.0  # legacy (non-PBR) materials are dielectric
    rough = float(_json_or(ud.get("RoughnessFactor"), ch_factor("RoughnessPBR", 1.0 if "rough" in textures else 0.6)))

    emissive = _json_or(ud.get("EmissiveColor"), None)
    if not isinstance(emissive, list) or len(emissive) < 3:
        emissive = [1.0, 1.0, 1.0] if "emissive" in textures else [0.0, 0.0, 0.0]
    efac = float(_json_or(ud.get("EmissiveFactor"), ch_factor("EmitColor", 1.0)))
    emissive = [min(1.0, float(c) * efac) for c in emissive[:3]]

    alpha_mode = str(ud.get("alphaMode") or "").upper()
    if alpha_mode not in ("OPAQUE", "MASK", "BLEND"):
        alpha_mode = "BLEND" if ("opacity" in textures or base[3] < 1.0) else "OPAQUE"

    name = _extract_material_name({"StateSet": ss}, fallback_name)
    if name == fallback_name and ss.get("Name"):
        name = str(ss["Name"])

    return {
        "name": name,
        "base_factor": base,
        "metal": metal,
        "rough": rough,
        "emissive": emissive,
        "alpha_mode": alpha_mode,
        "alpha_cutoff": float(_json_or(ud.get("alphaCutoff"), 0.5)),
        "double_sided": str(ud.get("doubleSided") or "").lower() == "true",
        "textures": textures,
    }


def _pil():
    try:
        from PIL import Image  # type: ignore

        return Image
    except Exception:
        return None


def _channel(img, name: Optional[str]):
    """Single-channel image: the requested channel, or luminance of a gray map."""
    bands = img.getbands()
    if name and name in bands:
        return img.getchannel(name)
    if len(bands) == 1:
        return img
    return img.convert("L")


def _bake_textures(mat: dict, out_dir: Path, key: str) -> dict[str, dict]:
    """Return glTF-ready image refs per slot: base, mr, normal, emissive, ao.

    Packs metal/rough (often shipped as separate grayscale maps) into one
    metallicRoughness image (G=roughness, B=metalness) and opacity into the
    base color alpha. Without Pillow those composites are skipped.
    """
    tex = mat["textures"]
    slots: dict[str, dict] = {}
    Image = _pil()
    out_dir.mkdir(parents=True, exist_ok=True)

    base = tex.get("base")
    if base:
        slots["base"] = dict(base)
    op = tex.get("opacity")
    if op and Image and mat["alpha_mode"] != "OPAQUE":
        try:
            from PIL import ImageChops

            with Image.open(op["file"]) as source:
                a = _channel(source, op["channel"] or "A").copy()
            if base:
                with Image.open(base["file"]) as source:
                    rgba = source.convert("RGBA")
                a = ImageChops.multiply(rgba.getchannel("A"), a.resize(rgba.size))
            else:
                # The material's baseColorFactor supplies the constant color.
                rgba = Image.new("RGBA", a.size, (255, 255, 255, 255))
            rgba.putalpha(a)
            dest = out_dir / f"{key}_baseColor.png"
            rgba.save(dest)
            slots["base"] = dict(base or op, file=dest)
        except Exception:
            pass

    metal, rough, gloss = tex.get("metal"), tex.get("rough"), tex.get("gloss")
    rough_src = rough or gloss
    if (metal or rough_src) and Image:
        try:
            imgs = {}
            if metal:
                imgs["B"] = _channel(Image.open(metal["file"]), metal["channel"] or "B")
            if rough_src:
                g = _channel(Image.open(rough_src["file"]), rough_src["channel"] or "G")
                if not rough and gloss:
                    g = g.point(lambda v: 255 - v)
                imgs["G"] = g
            size = max((im.size for im in imgs.values()), key=lambda s: s[0] * s[1])
            white = Image.new("L", size, 255)
            g = imgs["G"].resize(size) if "G" in imgs else white
            # glTF multiplies both channels by their material factors. A
            # missing map therefore needs 1, preserving the constant factor.
            b = imgs["B"].resize(size) if "B" in imgs else white
            dest = out_dir / f"{key}_metallicRoughness.png"
            Image.merge("RGB", (white, g, b)).save(dest)
            slots["mr"] = dict(rough_src or metal, file=dest)
        except Exception:
            pass

    for role in ("normal", "emissive"):
        if tex.get(role):
            slots[role] = dict(tex[role])

    ao = tex.get("ao")
    if ao:
        slots["ao"] = dict(ao)
        if Image and ao["channel"] and ao["channel"] != "R":
            try:
                dest = out_dir / f"{key}_occlusion.png"
                _channel(Image.open(ao["file"]), ao["channel"]).save(dest)
                slots["ao"]["file"] = dest
            except Exception:
                pass
    return slots



def convert_osgjs(
    work_dir: Path,
    model_name: str = "model",
    *,
    prefer: str = "gltf",
    texture_map: Optional[dict[str, Path]] = None,
    materials: Optional[dict] = None,
    weld: bool = False,
    double_sided: bool = False,
) -> Path:
    """
    Convert file.osgjs + *.bin in work_dir.
    prefer: 'gltf' (default) or 'obj'
    weld/double_sided: off by default (safer mesh; enable only if needed)
    texture_map: optional uid/name → local file paths
    materials: optional /i/models/{uid}/options "materials" (fallback for texture roles)
    """
    work_dir = Path(work_dir)
    osgjs_path = work_dir / "file.osgjs"
    if not osgjs_path.is_file():
        raise FileNotFoundError(f"No file.osgjs in {work_dir}")

    scene = json.loads(osgjs_path.read_text(encoding="utf-8"))
    bins = _load_bins(work_dir)
    if not bins:
        raise FileNotFoundError("No .bin buffers next to file.osgjs")

    geoms: list[dict] = []
    ctx: dict = {}
    _walk_geometries(scene, geoms, ctx=ctx, _mat=list(Z_UP_TO_Y_UP))
    if not geoms:
        raise RuntimeError("No osg.Geometry found in file.osgjs")
    ss_index = _index_statesets(scene)
    texture_index = _index_textures(scene)
    mat_cache: dict[Any, dict] = {}

    meshes: list[dict] = []
    for gi, g in enumerate(geoms):
        try:
            user = parse_user_data(g.get("UserDataContainer"))
            valist = g.get("VertexAttributeList") or {}
            if "Vertex" not in valist or not valist["Vertex"].get("Array"):
                continue

            prims = g.get("PrimitiveSetList") or []
            all_tri: list[int] = []
            predict_idx: Optional[list[int]] = None
            for pi, prim in enumerate(prims):
                try:
                    idx, mode = decode_indices(prim, user, bins)
                except Exception:
                    continue
                if predict_idx is None:
                    predict_idx = list(idx)
                if mode == "TRIANGLE_STRIP":
                    tris = strip_to_triangles(idx)
                elif mode == "TRIANGLES":
                    tris = idx
                else:
                    continue
                all_tri.extend(tris)
            if not all_tri:
                continue

            positions, _ = decode_vertex_attr(
                "Vertex", valist["Vertex"], user, bins, predict_idx
            )
            n_verts = len(positions) // 3
            if n_verts <= 0:
                continue
            all_tri = _filter_tris(all_tri, n_verts)
            if not all_tri:
                continue
            # Drop only extreme decode spikes (keep long thin real geometry like weapons/hair)
            all_tri = filter_spike_tris(positions, all_tri, max_edge_factor=80.0)
            if not all_tri:
                continue

            # Sketchfab names UV sets after the texture unit using them
            # (TexCoord1, TexCoord7 …), not only TexCoord0.
            uv_names = sorted(
                (k for k in valist if k.startswith("TexCoord") and (valist[k] or {}).get("Array")),
                key=lambda k: int(k[8:] or 0),
            )
            raw_uvs: dict[str, Optional[list[float]]] = {}

            def get_uv(name: str) -> Optional[list[float]]:
                if name not in raw_uvs:
                    try:
                        vals, _ = decode_vertex_attr(name, valist[name], user, bins, predict_idx)
                        raw_uvs[name] = vals[: n_verts * 2] if vals and len(vals) >= n_verts * 2 else None
                    except Exception:
                        raw_uvs[name] = None
                return raw_uvs[name]

            ss_ref = (ctx.get("stateset") or {}).get(id(g)) or g.get("StateSet")
            ss = _resolve_stateset(ss_ref, ss_index)
            ss_key = ss.get("UniqueID", id(ss)) if ss else None
            mat_def = None
            if ss:
                if ss_key not in mat_cache:
                    mat_cache[ss_key] = build_material(
                        ss, work_dir, texture_map, materials, f"mat_{gi}",
                        texture_index=texture_index,
                    )
                mat_def = mat_cache[ss_key]

            # One UV set per distinct (TexCoord<unit>, offset, scale) actually used
            uv_sets: list[list[float]] = []
            uv_index: dict[tuple, int] = {}
            set_keys: dict[tuple, int] = {}
            for t in (mat_def or {}).get("textures", {}).values():
                tkey = (t["unit"], tuple(t["scale"]), tuple(t["offset"]))
                name = f"TexCoord{t['unit']}"
                if name not in uv_names or get_uv(name) is None:
                    name = next((n for n in uv_names if get_uv(n) is not None), None)
                if name is None:
                    continue
                skey = (name, tkey[1], tkey[2])
                if skey not in set_keys:
                    # Units often carry byte-identical UVs — share one TEXCOORD
                    for (oname, osc, ooff), oi in list(set_keys.items()):
                        if osc == skey[1] and ooff == skey[2] and get_uv(oname) == get_uv(name):
                            set_keys[skey] = oi
                            break
                if skey not in set_keys:
                    raw = get_uv(name)
                    (sx, sy), (ox, oy) = (t["scale"] + [1.0, 1.0])[:2], (t["offset"] + [0.0, 0.0])[:2]
                    uv = list(raw)
                    uv[0::2] = [u * sx + ox for u in raw[0::2]]
                    # Sketchfab samples with GL convention (origin bottom-left); glTF is top-left
                    uv[1::2] = [1.0 - (v * sy + oy) for v in raw[1::2]]
                    set_keys[skey] = len(uv_sets)
                    uv_sets.append(uv)
                uv_index[tkey] = set_keys[skey]

            uvs = uv_sets[0] if uv_sets else None
            if uvs is None:
                for n in uv_names:
                    uvs = get_uv(n)
                    if uvs is not None:
                        uv_sets = [uvs]
                        break

            if weld:
                positions, all_tri, uvs = weld_mesh_by_position(
                    positions, all_tri, uvs, eps=1e-5
                )
                if not all_tri:
                    continue
                # weld keeps a single UV set
                uv_sets = [uvs] if uvs else []
                uv_index = {k: 0 for k in uv_index} if uvs else {}

            if double_sided:
                all_tri = make_double_sided(all_tri)

            # Prefer decoded normals; fall back to computed
            normals = None
            if "Normal" in valist and valist["Normal"].get("Array"):
                try:
                    normals, _ = decode_vertex_attr(
                        "Normal", valist["Normal"], user, bins
                    )
                    if not normals or len(normals) // 3 != len(positions) // 3:
                        normals = None
                except Exception:
                    normals = None
            if normals is None:
                normals = recalc_vertex_normals(positions, all_tri)

            mat_name = _extract_material_name(g, f"mat_{gi}")
            tex_ref = _extract_diffuse_texture(g)
            tex_rel = _resolve_texture_file(
                tex_ref or "",
                work_dir,
                texture_map,
                material_name=mat_name,
            )
            if mat_def:
                mat_name = mat_def["name"]
                if "base" in mat_def["textures"]:
                    tex_rel = _rel_tex(mat_def["textures"]["base"]["file"], work_dir)

            xform = (ctx.get("xform") or {}).get(id(g)) or {}
            meshes.append(
                {
                    "name": str(g.get("Name") or f"mesh_{gi}"),
                    "positions": positions,
                    "normals": normals,
                    "uvs": uvs,
                    "indices": all_tri,
                    "material": re.sub(r"[^\w\-]+", "_", mat_name)[:64] or f"mat_{gi}",
                    "texture": tex_rel,
                    "uv_sets": uv_sets,
                    "uv_index": uv_index,
                    "material_def": mat_def,
                    "material_key": ss_key,
                    "matrix": (ctx.get("matrix") or {}).get(id(g)),
                    "xform_name": xform.get("name") or "",
                    "local_matrix": xform.get("local"),
                    "parent_matrix": xform.get("parent"),
                }
            )
        except Exception:
            continue

    if not meshes:
        raise RuntimeError("No mesh data could be decoded from osgjs")

    if prefer == "gltf":
        animations = _extract_animations(scene, bins)
        return _write_gltf(work_dir, model_name, meshes, animations)
    return _write_obj(work_dir, model_name, meshes)


def _write_obj(work_dir: Path, model_name: str, meshes: list[dict]) -> Path:
    obj_path = work_dir / f"{model_name}.obj"
    mtl_path = work_dir / f"{model_name}.mtl"

    # materials
    mats: dict[str, Optional[str]] = {}
    for m in meshes:
        mats.setdefault(m["material"], m.get("texture"))

    mtl_lines = [f"# MTL for {model_name}", f"# materials: {len(mats)}"]
    for mat, tex in mats.items():
        mtl_lines.append(f"newmtl {mat}")
        mtl_lines.append("Ka 1.000 1.000 1.000")
        mtl_lines.append("Kd 1.000 1.000 1.000")
        mtl_lines.append("Ks 0.000 0.000 0.000")
        mtl_lines.append("d 1.0")
        mtl_lines.append("illum 1")
        if tex:
            mtl_lines.append(f"map_Kd {tex}")
        mtl_lines.append("")
    mtl_path.write_text("\n".join(mtl_lines), encoding="utf-8")

    lines: list[str] = [
        f"# Sketchfab pure-python OBJ export: {model_name}",
        f"mtllib {mtl_path.name}",
        f"o {model_name}",
    ]

    # Global vertex pools (Blender-safe): write unique v/vt/vn per mesh with running offsets
    v_off = 0
    vt_off = 0
    vn_off = 0

    for mi, m in enumerate(meshes):
        pos = m["positions"]
        nrm = m["normals"]
        uvs = m["uvs"]
        n_verts = len(pos) // 3
        lines.append(f"g {m['name'] or f'mesh_{mi}'}")
        lines.append(f"usemtl {m['material']}")

        for i in range(0, len(pos), 3):
            lines.append(f"v {pos[i]:.8g} {pos[i+1]:.8g} {pos[i+2]:.8g}")

        has_vn = bool(nrm) and len(nrm) // 3 == n_verts
        has_vt = bool(uvs) and len(uvs) // 2 == n_verts

        if has_vn:
            for i in range(0, len(nrm), 3):
                lines.append(f"vn {nrm[i]:.8g} {nrm[i+1]:.8g} {nrm[i+2]:.8g}")
        if has_vt:
            for i in range(0, len(uvs), 2):
                # Sketchfab UVs often need V flip for Blender
                lines.append(f"vt {uvs[i]:.8g} {1.0 - uvs[i+1]:.8g}")

        idx = m["indices"]
        for i in range(0, len(idx), 3):
            a0, b0, c0 = idx[i], idx[i + 1], idx[i + 2]
            # OBJ is 1-based
            a, b, c = a0 + 1 + v_off, b0 + 1 + v_off, c0 + 1 + v_off
            if has_vt and has_vn:
                at, bt, ct = a0 + 1 + vt_off, b0 + 1 + vt_off, c0 + 1 + vt_off
                an, bn, cn = a0 + 1 + vn_off, b0 + 1 + vn_off, c0 + 1 + vn_off
                lines.append(f"f {a}/{at}/{an} {b}/{bt}/{bn} {c}/{ct}/{cn}")
            elif has_vt:
                at, bt, ct = a0 + 1 + vt_off, b0 + 1 + vt_off, c0 + 1 + vt_off
                lines.append(f"f {a}/{at} {b}/{bt} {c}/{ct}")
            elif has_vn:
                an, bn, cn = a0 + 1 + vn_off, b0 + 1 + vn_off, c0 + 1 + vn_off
                lines.append(f"f {a}//{an} {b}//{bn} {c}//{cn}")
            else:
                lines.append(f"f {a} {b} {c}")

        v_off += n_verts
        if has_vt:
            vt_off += n_verts
        if has_vn:
            vn_off += n_verts

    obj_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return obj_path


# osgAnimation channel_mode bits (viewer: P=1, N=4, O=8, D=16).
_CHAN_QUANT = 1
_CHAN_ORIGIN = 4
_CHAN_SPHERICAL = 8
_CHAN_TIME_ORIGIN = 16

# (kind, compressed, packed) — packed is SOA layout, compressed enables time origin / dequant.
_ANIM_CHANNELS = {
    "osgAnimation.QuatSlerpChannelCompressedPacked": ("quat", True, True),
    "osgAnimation.QuatSlerpChannelCompressed": ("quat", True, False),
    "osgAnimation.QuatSlerpChannelPacked": ("quat", False, True),
    "osgAnimation.QuatSlerpChannel": ("quat", False, False),
    "osgAnimation.Vec3LerpChannelCompressedPacked": ("vec3", True, True),
    "osgAnimation.Vec3LerpChannelCompressed": ("vec3", True, False),
    "osgAnimation.Vec3LerpChannelPacked": ("vec3", False, True),
    "osgAnimation.Vec3LerpChannel": ("vec3", False, False),
}


def _soa_to_aos(values: list, item_size: int) -> list:
    """Sketchfab 'packed' keys store component 0 for every frame, then component 1, …"""
    n = len(values) // item_size
    if item_size <= 1 or n <= 0:
        return list(values)
    out = [0.0] * (n * item_size)
    for r in range(n):
        for o in range(item_size):
            out[r * item_size + o] = values[r + n * o]
    return out


def _prefix_sum(values: list[float], item_size: int) -> list[float]:
    out = list(values)
    n = len(out) // item_size
    for i in range(1, n):
        for k in range(item_size):
            out[i * item_size + k] += out[(i - 1) * item_size + k]
    return out


def _quat_mul(a: list[float], b: list[float]) -> list[float]:
    x1, y1, z1, w1 = a
    x2, y2, z2, w2 = b
    return [
        x1 * w2 + y1 * z2 - z1 * y2 + w1 * x2,
        -x1 * z2 + y1 * w2 + z1 * x2 + w1 * y2,
        x1 * y2 - y1 * x2 + z1 * w2 + w1 * z2,
        -x1 * x2 - y1 * y2 - z1 * z2 + w1 * w2,
    ]


def _quat_norm(q: list[float]) -> list[float]:
    length = math.sqrt(sum(v * v for v in q)) or 1.0
    return [v / length for v in q]


def _accumulate_quat(values: list[float]) -> list[float]:
    """key[i] = key[i - 1] * key[i], then keep the short hemisphere for slerp."""
    n = len(values) // 4
    if n <= 0:
        return []
    prev = _quat_norm(values[:4])
    out = list(prev)
    for i in range(1, n):
        cur = _quat_norm(values[i * 4:(i + 1) * 4])
        prod = _quat_norm(_quat_mul(prev, cur))
        if sum(a * b for a, b in zip(prev, prod)) < 0:
            prod = [-v for v in prod]
        out.extend(prod)
        prev = prod
    return out


def _channel_path(name: str, kind: str) -> Optional[str]:
    n = (name or "").lower()
    if n in ("rotate", "rotation", "quat", "quaternion"):
        return "rotation"
    if n in ("translate", "translation", "position", "pos"):
        return "translation"
    if n in ("scale", "scl"):
        return "scale"
    if kind == "quat":
        return "rotation"
    if kind == "vec3" and n != "scale":
        return "translation"
    return None


def _decode_anim_channel(
    ch: dict,
    kind: str,
    compressed: bool,
    packed: bool,
    bins: dict[str, bytes],
) -> Optional[dict]:
    keys_node = (ch.get("KeyFrames") or {}).get("Key")
    time_node = (ch.get("KeyFrames") or {}).get("Time")
    target = ch.get("TargetName")
    path = _channel_path(str(ch.get("Name") or ""), kind)
    if not keys_node or not time_node or not target or not path:
        return None
    user = parse_user_data(ch.get("UserDataContainer"))
    mode = int(user.get("channel_mode") or 0)
    keys, _, item_size = read_buffer_array(bins, keys_node, element_count_mode="viewer")
    times, _, _ = read_buffer_array(bins, time_node, element_count_mode="viewer")
    if not keys or not times:
        return None
    if packed and item_size > 1:
        keys = _soa_to_aos(keys, item_size)

    if kind == "quat" and (mode & _CHAN_SPHERICAL):
        quats = decode_normals(
            [int(x) for x in keys],
            4,
            float(user.get("epsilon") or 0.25),
            int(user.get("nphi") or 720),
            True,
        )
        if mode & _CHAN_ORIGIN:
            origin = [
                float(user.get("ox") or 0.0),
                float(user.get("oy") or 0.0),
                float(user.get("oz") or 0.0),
                float(user.get("ow") or 1.0),
            ]
            quats = _accumulate_quat(origin + quats)
        values = quats
        size = 4
    elif kind == "vec3":
        if compressed and (mode & _CHAN_QUANT):
            bbl = [float(user.get(k) or 0.0) for k in ("bx", "by", "bz")]
            step = [float(user.get(k) or 1.0) for k in ("hx", "hy", "hz")]
            keys = dequant([int(x) for x in keys], bbl, step, 3)
        else:
            keys = [float(x) for x in keys]
        if mode & _CHAN_ORIGIN:
            origin = [float(user.get("ox") or 0.0), float(user.get("oy") or 0.0), float(user.get("oz") or 0.0)]
            keys = _prefix_sum(origin + keys, 3)
        values = keys
        size = 3
    else:
        values = [float(x) for x in keys]
        size = 4 if kind == "quat" else max(item_size, 1)
        if kind == "quat" and (mode & _CHAN_ORIGIN):
            origin = [
                float(user.get("ox") or 0.0),
                float(user.get("oy") or 0.0),
                float(user.get("oz") or 0.0),
                float(user.get("ow") or 1.0),
            ]
            values = _accumulate_quat(origin + values)
            size = 4

    times_f = [float(t) for t in times]
    if compressed and (mode & _CHAN_TIME_ORIGIN):
        times_f = _prefix_sum([float(user.get("ot") or 0.0)] + times_f, 1)

    count = min(len(times_f), len(values) // size)
    if count < 1:
        return None
    out_t: list[float] = []
    out_v: list[float] = []
    last: Optional[float] = None
    for i in range(count):
        t = times_f[i]
        if last is not None and t <= last:
            continue
        out_t.append(t)
        out_v.extend(values[i * size:(i + 1) * size])
        last = t
    if not out_t:
        return None
    return {"target": str(target), "path": path, "times": out_t, "values": out_v, "size": size}


def _extract_animations(scene: Any, bins: dict[str, bytes]) -> list[dict]:
    """Decode osgAnimation clips into glTF channel records. Missing buffers are skipped."""
    found: list[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            anim = node.get("osgAnimation.Animation")
            if isinstance(anim, dict):
                channels: list[dict] = []
                for wrap in anim.get("Channels") or []:
                    if not isinstance(wrap, dict):
                        continue
                    for key, spec in _ANIM_CHANNELS.items():
                        if key not in wrap or not isinstance(wrap[key], dict):
                            continue
                        try:
                            decoded = _decode_anim_channel(wrap[key], *spec, bins)
                        except Exception:
                            decoded = None
                        if decoded:
                            channels.append(decoded)
                        break
                if channels:
                    found.append({
                        "name": str(anim.get("Name") or "Animation"),
                        "channels": channels,
                    })
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(scene)
    return found


def _mat_to_trs(matrix: Optional[list[float]]) -> tuple[list[float], list[float], list[float]]:
    """Column-major 4x4 → glTF translation, rotation (xyzw), scale."""
    if not matrix or len(matrix) != 16:
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0], [1.0, 1.0, 1.0]
    ax, ay, az = matrix[0:3], matrix[4:7], matrix[8:11]

    def length(v: list[float]) -> float:
        return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])

    sx, sy, sz = length(ax) or 1.0, length(ay) or 1.0, length(az) or 1.0
    cx = ax[1] * ay[2] - ax[2] * ay[1]
    cy = ax[2] * ay[0] - ax[0] * ay[2]
    cz = ax[0] * ay[1] - ax[1] * ay[0]
    if cx * az[0] + cy * az[1] + cz * az[2] < 0:
        sz = -sz
    m00, m10, m20 = ax[0] / sx, ax[1] / sx, ax[2] / sx
    m01, m11, m21 = ay[0] / sy, ay[1] / sy, ay[2] / sy
    m02, m12, m22 = az[0] / sz, az[1] / sz, az[2] / sz
    trace = m00 + m11 + m22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    return (
        [float(matrix[12]), float(matrix[13]), float(matrix[14])],
        _quat_norm([x, y, z, w]),
        [sx, sy, sz],
    )


def _write_gltf(
    work_dir: Path,
    model_name: str,
    meshes: list[dict],
    animations: Optional[list[dict]] = None,
) -> Path:
    """glTF with embedded geometry, PBR materials and external textures/ images."""
    import array
    import base64
    from urllib.parse import quote

    blob = bytearray()
    accessors: list[dict] = []
    buffer_views: list[dict] = []
    gl_meshes: list[dict] = []
    nodes: list[dict] = []

    def add_f32(data: list[float], item_size: int, *, target: Optional[int] = 34962) -> int:
        while len(blob) % 4:
            blob.append(0)
        offset = len(blob)
        raw = array.array("f", data).tobytes()
        blob.extend(raw)
        bv = len(buffer_views)
        view: dict = {"buffer": 0, "byteOffset": offset, "byteLength": len(raw)}
        if target:
            view["target"] = target
        buffer_views.append(view)
        mins = [min(data[i::item_size]) for i in range(item_size)]
        maxs = [max(data[i::item_size]) for i in range(item_size)]
        ai = len(accessors)
        accessors.append({
            "bufferView": bv, "componentType": 5126,
            "count": len(data) // item_size,
            "type": {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[item_size],
            "min": mins, "max": maxs,
        })
        return ai

    def add_u32(data: list[int]) -> int:
        while len(blob) % 4:
            blob.append(0)
        offset = len(blob)
        raw = array.array("I", data).tobytes()
        blob.extend(raw)
        bv = len(buffer_views)
        buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(raw), "target": 34963})
        ai = len(accessors)
        accessors.append({"bufferView": bv, "componentType": 5125, "count": len(data), "type": "SCALAR"})
        return ai

    materials: list[dict] = []
    textures: list[dict] = []
    images: list[dict] = []
    samplers: list[dict] = []
    image_idx: dict[str, int] = {}
    sampler_idx: dict[tuple, int] = {}
    texture_idx: dict[tuple, int] = {}
    material_idx: dict[tuple, int] = {}
    baked: dict[Any, dict] = {}
    bake_dir = work_dir / "textures" / "_gltf"

    def tex_ref(slot: dict, m: dict) -> Optional[dict]:
        tkey = (slot["unit"], tuple(slot["scale"]), tuple(slot["offset"]))
        if tkey not in m["uv_index"]:
            return None
        rel = _rel_tex(Path(slot["file"]), work_dir)
        if rel not in image_idx:
            image_idx[rel] = len(images)
            images.append({"uri": quote(rel)})
        skey = (slot["mag"], slot["min"], *slot["wrap"])
        if skey not in sampler_idx:
            sampler_idx[skey] = len(samplers)
            samplers.append({"magFilter": skey[0], "minFilter": skey[1], "wrapS": skey[2], "wrapT": skey[3]})
        key = (image_idx[rel], sampler_idx[skey])
        if key not in texture_idx:
            texture_idx[key] = len(textures)
            textures.append({"source": key[0], "sampler": key[1]})
        return {"index": texture_idx[key], "texCoord": m["uv_index"][tkey]}

    def material_for(m: dict) -> Optional[int]:
        mat = m.get("material_def")
        if not mat:
            return None
        mkey = m.get("material_key")
        if mkey not in baked:
            baked[mkey] = _bake_textures(mat, bake_dir, f"mat{len(baked)}")
        slots = baked[mkey]
        refs = {name: tex_ref(slot, m) for name, slot in slots.items()}
        refs = {k: v for k, v in refs.items() if v}
        cache_key = (mkey, tuple(sorted((k, v["index"], v["texCoord"]) for k, v in refs.items())))
        if cache_key in material_idx:
            return material_idx[cache_key]
        pbr: dict = {
            "baseColorFactor": mat["base_factor"],
            "metallicFactor": mat["metal"],
            "roughnessFactor": mat["rough"],
        }
        if "base" in refs:
            pbr["baseColorTexture"] = refs["base"]
        if "mr" in refs:
            pbr["metallicRoughnessTexture"] = refs["mr"]
        gm: dict = {"name": mat["name"], "pbrMetallicRoughness": pbr}
        if "normal" in refs:
            gm["normalTexture"] = refs["normal"]
        if "ao" in refs:
            gm["occlusionTexture"] = refs["ao"]
        if "emissive" in refs:
            gm["emissiveTexture"] = refs["emissive"]
        if any(mat["emissive"]):
            gm["emissiveFactor"] = mat["emissive"]
        if mat["alpha_mode"] != "OPAQUE":
            gm["alphaMode"] = mat["alpha_mode"]
            if mat["alpha_mode"] == "MASK":
                gm["alphaCutoff"] = mat["alpha_cutoff"]
        if mat["double_sided"]:
            gm["doubleSided"] = True
        material_idx[cache_key] = len(materials)
        materials.append(gm)
        return material_idx[cache_key]

    for mi, m in enumerate(meshes):
        attrs = {"POSITION": add_f32(m["positions"], 3)}
        if m["normals"]:
            attrs["NORMAL"] = add_f32(m["normals"], 3)
        uv_sets = m.get("uv_sets") or ([m["uvs"]] if m["uvs"] else [])
        for ui, uv in enumerate(uv_sets):
            attrs[f"TEXCOORD_{ui}"] = add_f32(uv, 2)
        if "uv_sets" not in m:
            m["uv_sets"], m["uv_index"] = uv_sets, {}
        prim: dict = {"attributes": attrs, "indices": add_u32(m["indices"]), "mode": 4}
        mat_i = material_for(m)
        if mat_i is not None:
            prim["material"] = mat_i
        gl_meshes.append({"name": m["name"], "primitives": [prim]})

    targets = {
        ch["target"]
        for anim in (animations or [])
        for ch in anim["channels"]
    }
    groups: dict[str, list[dict]] = {}
    plain: list[dict] = []
    for mi, m in enumerate(meshes):
        spec = {
            "mesh": mi,
            "name": m["name"],
            "matrix": m.get("matrix"),
            "xform_name": m.get("xform_name") or "",
            "local": m.get("local_matrix"),
            "parent": m.get("parent_matrix"),
        }
        if spec["xform_name"] in targets and spec["local"]:
            groups.setdefault(spec["xform_name"], []).append(spec)
        else:
            plain.append(spec)

    def _near_identity(matrix: Optional[list[float]]) -> bool:
        return not matrix or all(abs(a - b) < 1e-5 for a, b in zip(matrix, IDENTITY))

    parent_index: dict[tuple, int] = {}

    def parent_for(matrix: Optional[list[float]]) -> Optional[int]:
        if _near_identity(matrix):
            return None
        key = tuple(round(float(x), 5) for x in matrix or [])
        if key in parent_index:
            return parent_index[key]
        node: dict = {"matrix": [float(x) for x in matrix or []], "children": []}
        idx = len(nodes)
        nodes.append(node)
        parent_index[key] = idx
        return idx

    target_node: dict[str, int] = {}
    for name, specs in groups.items():
        first = specs[0]
        pidx = parent_for(first.get("parent"))
        translation, rotation, scale = _mat_to_trs(first.get("local"))
        node = {
            "name": name,
            "translation": translation,
            "rotation": rotation,
            "scale": scale,
        }
        idx = len(nodes)
        nodes.append(node)
        if len(specs) == 1:
            node["mesh"] = specs[0]["mesh"]
        else:
            kids = []
            for spec in specs:
                kids.append(len(nodes))
                nodes.append({"mesh": spec["mesh"], "name": spec["name"]})
            node["children"] = kids
        if pidx is None:
            pass
        else:
            nodes[pidx]["children"].append(idx)
        target_node[name] = idx

    for spec in plain:
        node = {"mesh": spec["mesh"], "name": spec["name"]}
        mtx = spec.get("matrix")
        if mtx and not _near_identity(mtx):
            node["matrix"] = [float(x) for x in mtx]
        nodes.append(node)

    for node in nodes:
        if "children" in node and not node["children"]:
            del node["children"]

    gl_anims: list[dict] = []
    for anim in animations or []:
        anim_samplers: list[dict] = []
        channels: list[dict] = []
        for ch in anim["channels"]:
            node_i = target_node.get(ch["target"])
            if node_i is None:
                continue
            anim_samplers.append({
                "input": add_f32(ch["times"], 1, target=None),
                "output": add_f32(ch["values"], ch["size"], target=None),
                "interpolation": "LINEAR",
            })
            channels.append({
                "sampler": len(anim_samplers) - 1,
                "target": {"node": node_i, "path": ch["path"]},
            })
        if channels:
            gl_anims.append({"name": anim["name"], "samplers": anim_samplers, "channels": channels})

    child_ids = {c for node in nodes for c in (node.get("children") or [])}
    scene_nodes = [i for i in range(len(nodes)) if i not in child_ids]

    b64 = base64.b64encode(bytes(blob)).decode("ascii")
    gltf = {
        "asset": {"version": "2.0", "generator": "sketchfab-dl-pure-python"},
        "buffers": [{"byteLength": len(blob), "uri": f"data:application/octet-stream;base64,{b64}"}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "meshes": gl_meshes,
        "nodes": nodes,
        **({"materials": materials} if materials else {}),
        **({"textures": textures, "images": images, "samplers": samplers} if textures else {}),
        **({"animations": gl_anims} if gl_anims else {}),
        "scenes": [{"nodes": scene_nodes}],
        "scene": 0,
    }
    out = work_dir / f"{model_name}.gltf"
    out.write_text(json.dumps(gltf), encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys
    d = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    p = convert_osgjs(d, "model", prefer="obj")
    print("wrote", p, p.stat().st_size)

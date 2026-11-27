#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download Sketchfab model textures (public API, no login)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

Progress = Optional[Callable[[str], None]]


def _log(progress: Progress, msg: str) -> None:
    if progress:
        try:
            progress(msg)
        except Exception:
            pass


def fetch_texture_manifest(uid: str, http_get_json) -> list[dict]:
    data = http_get_json(f"https://sketchfab.com/i/models/{uid}/textures")
    return list(data.get("results") or [])


def _best_image(images: list[dict]) -> Optional[dict]:
    if not images:
        return None

    def score(img: dict) -> tuple[int, int]:
        w = int(img.get("width") or 0)
        h = int(img.get("height") or 0)
        size = int(img.get("size") or 0)
        # Encoded byte size depends on format and compression. A larger JPEG
        # can still have fewer pixels than a smaller WebP or PNG variant.
        return w * h, size

    # Prefer power-of-two full maps (skip tiny thumbs)
    usable = [
        i
        for i in images
        if int(i.get("width") or 0) >= 64 and int(i.get("height") or 0) >= 64
    ]
    pool = usable or images
    return max(pool, key=score)


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "tex")
    name = name.strip(" .") or "tex"
    return name


def collect_osgjs_texture_refs(osgjs_path: Path) -> list[str]:
    """Image paths named by file.osgjs (``textures/<uid>/<file>.png``).

    These are the images the viewer samples. The public texture API serves the
    same atlas after an 8×8 block scramble (see ``unscramble_image``).
    """
    if not osgjs_path.is_file():
        return []
    try:
        scene = json.loads(osgjs_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    found: list[str] = []
    seen: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            file_ref = node.get("File")
            if isinstance(file_ref, str):
                rel = file_ref.replace("\\", "/").lstrip("/")
                if Path(rel).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and rel not in seen:
                    seen.add(rel)
                    found.append(rel)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(scene)
    return found


def texture_uid_from_ref(ref: str) -> Optional[str]:
    match = re.search(r"([a-f0-9]{32})", (ref or "").replace("\\", "/"))
    return match.group(1) if match else None


def image_pk_maps(manifest: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    """image uid → pk, and texture uid → pk when every full image shares one pk.

    Sketchfab stores the public texture files with an 8×8 block scramble.
    ``pk`` is the key the viewer uses to put the blocks back. Thumbnails have
    no pk and are already linear.
    """
    by_image: dict[str, int] = {}
    by_texture: dict[str, int] = {}
    for tex in manifest:
        tuid = str(tex.get("uid") or "")
        pks: set[int] = set()
        for im in tex.get("images") or []:
            uid = str(im.get("uid") or "")
            pk = _as_pk(im.get("pk"))
            if not uid or not pk:
                continue
            by_image[uid] = pk
            pks.add(pk)
        if tuid and len(pks) == 1:
            by_texture[tuid] = next(iter(pks))
    return by_image, by_texture


def _as_pk(value) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        pk = int(value)
    except (TypeError, ValueError):
        return 0
    return pk if pk > 0 else 0


def _trunc_div(a: int, b: int) -> int:
    """GLSL integer division (toward zero)."""
    if b == 0:
        return 0
    return int(a / b) if a >= 0 else -int((-a) / b)


def _tri_prefix(y: int, t: int, f: int) -> int:
    x = y if y < t else t
    n = t if y < t else y
    if f < x:
        return f * (f + 1) // 2
    if f < n:
        return x * (x + 1) // 2 + x * (f - x)
    r = f - n
    return x * (x + 1) // 2 + x * (n - x) + (x - 1) * r - (r - 1) * r // 2


def _block_index(bx: int, by: int, cx: int, cy: int) -> int:
    r = bx if bx < by else by
    n = by if bx < by else bx
    v = cx + cy
    even = (v & 1) == 0
    base = _tri_prefix(bx, by, v)
    if v < r:
        return base + (v - cy if even else cy)
    if v < n:
        s = by - cy - 1
        if bx < by:
            s = r - (bx - cx)
        return base + (s if even else r - s - 1)
    s = by - cy - 1
    e = r + n - v - 1
    return base + (s if even else e - s - 1)


def _index_to_block(bx: int, by: int, index: int) -> tuple[int, int]:
    v = bx if bx < by else by
    r = by if bx < by else bx
    tri = v * (v + 1) // 2
    if index < tri:
        n = (-1 + int(1e-6 + (8 * index + 1) ** 0.5)) // 2
        h = index - _tri_prefix(bx, by, n)
        if (n & 1) == 0:
            return h, n - h
        return n - h, h
    mid = tri + v * (r - v)
    if index < mid:
        x = index - tri
        n = v + x // v
        s = x % v
        even = (n & 1) == 0
        g = n - v + s + 1
        e = v - s - 1
        if bx > by:
            return (g, e) if even else (n - s, s)
        return (s, n - s) if even else (e, g)
    n0 = v * (v - 1) // 2 - (index - mid) - 1
    s = (-1 + int((8 * n0 + 1) ** 0.5)) // 2
    n = r + v - s - 2
    h = index - _tri_prefix(bx, by, n)
    e = v + r - n - 1
    if (n & 1) == 0:
        h = e - h - 1
    col = n + h - bx + 1
    return n - col, col


def _pixel_to_index(x: int, y: int, bw: int, bh: int) -> int:
    bi = _block_index(bw, bh, x >> 3, y >> 3)
    px, py = x & 7, y & 7
    kind = bi & 3
    if kind == 1:
        px = 7 - px
    elif kind == 2:
        px, py = py, px
    elif kind == 3:
        px, py = 7 - py, px
    return bi * 64 + px + py * 8


def _index_to_pixel(index: int, w: int, h: int, bw: int, bh: int) -> tuple[int, int]:
    area = w * h
    if index < 0:
        index += area
    index %= area
    block = index // 64
    rem = index - block * 64
    row, col = rem // 8, rem % 8
    kind = block & 3
    gx, gy = _index_to_block(bw, bh, block)
    x, y = gx * 8, gy * 8
    if kind == 0:
        x += col
        y += row
    elif kind == 1:
        x += 7 - col
        y += row
    elif kind == 2:
        x += row
        y += col
    else:
        x += row
        y += 7 - col
    return x, y


def unscramble_pixels(src: bytes, w: int, h: int, bpp: int, pk: int) -> bytes:
    """Inverse of the viewer's texture upload scramble. ``pk`` comes from the texture API."""
    if pk <= 0 or w < 8 or h < 8 or (w % 8) or (h % 8) or bpp < 1:
        return src
    area = w * h
    if len(src) < area * bpp:
        return src
    offset = -((pk * 64) % area)
    bw, bh = w // 8, h // 8
    out = bytearray(area * bpp)
    for y in range(h):
        row = y * w
        for x in range(w):
            n = _pixel_to_index(x, y, bw, bh) + offset
            if n >= area:
                n -= area
            elif n < 0:
                n += area
            sx, sy = _index_to_pixel(n, w, h, bw, bh)
            di = (row + x) * bpp
            si = (sy * w + sx) * bpp
            out[di : di + bpp] = src[si : si + bpp]
    return bytes(out)


def _load_rgba(path: Path):
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        im = Image.open(path)
        bands = im.getbands()
        if im.mode == "P":
            im = im.convert("RGBA" if "transparency" in im.info else "RGB")
        elif im.mode not in ("RGB", "RGBA", "L", "LA"):
            im = im.convert("RGBA" if "A" in bands else "RGB")
        return im.tobytes(), im.size[0], im.size[1], im.mode
    if path.suffix.lower() == ".png":
        return _read_png(path)
    raise RuntimeError("Pillow is required to unscramble this image")


def _save_image(path: Path, data: bytes, w: int, h: int, mode: str) -> None:
    try:
        from PIL import Image
        Image.frombytes(mode, (w, h), data).save(path)
        return
    except ImportError:
        pass
    if path.suffix.lower() == ".png" and mode in ("L", "LA", "RGB", "RGBA"):
        _write_png(path, data, w, h, len(mode))
        return
    raise RuntimeError("Pillow is required to write this image")


def unscramble_image(path: Path, pk: int) -> bool:
    """Rewrite ``path`` in place when ``pk`` marks it as block-scrambled."""
    pk = _as_pk(pk)
    if not pk or not path.is_file():
        return False
    try:
        raw, w, h, mode = _load_rgba(path)
    except Exception:
        return False
    bpp = len(mode)
    if bpp not in (1, 2, 3, 4) or w % 8 or h % 8:
        return False
    fixed = unscramble_pixels(raw, w, h, bpp, pk)
    if fixed == raw:
        return False
    _save_image(path, fixed, w, h, mode)
    return True


def looks_block_scrambled(path: Path) -> bool:
    """True when 8-pixel boundaries are much sharper than pixels inside a block."""
    try:
        raw, w, h, mode = _load_rgba(path)
    except Exception:
        return False
    bpp = len(mode)
    if w < 16 or h < 16 or w % 8 or h % 8 or len(raw) < w * h * bpp:
        return False
    inside = edge = 0
    ni = ne = 0
    channels = min(3, bpp)
    for y in range(0, h - 1, 2):
        row = y * w * bpp
        for x in range(w - 1):
            i = row + x * bpp
            d = sum(abs(raw[i + c] - raw[i + bpp + c]) for c in range(channels))
            if ((x + 1) & 7) == 0:
                edge += d
                ne += 1
            elif (x & 7) != 0:
                inside += d
                ni += 1
    if ni == 0 or ne == 0 or inside == 0:
        return False
    return (edge / ne) / (inside / ni) > 4.0


def _read_png(path: Path):
    import struct
    import zlib

    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png")
    i = 8
    w = h = color = None
    idat = bytearray()
    while i + 8 <= len(data):
        ln = struct.unpack(">I", data[i : i + 4])[0]
        typ = data[i + 4 : i + 8]
        chunk = data[i + 8 : i + 8 + ln]
        if typ == b"IHDR":
            w, h, bit, color, _comp, _filt, inter = struct.unpack(">IIBBBBB", chunk)
            if bit != 8 or inter != 0 or color not in (0, 2, 4, 6):
                raise ValueError("unsupported png")
        elif typ == b"IDAT":
            idat.extend(chunk)
        elif typ == b"IEND":
            break
        i += 12 + ln
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    raw = zlib.decompress(bytes(idat))
    stride = w * bpp
    rows = []
    prev = bytearray(stride)
    pos = 0
    for _y in range(h):
        filt = raw[pos]
        pos += 1
        row = bytearray(raw[pos : pos + stride])
        pos += stride
        if filt == 1:
            for x in range(stride):
                left = row[x - bpp] if x >= bpp else 0
                row[x] = (row[x] + left) & 255
        elif filt == 2:
            for x in range(stride):
                row[x] = (row[x] + prev[x]) & 255
        elif filt == 3:
            for x in range(stride):
                left = row[x - bpp] if x >= bpp else 0
                row[x] = (row[x] + ((left + prev[x]) // 2)) & 255
        elif filt == 4:
            for x in range(stride):
                left = row[x - bpp] if x >= bpp else 0
                up = prev[x]
                ul = prev[x - bpp] if x >= bpp else 0
                p = left + up - ul
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                pr = left if pa <= pb and pa <= pc else up if pb <= pc else ul
                row[x] = (row[x] + pr) & 255
        elif filt != 0:
            raise ValueError("bad png filter")
        rows.append(row)
        prev = row
    mode = {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}[bpp]
    return b"".join(rows), w, h, mode


def _write_png(path: Path, data: bytes, w: int, h: int, bpp: int) -> None:
    import struct
    import zlib

    stride = w * bpp
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(data[y * stride : (y + 1) * stride])

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    color = {1: 0, 2: 4, 3: 2, 4: 6}[bpp]
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def download_textures(
    uid: str,
    out_dir: Path,
    *,
    http_get_json,
    download_file,
    progress: Progress = None,
    only_uids: Optional[set[str]] = None,
) -> dict[str, Path]:
    """
    Download all textures for a model.

    Returns mapping:
      texture_uid -> local path
      and also basename / relative path keys for MTL lookup.

    Progress tokens (for i18n): step:tex_count|N , step:tex_item|i|N|name
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = fetch_texture_manifest(uid, http_get_json)
    if only_uids is not None:
        results = [tex for tex in results if str(tex.get("uid") or "") in only_uids]
    mapping: dict[str, Path] = {}
    _log(progress, f"step:tex_count|{len(results)}")

    for i, tex in enumerate(results, start=1):
        tuid = str(tex.get("uid") or "")
        name = _safe_name(str(tex.get("name") or tuid or f"tex_{i}"))
        img = _best_image(list(tex.get("images") or []))
        if not img or not img.get("url"):
            _log(progress, f"step:tex_skip|{name}")
            continue
        url = img["url"]
        # keep extension from url or name
        ext = Path(name).suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            url_ext = Path(url.split("?", 1)[0]).suffix.lower()
            ext = url_ext if url_ext in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
            if not name.lower().endswith(ext):
                name = f"{name}{ext}"
        # Sketchfab-like filename used by some pipelines
        fname = f"{tuid}_{name}" if tuid else name
        dest = out_dir / fname
        try:
            _log(progress, f"step:tex_item|{i}|{len(results)}|{name}")
            download_file(url, dest)
            pk = _as_pk(img.get("pk"))
            if pk and unscramble_image(dest, pk):
                _log(progress, f"step:tex_fix|{name}")
            if tuid:
                mapping[tuid] = dest
            mapping[name] = dest
            mapping[fname] = dest
            mapping[dest.name] = dest
        except Exception as e:
            _log(progress, f"step:tex_fail|{name}|{e}")

    # also write manifest
    (out_dir / "textures_manifest.json").write_text(
        json.dumps(
            {
                "uid": uid,
                "count": len(results),
                "files": [p.name for p in out_dir.glob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return mapping

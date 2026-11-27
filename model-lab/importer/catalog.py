# -*- coding: utf-8 -*-
"""
Send a downloaded model to the ModelFolder catalog (ModelsFolder project).

ModelFolder ingests a *model folder*: the model file, a preview image with the
same stem (models without a preview are skipped by its ingest), textures and a
`<stem>.url` file whose URL= line becomes the model's source_url.

Category: explicit override, else mapped from Sketchfab categories / tags to
ModelFolder's closed category list (app/taxonomy.py there), else "other".

Config (env):
  CATALOG_URL        e.g. https://models.example.com   (required for destination=catalog)
  CATALOG_USERNAME   ModelFolder AUTH_USERNAME (HTTP Basic), if auth is enabled
  CATALOG_PASSWORD   ModelFolder AUTH_PASSWORD
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

import main as sf

def _load_dotenv() -> None:
    """Read project .env without overriding variables already set in the process."""
    path = Path(__file__).resolve().parent / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = val.strip().strip('"').strip("'")


def _env(name: str, default: str = "") -> str:
    _load_dotenv()
    return os.environ.get(name, default).strip()


def catalog_url() -> str:
    return _env("CATALOG_URL").rstrip("/")


def catalog_timeout() -> int:
    try:
        return int(_env("CATALOG_TIMEOUT", "600") or "600")
    except ValueError:
        return 600

# Mirrors ModelFolder app/taxonomy.py CATEGORIES
CATEGORIES = (
    "character", "creature", "vehicle", "weapon", "prop", "environment",
    "building", "nature", "furniture", "food", "clothing", "vfx", "animation",
    "material", "ui", "vehicle-part", "scene-kit", "other",
)

# Sketchfab category slug -> ModelFolder category
SKETCHFAB_CATEGORIES = {
    "animals-pets": "creature",
    "architecture": "building",
    "cars-vehicles": "vehicle",
    "characters-creatures": "character",
    "people": "character",
    "cultural-heritage-history": "prop",
    "electronics-gadgets": "prop",
    "fashion-style": "clothing",
    "food-drink": "food",
    "furniture-home": "furniture",
    "music": "prop",
    "nature-plants": "nature",
    "places-travel": "environment",
    "science-technology": "prop",
    "sports-fitness": "prop",
    "weapons-military": "weapon",
}

# Most specific first: a model tagged both "people" and "weapons" is a weapon pack
# far less often than a character, so characters win over props, etc.
CATEGORY_PRIORITY = (
    "weapon", "vehicle", "creature", "character", "clothing", "food",
    "furniture", "building", "nature", "environment", "prop",
)

# Tag word -> category, used when Sketchfab categories are missing
TAG_HINTS = {
    "character": "character", "human": "character", "humanoid": "character",
    "girl": "character", "boy": "character", "man": "character", "woman": "character",
    "anime": "character", "npc": "character", "hero": "character",
    "animal": "creature", "creature": "creature", "monster": "creature",
    "dinosaur": "creature", "dragon": "creature", "dog": "creature", "cat": "creature",
    "car": "vehicle", "vehicle": "vehicle", "truck": "vehicle", "ship": "vehicle",
    "boat": "vehicle", "airplane": "vehicle", "plane": "vehicle", "tank": "vehicle",
    "motorcycle": "vehicle", "spaceship": "vehicle",
    "weapon": "weapon", "gun": "weapon", "rifle": "weapon", "pistol": "weapon",
    "sword": "weapon", "knife": "weapon", "axe": "weapon",
    "building": "building", "house": "building", "architecture": "building",
    "castle": "building", "tower": "building",
    "tree": "nature", "plant": "nature", "rock": "nature", "flower": "nature",
    "landscape": "environment", "terrain": "environment", "scene": "environment",
    "environment": "environment", "city": "environment", "room": "environment",
    "furniture": "furniture", "chair": "furniture", "table": "furniture", "sofa": "furniture",
    "food": "food", "fruit": "food", "drink": "food",
    "clothing": "clothing", "armor": "clothing", "helmet": "clothing", "shoe": "clothing",
    "vfx": "vfx", "particle": "vfx",
}


def is_configured() -> bool:
    return bool(catalog_url())


def normalize_category(raw: Optional[str]) -> Optional[str]:
    key = re.sub(r"[^a-z0-9]+", "-", (raw or "").strip().lower()).strip("-")
    if not key:
        return None
    if key in CATEGORIES:
        return key
    if key in SKETCHFAB_CATEGORIES:
        return SKETCHFAB_CATEGORIES[key]
    singular = key[:-1] if key.endswith("s") else key
    return TAG_HINTS.get(key) or TAG_HINTS.get(singular)


def fetch_sketchfab_meta(uid: str) -> dict:
    """Public Data API (no token): categories, tags, thumbnails."""
    try:
        return sf.http_get_json(f"https://api.sketchfab.com/v3/models/{uid}", timeout=60)
    except Exception:
        return {}


def guess_category(meta: dict) -> str:
    found = {
        SKETCHFAB_CATEGORIES[c["slug"]]
        for c in meta.get("categories") or []
        if c.get("slug") in SKETCHFAB_CATEGORIES
    }
    if not found:
        for t in meta.get("tags") or []:
            cat = normalize_category(t.get("slug") or t.get("name"))
            if cat:
                found.add(cat)
    for cat in CATEGORY_PRIORITY:
        if cat in found:
            return cat
    return next(iter(found), "other")


def sketchfab_tags(meta: dict, limit: int = 30) -> list[str]:
    out: list[str] = []
    for t in meta.get("tags") or []:
        slug = re.sub(r"[^a-z0-9]+", "-", str(t.get("slug") or t.get("name") or "").lower()).strip("-")
        if slug and slug not in out:
            out.append(slug)
    return out[:limit]


def _thumbnail_url(meta: dict) -> Optional[str]:
    images = (meta.get("thumbnails") or {}).get("images") or []
    if not images:
        return None
    # ~1024px is plenty for a catalog card; fall back to the largest one
    sized = sorted(images, key=lambda i: int(i.get("width") or 0))
    for img in sized:
        if int(img.get("width") or 0) >= 1024:
            return img.get("url")
    return sized[-1].get("url")


def _local_preview(model: Path) -> Optional[Path]:
    """Cover image saved next to the glTF (same stem). ModelFolder requires that pair."""
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = model.with_suffix(ext)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _model_file(model_dir: Path) -> Path:
    files = list(model_dir.glob("*.gltf")) + list(model_dir.glob("*.glb"))
    if not files:
        raise RuntimeError("no glTF/GLB produced — cannot add to catalog")
    return max(files, key=lambda p: p.stat().st_size)


def _gltf_sidecars(model: Path) -> list[Path]:
    """External buffers/images referenced by a .gltf (embedded data: URIs need nothing)."""
    if model.suffix.lower() != ".gltf":
        return []
    try:
        doc = json.loads(model.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for entry in (doc.get("buffers") or []) + (doc.get("images") or []):
        uri = entry.get("uri") or ""
        if uri and not uri.startswith("data:") and "://" not in uri:
            p = (model.parent / uri).resolve()
            if p.is_file() and model.parent.resolve() in p.parents:
                out.append(p)
    return out


def build_archive(model_dir: Path, display_name: str, source_url: str, meta: dict) -> tuple[bytes, str]:
    """ZIP with <Name>/<Name>.gltf + <Name>.jpg preview + <Name>.url + textures/."""
    model = _model_file(model_dir)
    stem = sf.sanitize_name(display_name) or model.stem
    folder = stem

    local = _local_preview(model)
    if local:
        preview = local.read_bytes()
        preview_ext = ".jpg" if local.suffix.lower() == ".jpeg" else local.suffix.lower()
    else:
        thumb = _thumbnail_url(meta)
        if not thumb:
            raise RuntimeError("No preview image — ModelFolder skips models without a same-stem cover")
        preview = sf.http_get_bytes(thumb, timeout=60, label="thumbnail")
        preview_ext = ".jpg"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(model, f"{folder}/{stem}{model.suffix.lower()}")
        for side in _gltf_sidecars(model):
            zf.write(side, f"{folder}/{side.relative_to(model_dir.resolve())}")
        tex_dir = model_dir / "textures"
        if tex_dir.is_dir():
            for p in sorted(tex_dir.rglob("*")):
                if p.is_file() and p.suffix.lower() != ".json":
                    zf.write(p, f"{folder}/{p.relative_to(model_dir)}")
        zf.writestr(f"{folder}/{stem}{preview_ext}", preview)
        zf.writestr(f"{folder}/{stem}.url", f"[InternetShortcut]\nURL={source_url}\n")
    return buf.getvalue(), f"{folder}/{stem}{model.suffix.lower()}"


def _session() -> requests.Session:
    s = requests.Session()
    username = _env("CATALOG_USERNAME")
    if username:
        s.auth = (username, _env("CATALOG_PASSWORD"))
    s.headers["Accept"] = "application/json"
    return s


def send_to_catalog(
    model_dir: Path,
    *,
    uid: str,
    display_name: str,
    source_url: str,
    category: Optional[str] = None,
) -> dict:
    """Upload one downloaded model folder to ModelFolder. Returns a result dict."""
    if not is_configured():
        raise RuntimeError("CATALOG_URL is not set on the service")

    meta = fetch_sketchfab_meta(uid)
    chosen = normalize_category(category) if category else None
    if category and not chosen:
        raise RuntimeError(f"unknown catalog category {category!r}; allowed: {', '.join(CATEGORIES)}")
    chosen = chosen or guess_category(meta)

    data, rel_model = build_archive(model_dir, display_name, source_url, meta)
    s = _session()
    r = s.post(
        f"{catalog_url()}/api/admin/upload",
        files=[("files", (f"{uid}.zip", data, "application/zip"))],
        data={"category": chosen},
        timeout=catalog_timeout(),
    )
    if r.status_code >= 400:
        raise RuntimeError(f"catalog upload HTTP {r.status_code}: {r.text[:300]}")
    resp = r.json()
    if not resp.get("ok") and not resp.get("duplicates") and not resp.get("already_known"):
        raise RuntimeError(f"catalog rejected model: {resp.get('error') or resp.get('errors') or resp}")

    category_used = resp.get("category") or chosen
    path = f"{category_used}/{rel_model}"
    card = f"{catalog_url().rstrip('/')}/?path={quote(path, safe='')}"
    tags = sketchfab_tags(meta)
    tags_added = False
    if tags and resp.get("uploaded"):
        try:
            t = s.post(f"{catalog_url()}/api/tags/append", json={"path": path, "tags": tags}, timeout=60)
            tags_added = t.status_code < 400
        except Exception:
            pass

    return {
        "category": category_used,
        "path": path,
        "url": card,
        "uploaded": int(resp.get("uploaded") or 0),
        "duplicate": bool(resp.get("duplicates") or resp.get("already_known")),
        "tags": tags if tags_added else [],
        "sketchfab_categories": [c.get("slug") for c in meta.get("categories") or []],
    }

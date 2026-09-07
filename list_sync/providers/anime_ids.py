"""
Anime ID mapping (fork addition).

Resolves AniList / MyAnimeList IDs to TheTVDB / IMDb IDs using the
community-maintained mapping published by the Kometa team:

    https://github.com/Kometa-Team/Anime-IDs  (anime_ids.json)

This replaces the upstream dependency on the Trakt API for AniList
title -> ID resolution. Trakt made API app creation VIP-only on
2026-07-30, so a Trakt client ID can no longer be obtained for free.

The JSON is keyed by AniDB ID; each value may contain: tvdb_id,
tvdb_season, tvdb_epoffset, mal_id, anilist_id, imdb_id.
"""

import logging
import os
import time
from typing import Any, Dict, Optional

import requests

ANIME_IDS_URL = "https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/master/anime_ids.json"
_CACHE_PATH = os.path.join(os.getenv("ANIME_IDS_CACHE_DIR", "/tmp"), "anime_ids.json")
_CACHE_TTL_SECONDS = 24 * 3600

# In-process indexes, built lazily.
_by_anilist: Optional[Dict[int, Dict[str, Any]]] = None
_by_mal: Optional[Dict[int, Dict[str, Any]]] = None


def _download_raw() -> Dict[str, Any]:
    """Fetch anime_ids.json, using a small on-disk cache (24h TTL)."""
    try:
        if os.path.exists(_CACHE_PATH) and (time.time() - os.path.getmtime(_CACHE_PATH)) < _CACHE_TTL_SECONDS:
            with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
                import json
                return json.load(fh)
    except Exception as exc:  # noqa: BLE001 - cache is best-effort
        logging.debug(f"anime_ids cache read failed ({exc}); refetching")

    logging.info("🗺️  Downloading Kometa Anime-IDs mapping (anime_ids.json)")
    resp = requests.get(ANIME_IDS_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as fh:
            fh.write(resp.text)
    except Exception as exc:  # noqa: BLE001
        logging.debug(f"anime_ids cache write failed ({exc})")

    return data


def _iter_ids(value: Any):
    """Yield int ids from a field that may be an int, "123" or "123,456"."""
    if value is None:
        return
    for part in str(value).split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            yield int(part)


def _build_indexes() -> None:
    global _by_anilist, _by_mal
    if _by_anilist is not None:
        return

    _by_anilist = {}
    _by_mal = {}

    try:
        raw = _download_raw()
    except Exception as exc:  # noqa: BLE001
        logging.warning(f"⚠️  Could not load Anime-IDs mapping: {exc}. AniList resolution will fall back to TMDB search.")
        return

    for entry in raw.values():
        if not isinstance(entry, dict):
            continue
        # Prefer the first entry that carries a usable external ID.
        has_ext = bool(entry.get("tvdb_id") or entry.get("imdb_id"))
        for anilist_id in _iter_ids(entry.get("anilist_id")):
            if anilist_id not in _by_anilist or has_ext:
                _by_anilist[anilist_id] = entry
        for mal_id in _iter_ids(entry.get("mal_id")):
            if mal_id not in _by_mal or has_ext:
                _by_mal[mal_id] = entry

    logging.info(f"🗺️  Anime-IDs mapping ready: {len(_by_anilist)} AniList entries, {len(_by_mal)} MAL entries")


def lookup(anilist_id: Optional[int] = None, mal_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Return {'tvdb_id': int|None, 'imdb_id': str|None} for the given AniList
    or MAL id. Empty dict-ish result if nothing is known.
    """
    _build_indexes()
    entry: Optional[Dict[str, Any]] = None

    if anilist_id and _by_anilist:
        entry = _by_anilist.get(int(anilist_id))
    if entry is None and mal_id and _by_mal:
        entry = _by_mal.get(int(mal_id))

    if not entry:
        return {"tvdb_id": None, "imdb_id": None}

    tvdb_id = entry.get("tvdb_id")
    if isinstance(tvdb_id, int) and tvdb_id <= 0:
        tvdb_id = None

    return {"tvdb_id": tvdb_id, "imdb_id": entry.get("imdb_id")}

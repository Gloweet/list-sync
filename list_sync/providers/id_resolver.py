"""
TMDB-based ID resolution (fork addition).

Upstream resolves external IDs (IMDb / title) to a TMDB ID through the
Trakt API. Trakt made API app creation VIP-only on 2026-07-30, so this
fork resolves through TMDB directly instead:

  * TheTVDB id  -> TMDB id   via  GET /find/{id}?external_source=tvdb_id
  * IMDb id     -> TMDB id   via  GET /find/{id}?external_source=imdb_id
  * title/year  -> TMDB id   via  GET /search/{tv,movie}

Requires a (free) TMDB API key in the TMDB_KEY env var. When TMDB_KEY is
unset every function returns None and callers fall back to Overseerr's
own title search.
"""

import logging
from typing import Any, Dict, Optional

import requests

from ..config import get_tmdb_api_key

_TMDB_BASE = "https://api.themoviedb.org/3"


def _get(path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    api_key = get_tmdb_api_key()
    if not api_key:
        return None
    try:
        resp = requests.get(
            f"{_TMDB_BASE}{path}",
            params={"api_key": api_key, **params},
            timeout=20,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logging.warning(f"⚠️  TMDB request failed ({path}): {exc}")
        return None


def _pick(data: Dict[str, Any], prefer: str) -> Optional[Dict[str, Any]]:
    """Pick a result from a /find response, preferring the given media kind."""
    order = ("tv_results", "movie_results") if prefer == "tv" else ("movie_results", "tv_results")
    for key in order:
        results = data.get(key) or []
        if results:
            media_type = "tv" if key == "tv_results" else "movie"
            return {"tmdb_id": str(results[0]["id"]), "media_type": media_type}
    return None


def resolve_by_tvdb_id(tvdb_id: int) -> Optional[Dict[str, str]]:
    data = _get(f"/find/{tvdb_id}", {"external_source": "tvdb_id"})
    if not data:
        return None
    return _pick(data, prefer="tv")


def resolve_by_imdb_id(imdb_id: str, prefer: str = "movie") -> Optional[Dict[str, str]]:
    if not imdb_id:
        return None
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"
    data = _get(f"/find/{imdb_id}", {"external_source": "imdb_id"})
    if not data:
        return None
    return _pick(data, prefer=prefer)


def resolve_by_title(title: str, year: Optional[int], media_type: str) -> Optional[Dict[str, str]]:
    """Title/year search fallback. media_type: 'tv' or 'movie'."""
    if not title:
        return None
    path = "/search/tv" if media_type == "tv" else "/search/movie"
    params: Dict[str, Any] = {"query": title, "include_adult": "false"}
    if year:
        params["first_air_date_year" if media_type == "tv" else "primary_release_year"] = year
    data = _get(path, params)
    results = (data or {}).get("results") or []
    if not results:
        return None
    return {"tmdb_id": str(results[0]["id"]), "media_type": media_type}

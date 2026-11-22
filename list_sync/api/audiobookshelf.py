"""
Audiobookshelf API client for ListSync (fork addition).

Audiobookshelf (https://www.audiobookshelf.org/) is the audiobook/podcast
library server. ListSync uses it read-only to check whether a book already
exists as an audiobook before asking Shelfmark for a download - the audiobook
counterpart of the Komga check for ebooks.

API reference: https://api.audiobookshelf.org/
"""

import logging
import unicodedata
from typing import Any, Dict, Optional

import requests


def _normalize(value: Optional[str]) -> str:
    """Lowercase and strip accents so "Beren et Luthien" matches "Béren et Lúthien"."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().lower()


class AudiobookshelfClient:
    """Read-only client for the Audiobookshelf API."""

    def __init__(self, base_url: str, api_key: str, library_id: Optional[str] = None):
        """
        Initialize the Audiobookshelf API client.

        Args:
            base_url (str): Audiobookshelf server URL (e.g. http://audiobookshelf.arr.svc.cluster.local)
            api_key (str): Audiobookshelf API key
            library_id (str, optional): Book library id. When omitted, the
                first (or "Audiobooks"-named) book library is resolved lazily.
        """
        base_url = (base_url or "").strip()
        if base_url and "://" not in base_url:
            base_url = f"http://{base_url}"
        self.base_url = base_url.rstrip('/')
        self.library_id = library_id
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET an ABS endpoint. Raises on transport/HTTP error so callers can
        tell "library unreachable" apart from "book not in the library"."""
        response = requests.get(
            f"{self.base_url}{path}", headers=self.headers, params=params, timeout=20
        )
        response.raise_for_status()
        return response.json()

    def _resolve_library_id(self) -> str:
        if self.library_id:
            return self.library_id
        libraries = self._get("/api/libraries").get("libraries", []) or []
        book_libraries = [lib for lib in libraries if lib.get("mediaType") == "book"]
        if not book_libraries:
            raise RuntimeError("Audiobookshelf has no book library")
        picked = next(
            (lib for lib in book_libraries if _normalize(lib.get("name")) == "audiobooks"),
            book_libraries[0],
        )
        self.library_id = picked["id"]
        logging.info(f"Audiobookshelf: using library '{picked.get('name')}' ({self.library_id})")
        return self.library_id

    def find_book_by_title_author(self, title: str, author: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Find a book in the Audiobookshelf library by title (and author).

        Returns:
            The matching library item dict, None if the book is not in the
            library. Raises on API/transport errors.
        """
        library_id = self._resolve_library_id()
        # ABS's search is case-insensitive but not accent-insensitive, so
        # strip accents from the query ("Béren et Lúthien" -> "beren et luthien").
        query = _normalize(f"{title} {author}" if author else title)
        data = self._get(
            f"/api/libraries/{library_id}/items", params={"limit": 10, "search": query}
        )
        results = data.get("results", []) or []

        title_n = _normalize(title)
        author_n = _normalize(author)

        for item in results:
            metadata = (item.get("media") or {}).get("metadata") or {}
            item_title = _normalize(metadata.get("title"))
            if not item_title:
                continue
            title_exact = item_title == title_n
            if not title_exact and not (
                len(title_n) >= 3 and (title_n in item_title or item_title in title_n)
            ):
                continue
            # An exact title match is enough: ABS often files a book under the
            # narrator ("authorName") or truncates the author list, so a strict
            # author check would miss books that are plainly there.
            if author_n and not title_exact:
                authors = [a.get("name") for a in metadata.get("authors") or []]
                if metadata.get("authorName"):
                    authors.append(metadata["authorName"])
                authors = [_normalize(name) for name in authors if name]
                if authors and not any(author_n in name or name in author_n for name in authors):
                    continue
            return item

        return None

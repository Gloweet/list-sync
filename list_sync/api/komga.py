"""
Komga API client for ListSync.

Komga (https://komga.org/) is a book/comics library manager. ListSync uses it
to check if a book is already in the library before downloading, and to tag
downloaded books with collection tags (e.g. the source list name like
"Librairie de PheonBest").

API notes (verified against Komga 1.30):
- book search is `GET /api/v1/books?search=<q>` (there is no /books/search)
- a book's title/authors live in `metadata.title` / `metadata.authors`
- a book's language lives on its series (`GET /api/v1/series/{id}`)
- tags are plain strings in the book metadata, updated with
  `PATCH /api/v1/books/{id}/metadata {"tags": [...]}`

API reference: https://komga.org/docs/api/
"""

import logging
from typing import Any, Dict, List, Optional

import requests


class KomgaClient:
    """Client for interacting with the Komga API."""

    def __init__(self, base_url: str, api_key: str):
        """
        Initialize the Komga API client.

        Args:
            base_url (str): Komga server URL (e.g. http://komga:8080)
            api_key (str): Komga API key
        """
        base_url = (base_url or "").strip()
        if base_url and "://" not in base_url:
            host = base_url.split("/", 1)[0]
            has_port = ":" in host
            base_url = f"http://{base_url}" if has_port else f"http://{base_url}:8080"
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        # Komga authenticates API keys with X-API-Key, not a Bearer token.
        self.headers = {
            "X-API-Key": api_key,
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Make an authenticated request to the Komga API."""
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(method, url, headers=self.headers, timeout=30, **kwargs)
            if response.status_code >= 400:
                logging.error(f"Komga API {method} {path} returned {response.status_code}: {response.text[:200]}")
                return None
            try:
                return response.json()
            except ValueError:
                return {}
        except Exception as exc:  # noqa: BLE001
            logging.error(f"Komga API {method} {path} failed: {exc}")
            return None

    def test_connection(self) -> bool:
        """Test the connection and the API key."""
        result = self._request("GET", "/api/v1/books", params={"size": 1})
        if result is not None:
            logging.info("Komga API connection successful!")
            return True
        return False

    def search_book(self, query: str, size: int = 10) -> Optional[List[Dict[str, Any]]]:
        """
        Search Komga for a book by title/author.

        Args:
            query: Free-text search query
            size: Maximum number of results

        Returns:
            List of matching book objects, empty list if none found, None on error
        """
        result = self._request("GET", "/api/v1/books", params={"search": query, "size": size})
        if result is None:
            return None
        return result.get("content", []) or []

    def get_book(self, book_id: str) -> Optional[Dict[str, Any]]:
        """Get a single book by its Komga ID."""
        return self._request("GET", f"/api/v1/books/{book_id}")

    def get_series(self, series_id: str) -> Optional[Dict[str, Any]]:
        """Get a series (books carry their language on the series)."""
        return self._request("GET", f"/api/v1/series/{series_id}")

    def book_language(self, book: Dict[str, Any]) -> Optional[str]:
        """Language of a book, read from its series metadata."""
        series_id = book.get("seriesId")
        if not series_id:
            return None
        series = self.get_series(series_id) or {}
        return (series.get("metadata") or {}).get("language")

    def _with_language(self, book: Dict[str, Any]) -> Dict[str, Any]:
        """Copy the series language into the book metadata for the callers."""
        language = self.book_language(book)
        if not language:
            return book
        enriched = dict(book)
        metadata = dict(book.get("metadata") or {})
        metadata["language"] = language
        enriched["metadata"] = metadata
        return enriched

    def find_book_by_title_author(self, title: str, author: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Find the best matching book in Komga by title and author.

        Returns:
            Best matching book object dict (with its series language in
            metadata.language), or None if not found/error
        """
        matches = self.search_book(title)
        if not matches:
            return None

        title_lower = title.lower()
        author_lower = (author or "").lower()

        for book in matches:
            metadata = book.get("metadata") or {}
            book_title = (metadata.get("title") or book.get("name") or "").lower()
            book_authors = [a.get("name", "").lower() for a in metadata.get("authors") or []]

            if title_lower == book_title or title_lower in book_title:
                if (
                    not author_lower
                    or not book_authors
                    or any(author_lower in name or name in author_lower for name in book_authors)
                ):
                    return self._with_language(book)

        # Komga's search is relevance-ranked; fall back to its first result.
        return self._with_language(matches[0])

    def scan_libraries(self) -> int:
        """Ask Komga to rescan every library.

        The media volume is NFS, so Komga's watcher does not reliably see
        files downloaded by qBittorrent; a scan is what makes them appear.

        Returns:
            Number of libraries scanned. Raises on permission/transport error.
        """
        libraries = self._request("GET", "/api/v1/libraries")
        if libraries is None:
            raise RuntimeError("Komga: could not list libraries")
        scanned = 0
        for library in libraries:
            if self._request("POST", f"/api/v1/libraries/{library['id']}/scan") is None:
                raise RuntimeError(f"Komga: scan failed for library '{library.get('name')}'")
            scanned += 1
        return scanned

    def add_tags_to_book(self, book_id: str, tags: List[str]) -> bool:
        """Add tags to a book's metadata (Komga tags are plain strings)."""
        book = self.get_book(book_id)
        if not book:
            return False
        existing = list((book.get("metadata") or {}).get("tags") or [])
        missing = [tag for tag in tags if tag not in existing]
        if not missing:
            return True
        result = self._request(
            "PATCH", f"/api/v1/books/{book_id}/metadata", json={"tags": existing + missing}
        )
        return result is not None

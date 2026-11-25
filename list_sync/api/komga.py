"""
Komga API client for ListSync.

Komga (https://komga.org/) is a book/comics library manager. ListSync uses it
to check if a book is already in the library before downloading, and to tag
downloaded books with collection tags (e.g. the source list name like
"Librairie de PheonBest").

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
        """Test the connection to the Komga API."""
        result = self._request("GET", "/api/v1/books?size=1")
        if result is not None:
            logging.info("Komga API connection successful!")
            return True
        return False

    def search_book(self, query: str) -> Optional[List[Dict[str, Any]]]:
        """
        Search Komga for a book by title/author.

        Args:
            query: Free-text search query

        Returns:
            List of matching book objects, empty list if none found, None on error
        """
        result = self._request("GET", "/api/v1/books/search", params={"query": query, "size": 10})
        if result is None:
            return None
        return result.get("content", []) or []

    def get_book(self, book_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a single book by its Komga ID.

        Args:
            book_id: Komga book UUID

        Returns:
            Book object dict, or None if not found/error
        """
        return self._request("GET", f"/api/v1/books/{book_id}")

    def get_tags(self) -> Optional[List[Dict[str, Any]]]:
        """
        List all tags in Komga.

        Returns:
            List of tag objects, empty list if none, None on error
        """
        result = self._request("GET", "/api/v1/tags")
        if result is None:
            return None
        return result.get("content", []) or []

    def create_tag(self, name: str, color: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Create a tag in Komga if it doesn't exist yet.

        Args:
            name: Tag name
            color: Optional hex color (e.g. "#ff0000")

        Returns:
            Tag object dict, or None on error
        """
        payload: Dict[str, Any] = {"name": name}
        if color:
            payload["color"] = color
        return self._request("POST", "/api/v1/tags", json=payload)

    def find_or_create_tag(self, name: str) -> Optional[str]:
        """
        Find a tag by name, or create it if it doesn't exist.

        Args:
            name: Tag name

        Returns:
            Tag UUID string, or None on error
        """
        tags = self.get_tags() or []
        for tag in tags:
            if tag.get("name") == name:
                return tag.get("id")
        # Tag doesn't exist, create it
        created = self.create_tag(name)
        if created:
            return created.get("id")
        return None

    def add_tag_to_book(self, book_id: str, tag_id: str) -> bool:
        """
        Add a tag to a book.

        Args:
            book_id: Komga book UUID
            tag_id: Komga tag UUID

        Returns:
            True on success, False on error
        """
        result = self._request("POST", f"/api/v1/books/{book_id}/tags/{tag_id}")
        return result is not None

    def remove_tag_from_book(self, book_id: str, tag_id: str) -> bool:
        """
        Remove a tag from a book.

        Args:
            book_id: Komga book UUID
            tag_id: Komga tag UUID

        Returns:
            True on success, False on error
        """
        result = self._request("DELETE", f"/api/v1/books/{book_id}/tags/{tag_id}")
        return result is not None

    def tag_book_with_collections(self, book_id: str, collection_tags: List[str]) -> bool:
        """
        Tag a book with one or more collection tags.

        Args:
            book_id: Komga book UUID
            collection_tags: List of tag names to add

        Returns:
            True if all tags were added successfully, False if any failed
        """
        success = True
        for tag_name in collection_tags:
            tag_id = self.find_or_create_tag(tag_name)
            if not tag_id:
                logging.warning(f"Komga: could not find or create tag '{tag_name}'")
                success = False
                continue
            if not self.add_tag_to_book(book_id, tag_id):
                logging.warning(f"Komga: could not add tag '{tag_name}' to book {book_id}")
                success = False
        return success

    def book_has_tag(self, book_id: str, tag_name: str) -> bool:
        """
        Check if a book already has a specific tag.

        Args:
            book_id: Komga book UUID
            tag_name: Tag name to check

        Returns:
            True if the book has the tag, False otherwise
        """
        book = self.get_book(book_id)
        if not book:
            return False
        tags = book.get("tags", []) or []
        return any(tag.get("name") == tag_name for tag in tags)

    def search_book_by_title_author(self, title: str, author: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """
        Search Komga for a book by title and optionally author.

        Args:
            title: Book title
            author: Optional author name

        Returns:
            List of matching book objects
        """
        query = f"{title} {author}" if author else title
        return self.search_book(query)

    def find_book_by_title_author(self, title: str, author: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Find the best matching book in Komga by title and author.

        Args:
            title: Book title
            author: Optional author name

        Returns:
            Best matching book object dict, or None if not found/error
        """
        matches = self.search_book_by_title_author(title, author)
        if matches is None:
            return None
        if not matches:
            return None

        # Try to find an exact title match first
        title_lower = title.lower()
        author_lower = (author or "").lower()

        for book in matches:
            book_title = (book.get("title") or "").lower()
            book_authors = [a.get("name", "").lower() for a in book.get("authors", []) or []]

            if title_lower == book_title:
                if not author_lower or any(author_lower in ba for ba in book_authors):
                    return book

        # Return the first match as fallback
        return matches[0]
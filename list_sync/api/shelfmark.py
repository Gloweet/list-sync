"""
Shelfmark API client for the ListSync application (fork addition).

Shelfmark (https://github.com/calibrain/shelfmark) is a self-hosted book
search & request tool - the book equivalent of Overseerr for movies/TV.
ListSync uses its admin API key to search metadata providers, list releases
for a matched book, and queue a download.

API reference: https://github.com/calibrain/shelfmark/blob/main/docs/api-access.md

Note: unlike Overseerr, Shelfmark has no concept of "already in your
library" (it is a search/download tool, not a library manager - see its
README "Project Scope" section), so there is no is_available/is_requested
check here. A book is either matched + a release queued ("requested"), or
it isn't ("not_found").
"""

import logging
import os
from typing import Any, Dict, List, Optional

import requests


class ShelfmarkClient:
    """Client for interacting with the Shelfmark API."""

    def __init__(self, base_url: str, api_key: str):
        """
        Initialize the Shelfmark API client.

        Args:
            base_url (str): Shelfmark server URL (e.g. http://shelfmark:8084)
            api_key (str): SHELFMARK_API_KEY admin key (see docs/api-access.md)
        """
        base_url = (base_url or "").strip()
        if base_url and "://" not in base_url:
            # Bare host with no scheme. If no explicit port, default to
            # Shelfmark's own default port (8084).
            host = base_url.split("/", 1)[0]
            has_port = ":" in host
            base_url = f"http://{base_url}" if has_port else f"http://{base_url}:8084"
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        # Either header works per Shelfmark's docs; Authorization is the documented default.
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def test_connection(self) -> bool:
        """
        Test the connection to the Shelfmark API.

        Raises:
            Exception: If the connection test fails
        """
        status_url = f"{self.base_url}/api/status"
        try:
            response = requests.get(status_url, headers=self.headers, timeout=15)
            response.raise_for_status()
            logging.info("Shelfmark API connection successful!")
            return True
        except Exception as e:
            logging.error(f"Shelfmark API connection failed. Error: {str(e)}")
            raise

    def search_metadata(self, query: str) -> Optional[List[Dict[str, Any]]]:
        """
        Search metadata providers (Hardcover, Open Library, Google Books, ...)
        configured in Shelfmark for a book matching `query`.

        Args:
            query (str): Free-text search query (e.g. "Dune Frank Herbert")

        Returns:
            List[Dict[str, Any]]: Matching books, each with at least
                "provider" and "provider_id"; empty list if the search ran
                but matched nothing; None if the request itself failed
                (instance down, auth error, ...) so callers can distinguish
                "not found" from "couldn't check".
        """
        search_url = f"{self.base_url}/api/metadata/search"
        try:
            try:
                timeout = int(os.getenv("SHELFMARK_SEARCH_TIMEOUT", "30"))
            except ValueError:
                timeout = 30
            response = requests.get(
                search_url, headers=self.headers, params={"query": query}, timeout=timeout
            )
            response.raise_for_status()
            data = response.json()
            return data.get("books", []) or []
        except Exception as e:
            logging.error(f"Shelfmark metadata search failed for '{query}': {str(e)}")
            return None

    def get_releases(
        self, provider: str, book_id: str, content_type: str = "ebook"
    ) -> List[Dict[str, Any]]:
        """
        List downloadable releases for a book identified by a metadata provider.

        Note on timing: Shelfmark's own release-search budget is 300s by
        default (RELEASE_SEARCH_TIMEOUT - see shelfmark/core/search_deadline.py),
        and the API only answers once it's spent that budget or found releases.
        A client timeout shorter than that budget just produces a false
        "no releases" - so this defaults to 310s and can be tuned with
        SHELFMARK_RELEASE_TIMEOUT (seconds).

        Args:
            provider (str): Metadata provider name (e.g. "hardcover")
            book_id (str): Provider-specific book id
            content_type (str): "ebook" or "audiobook"

        Returns:
            List[Dict[str, Any]]: Available releases, each suitable to pass
                directly to download_release(); empty list if the search ran
                but found nothing; None if the search itself failed (Shelfmark
                answers 503 with {"error": <cause>} when no source could be
                reached - e.g. the download source is behind an unsolvable
                protection challenge).
        """
        releases_url = f"{self.base_url}/api/releases"
        params = {"provider": provider, "book_id": book_id, "content_type": content_type}
        try:
            timeout = int(os.getenv("SHELFMARK_RELEASE_TIMEOUT", "310"))
        except ValueError:
            timeout = 310
        try:
            response = requests.get(releases_url, headers=self.headers, params=params, timeout=timeout)
            if response.status_code == 503:
                try:
                    cause = response.json().get("error", "")
                except Exception:  # noqa: BLE001
                    cause = ""
                logging.error(f"Shelfmark release search failed for {provider}:{book_id}: {cause}")
                return None
            response.raise_for_status()
            data = response.json()
            return data.get("releases", []) or []
        except Exception as e:
            logging.error(f"Shelfmark release lookup failed for {provider}:{book_id}: {str(e)}")
            return None

    def download_release(self, release: Dict[str, Any]) -> bool:
        """
        Queue a release for download.

        Args:
            release (Dict[str, Any]): One of the release objects returned by
                get_releases() - must contain at least "source" and "source_id"

        Returns:
            bool: True if the download was queued successfully
        """
        download_url = f"{self.base_url}/api/releases/download"
        try:
            response = requests.post(download_url, headers=self.headers, json=release, timeout=20)
            response.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Shelfmark download request failed: {str(e)}")
            return False


class _ShelfmarkClientPlaceholder:  # pragma: no cover - marker only
    pass

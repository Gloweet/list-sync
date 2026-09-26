"""
Babelio provider for ListSync (fork addition).

Babelio (https://www.babelio.com) is a French books community site. This
provider scrapes the authenticated account's own library
("mabibliotheque.php", paginated) and returns book items for the
Shelfmark sync target (see list_sync/api/shelfmark.py).

Authentication is the hard part: every single page on babelio.com,
including the homepage, sits behind a WAF that serves a "drag the slider to
verify you're human" challenge to anything that isn't a real browser. There
is no way around it with plain HTTP requests. So this provider:

1. Tries to reuse a cached, previously-authenticated `requests` session
   (cookies persisted to data/babelio_session.json). Once logged in, plain
   requests work fine for scraping - the WAF only interactively challenges
   unauthenticated/unrecognised sessions.
2. Falls back to a one-off Selenium (SeleniumBase, undetected-chromedriver
   mode) login when the cached session is missing or stale: solves the
   slider captcha with a human-like drag (ActionChains), fills the login
   form with BABELIO_EMAIL/BABELIO_PASSWORD, and caches the resulting
   session cookies for next time.

This is inherently more fragile than the other providers (it depends on
Babelio's anti-bot challenge not changing shape). If login starts failing,
check the logs for the exact step that failed - the DOM selectors used here
(#slider-track, #slider-thumb, data-drag-distance) were verified against the
live site, but the post-captcha login form field names are best-effort
(matched generically by input type, not hardcoded names).
"""

import html as html_module
import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional

import requests

from . import register_provider, check_and_raise_if_cancelled, SyncCancelledException
from ..config import get_babelio_credentials
from ..utils.logger import DATA_DIR

BASE_URL = "https://www.babelio.com"

_SESSION_CACHE_PATH = os.path.join(DATA_DIR, "babelio_session.json")

_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr,fr-FR;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Row extraction regexes, matched against the raw HTML of one mabibliotheque.php page.
_ROW_SPLIT_RE = re.compile(r'<tr><td class="check">')
_TITLE_RE = re.compile(r'<td class="titre_livre"><a href="([^"]+)"[^>]*><h2>(.*?)</h2></a>', re.DOTALL)
_YEAR_RE = re.compile(r'</h2></a><p[^>]*>(\d{4})</p>')
_AUTHOR_RE = re.compile(r'<td class="auteur"><a[^>]*>(.*?)</a></td>', re.DOTALL)
_EDITOR_RE = re.compile(r'<span class="titre_livre_editor">(.*?)</span>', re.DOTALL)
_BABELIO_BOOK_ID_RE = re.compile(r'/livres/[^/"]+/(\d+)')


# ---------------------------------------------------------------------------
# Session cache
# ---------------------------------------------------------------------------

def _load_cached_cookies() -> Optional[Dict[str, str]]:
    try:
        if not os.path.exists(_SESSION_CACHE_PATH):
            return None
        with open(_SESSION_CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        cookies = data.get("cookies")
        return cookies if cookies else None
    except Exception as exc:  # noqa: BLE001 - cache is best-effort
        logging.debug(f"Babelio: could not read cached session ({exc})")
        return None


def _save_cookies(cookies: Dict[str, str]) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(_SESSION_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"cookies": cookies, "saved_at": time.time()}, fh)
        logging.debug("Babelio: session cookies cached to disk")
    except Exception as exc:  # noqa: BLE001
        logging.debug(f"Babelio: could not cache session cookies ({exc})")


def _build_session(cookies: Dict[str, str]) -> requests.Session:
    session = requests.Session()
    session.headers.update(_REQUEST_HEADERS)
    for name, value in cookies.items():
        session.cookies.set(name, value, domain="www.babelio.com")
    return session


def _is_authenticated_page(html_text: str) -> bool:
    """True if `html_text` is a real, logged-in mabibliotheque.php page (not
    the WAF's "Vérification de sécurité" interstitial and not a logged-out
    redirect)."""
    if "Vérification de sécurité" in html_text:
        return False
    return "mes_livres_con" in html_text or 'id="hid_user"' in html_text


# ---------------------------------------------------------------------------
# Selenium login (only used when the cached session is missing/stale)
# ---------------------------------------------------------------------------

def _solve_slider_captcha(sb, timeout: int = 12) -> bool:
    """
    Solve Babelio's "glisser pour vérifier" drag-slider WAF challenge, if
    present on the current page. Returns True if no challenge was present,
    or if it was solved successfully.
    """
    from selenium.webdriver.common.by import By

    try:
        sb.wait_for_element_present("#slider-track", timeout=timeout)
    except Exception:
        return True  # No challenge on this page.

    try:
        from selenium.webdriver.common.action_chains import ActionChains

        track = sb.driver.find_element(By.ID, "slider-track")
        thumb = sb.driver.find_element(By.ID, "slider-thumb")
        distance = int(track.get_attribute("data-drag-distance") or "231")

        actions = ActionChains(sb.driver)
        actions.move_to_element(thumb)
        actions.click_and_hold()
        actions.pause(0.15)

        steps = random.randint(12, 18)
        step_size = distance / steps
        for _ in range(steps):
            jitter_y = random.randint(-2, 2)
            actions.move_by_offset(step_size, jitter_y)
            actions.pause(random.uniform(0.02, 0.06))
        actions.pause(0.2)
        actions.release()
        actions.perform()
        sb.sleep(1.5)
    except Exception as exc:  # noqa: BLE001
        logging.warning(f"Babelio: slider captcha drag attempt failed: {exc}")
        return False

    try:
        sb.wait_for_element_not_present("#slider-track", timeout=8)
        return True
    except Exception:
        return False


def _dismiss_consent_if_present(sb) -> None:
    """
    Close the appconsent cookie banner if it covers the page (it blocks the
    submit button with a full-screen iframe). Prefers declining non-essential
    cookies ("Continue without accepting"); falls back to "Accept all".
    """
    try:
        frames = sb.find_elements('iframe[title="Consent window"]')
        if not frames:
            return
        sb.switch_to_frame('iframe[title="Consent window"]')
        for text in ("Continue without accepting", "Accept all"):
            buttons = sb.find_elements("tag name", "button")
            for button in buttons:
                if text.lower() in (button.text or "").lower():
                    button.click()
                    sb.switch_to_default_content()
                    sb.sleep(1)
                    return
        sb.switch_to_default_content()
    except Exception as exc:  # noqa: BLE001 - consent is best-effort
        logging.debug(f"Babelio: could not dismiss consent banner ({exc})")


def _fill_login_form(sb, email: str, password: str) -> bool:
    """
    Fill and submit Babelio's login form (/connection.php).

    The form fields are name="Login" and name="Password" (verified against
    the live site). We try those exact names first, then fall back to
    generic matching by input type in case Babelio changes the markup.
    """
    email_field = None
    for selector in (
        "input[name='Login']",
        "input[type='email']",
        "input[name*='mail' i]",
        "input[name*='login' i]",
        "input[type='text']",
    ):
        elements = sb.find_elements(selector)
        if elements:
            email_field = elements[0]
            break

    password_field = None
    for selector in ("input[name='Password']", "input[type='password']"):
        elements = sb.find_elements(selector)
        if elements:
            password_field = elements[0]
            break

    if not email_field or not password_field:
        logging.error("Babelio: login form fields not found on the page")
        return False

    email_field.clear()
    email_field.send_keys(email)
    password_field.clear()
    password_field.send_keys(password)

    for selector in ("input[name='sub_btn']", "button[type='submit']", "input[type='submit']"):
        elements = sb.find_elements(selector)
        if elements:
            elements[0].click()
            return True

    password_field.submit()
    return True


def _login_with_selenium(email: str, password: str) -> Optional[Dict[str, str]]:
    from seleniumbase import SB

    logging.info("🔐 Babelio: starting Selenium login (solving security check)...")
    # The WAF challenge is flaky - the first page load is sometimes the
    # "drag to verify" captcha, sometimes the login form, sometimes a retry
    # of the whole flow is needed. Retry a few times before giving up.
    last_error = None
    for attempt in range(1, 4):
        try:
            with SB(uc=True, headless=True) as sb:
                try:
                    sb.uc_open_with_reconnect(f"{BASE_URL}/connection.php", 4)
                except Exception:
                    sb.open(f"{BASE_URL}/connection.php")
                sb.sleep(1)

                solved = _solve_slider_captcha(sb)
                if not solved:
                    sb.sleep(1)
                    solved = _solve_slider_captcha(sb)
                if not solved:
                    logging.warning(f"Babelio: slider captcha not solved (attempt {attempt})")
                    _log_page_state(sb, "after-slider-fail")
                    continue

                sb.sleep(1)
                # The slider can reappear after the first solve - clear it again.
                _solve_slider_captcha(sb)
                _dismiss_consent_if_present(sb)

                if not _fill_login_form(sb, email, password):
                    logging.warning(f"Babelio: login form not found (attempt {attempt})")
                    _log_page_state(sb, "form-missing")
                    continue

                sb.sleep(3)
                # A slider challenge can appear right after submitting the form.
                _solve_slider_captcha(sb)
                sb.sleep(1)

                cookies = {c["name"]: c["value"] for c in sb.driver.get_cookies()}
                if "id_user" not in cookies:
                    logging.warning(
                        f"Babelio: login did not produce an authenticated session "
                        f"(no id_user cookie) - attempt {attempt} - check BABELIO_EMAIL/BABELIO_PASSWORD"
                    )
                    _log_page_state(sb, "no-id-user")
                    continue

                logging.info("✅ Babelio: login successful")
                return cookies
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logging.warning(f"Babelio: Selenium login attempt {attempt} raised an error: {exc}")

    logging.error(f"Babelio: Selenium login failed after 3 attempts ({last_error})")
    return None


def _log_page_state(sb, label: str) -> None:
    """Log what is actually on the page, for debugging the flaky WAF/login."""
    try:
        logging.info(
            f"Babelio: page state [{label}] title={sb.get_title()!r} "
            f"url={sb.get_current_url()!r} slider={len(sb.find_elements('#slider-track')) > 0} "
            f"body={sb.get_text('body')[:160]!r}"
        )
    except Exception as exc:  # noqa: BLE001
        logging.debug(f"Babelio: could not capture page state [{label}]: {exc}")


def _get_authenticated_session(email: str, password: str) -> requests.Session:
    cached = _load_cached_cookies()
    if cached:
        session = _build_session(cached)
        try:
            resp = session.get(f"{BASE_URL}/mabibliotheque.php?pageN=1", timeout=20)
            resp.encoding = "iso-8859-1"
            if resp.status_code == 200 and _is_authenticated_page(resp.text):
                logging.info("Babelio: reusing cached session cookies")
                return session
        except Exception as exc:  # noqa: BLE001
            logging.debug(f"Babelio: cached session check failed ({exc})")
        logging.info("Babelio: cached session is stale or invalid, logging in again")

    cookies = _login_with_selenium(email, password)
    if not cookies:
        raise RuntimeError(
            "Babelio login failed - could not pass the security check or authenticate. "
            "Check BABELIO_EMAIL/BABELIO_PASSWORD and the logs for details."
        )
    _save_cookies(cookies)
    return _build_session(cookies)


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def _strip_tags(text: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _parse_book_rows(html_text: str) -> List[Dict[str, Any]]:
    books = []
    rows = _ROW_SPLIT_RE.split(html_text)[1:]  # first chunk is page preamble, discard

    for row in rows:
        end = row.find("</tr>")
        if end != -1:
            row = row[:end]

        title_match = _TITLE_RE.search(row)
        if not title_match:
            continue
        href, raw_title = title_match.groups()
        title = _strip_tags(raw_title)
        if not title:
            continue

        book_id_match = _BABELIO_BOOK_ID_RE.search(href)
        babelio_id = book_id_match.group(1) if book_id_match else None

        year_match = _YEAR_RE.search(row)
        year = int(year_match.group(1)) if year_match else None

        author_match = _AUTHOR_RE.search(row)
        author = _strip_tags(author_match.group(1)) if author_match else None

        editor_match = _EDITOR_RE.search(row)
        editor = _strip_tags(editor_match.group(1)) if editor_match else None

        books.append(
            {
                "title": title,
                "author": author,
                "year": year,
                "media_type": "book",
                "babelio_id": babelio_id,
                "babelio_url": f"{BASE_URL}{href}" if href.startswith("/") else href,
                "editor": editor,
            }
        )

    return books


# ---------------------------------------------------------------------------
# Provider entrypoint
# ---------------------------------------------------------------------------

@register_provider("babelio")
def fetch_babelio_list(list_id: str = "mabibliotheque") -> List[Dict[str, Any]]:
    """
    Fetch the authenticated Babelio account's library (mabibliotheque.php,
    paginated). `list_id` is accepted for interface consistency with other
    providers but ignored - Babelio has no concept of arbitrary shareable
    list URLs, only the logged-in account's own bookshelf.

    Args:
        list_id (str): Ignored; kept for provider interface consistency

    Returns:
        List[Dict[str, Any]]: Book items (title, author, year, media_type="book",
            babelio_id, babelio_url, editor)
    """
    email, password = get_babelio_credentials()
    if not (email and password):
        raise ValueError("BABELIO_EMAIL and BABELIO_PASSWORD must be set to sync a Babelio list")

    logging.info("Fetching Babelio library (mabibliotheque.php)")
    session = _get_authenticated_session(email, password)

    books: List[Dict[str, Any]] = []
    seen_ids = set()
    page = 1
    max_pages = 200  # safety cap against an unexpected pagination loop

    try:
        while page <= max_pages:
            check_and_raise_if_cancelled()

            url = f"{BASE_URL}/mabibliotheque.php?pageN={page}"
            logging.info(f"Fetching Babelio library page {page}: {url}")
            try:
                resp = session.get(url, timeout=20)
                resp.encoding = "iso-8859-1"
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                logging.error(f"Babelio: failed to fetch page {page}: {exc}")
                break

            if not _is_authenticated_page(resp.text):
                logging.warning("Babelio: session appears to have expired mid-sync, stopping pagination")
                break

            page_books = _parse_book_rows(resp.text)
            if not page_books:
                logging.info(f"Babelio: no items on page {page}, stopping pagination")
                break

            new_count = 0
            for book in page_books:
                key = book.get("babelio_id") or book["title"]
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                books.append(book)
                new_count += 1

            logging.info(f"Babelio: page {page} -> {len(page_books)} items ({new_count} new)")

            if new_count == 0:
                # Pagination looped back to already-seen items - stop.
                break

            page += 1

        logging.info(f"Babelio library fetched successfully. Found {len(books)} books across {page - 1} pages.")
        return books

    except SyncCancelledException:
        logging.warning(f"⚠️ Babelio list fetch cancelled by user - returning {len(books)} items fetched so far")
        raise

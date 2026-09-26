# Gloweet fork of list-sync

Fork of [Woahai321/list-sync](https://github.com/Woahai321/list-sync) for the
`Gloweet/gitops-demo` cluster (`apps/base/arr/list-sync/`).

## Why this fork exists

Upstream resolves every list item to a TMDB id **through the Trakt API**
(`IMDb id / title -> Trakt -> TMDB id`). On **2026-07-30 Trakt made API app
creation VIP-only** and deleted existing free-tier apps, so a `TRAKT_CLIENT_ID`
can no longer be obtained without a paid Trakt VIP subscription. Without it,
upstream's AniList sync is effectively broken (its provider *only* resolves via
Trakt) and Letterboxd sync degrades to Overseerr fuzzy text search.

## What changed

Trakt-free ID resolution, inspired by [AniPlanrr](https://github.com/noggl/AniPlanrr):

| Area | Change |
| --- | --- |
| `list_sync/providers/anime_ids.py` (new) | Loads the [Kometa Anime-IDs](https://github.com/Kometa-Team/Anime-IDs) map (`anime_ids.json`, 24h disk cache). Resolves AniList/MAL id -> TVDB/IMDb id. |
| `list_sync/providers/id_resolver.py` (new) | Resolves TVDB/IMDb id -> TMDB id via the TMDB `/find` endpoint, plus a `/search` title fallback. Needs `TMDB_KEY` (free from themoviedb.org); returns `None` and defers to Overseerr search when unset. |
| `list_sync/providers/anilist.py` | Resolution chain is now: Anime-IDs map -> TMDB `/find` (tvdb) -> TMDB `/find` (imdb) -> TMDB title search -> (optional) Trakt if `TRAKT_CLIENT_ID` is set. Also detects AniList `MOVIE` format instead of forcing `tv`. |
| `list_sync/providers/letterboxd.py` | After scraping a list, fetches each film page and reads the `data-tmdb-id` / `data-tmdb-type` / IMDb link embedded in the HTML (8-way threaded). No API key needed. |
| `list_sync/main.py` | New **Method 1.5**: IMDb id / title -> TMDB id via `id_resolver` (no Trakt). The Trakt methods (2 & 3) now only run when `TRAKT_CLIENT_ID` is set, avoiding retry/backoff storms. |
| `listsync-nuxt/components/setup/Step2Configuration.vue`, `api_server.py` | Setup wizard step 2 no longer requires a Trakt Client ID. The field is optional and only format/API-validated when a value is entered. |
| `.github/workflows/docker-build.yml` | Trimmed to a single `linux/amd64` push to `ghcr.io/<owner>/list-sync` (cluster is amd64). No attestation/SBOM/provenance, no external CI infra. |
| `list_sync/providers/babelio.py` (new) | Scrapes the authenticated account's own library from babelio.com (`mabibliotheque.php`, paginated). babelio.com gates every page behind a WAF, so this logs in via SeleniumBase (undetected-chromedriver): opens `/connection.php`, dismisses the appconsent cookie banner, fills the `Login`/`Password` form, and drag-solves the slider captcha if it appears (flaky, so login retries up to 3x with page-state diagnostics). The resulting session cookies are cached to `data/babelio_session.json` so ongoing scraping uses plain `requests` without re-solving the captcha. |
| `list_sync/api/shelfmark.py` (new) | Client for [Shelfmark](https://github.com/calibrain/shelfmark) (self-hosted book search & request tool) - the book equivalent of Overseerr. Search metadata providers, list releases, queue a download. Tolerates a bare host (defaults to `http://host:8084`) and distinguishes "no releases" (empty list) from "release search failed" (Shelfmark answers 503 with a cause - e.g. the download source is behind an unsolvable protection challenge). Release searches can take up to Shelfmark's 300s budget (`SHELFMARK_RELEASE_TIMEOUT`, default 310s). |
| `list_sync/main.py` | New `media_type == "book"` path: `sync_media_to_overseerr` now splits book items out and routes them through `sync_books_to_shelfmark`/`process_book_item` instead (Shelfmark has no library-status concept, so books are just matched + queued, no available/requested check). |
| `list_sync/database.py` | `synced_items` gained `author`/`external_id` columns and a `should_sync_book()` skip-window check, since books have no TMDB/IMDb/Overseerr id to key on. |

## Env

`BABELIO_EMAIL` / `BABELIO_PASSWORD` / `BABELIO_LISTS=mabibliotheque` and
`SHELFMARK_URL` / `SHELFMARK_API_KEY` — optional, enable syncing your Babelio
library to Shelfmark. See `.env.example`. The Babelio login flow is more
fragile than the other providers (it depends on solving a custom slider
captcha) - check `data/list_sync.log` if it stops working.

Two cluster-side requirements discovered while testing (both committed to
`Gloweet/gitops-demo`, `apps/base/arr/shelfmark/`):
- Shelfmark must run **≥ v1.4.0** — older releases (e.g. v1.3.15) reject
  `SHELFMARK_API_KEY` with 401 (the feature landed in v1.4.0).
- Shelfmark's pod must stay off `talos-worker-3`, whose node-local
  csi-driver-nfs crash-loops (70+ restarts) and wedges the pod in Init:0/2
  on a failed NFS mount. A `nodeAffinity` keeps it on healthy nodes.

End-to-end note: matching books works (Shelfmark → Open Library), but
actually queueing a download depends on Shelfmark's configured download
sources. In this cluster, Anna's Archive sits behind a protection challenge
that the external bypasser (byparr) currently can't solve, so release search
spends its 300s budget and returns 503 ("no releases"). list-sync reports
that as `error` with the Shelfmark-provided cause, not a false `not_found`.

Full diagnosis + every fix (code, gitops, Shelfmark runtime config) is
documented in [docs/babelio-shelfmark.md](docs/babelio-shelfmark.md).

`TMDB_KEY` — **recommended** (free v3 API key from
<https://www.themoviedb.org/settings/api>). Without it, AniList/Letterboxd items
that don't already carry a TMDB id fall back to Overseerr's fuzzy search.

`TRAKT_CLIENT_ID` — optional now; only used as a last-resort resolver if present.

## Keeping up with upstream

```bash
git remote add upstream https://github.com/Woahai321/list-sync.git   # once
git fetch upstream && git merge upstream/main
```

## Guardrails

This fork must never push to / open PRs against the original repo
(`Woahai321/list-sync`). Protection is layered:

- **`githooks/pre-push`** (committed) refuses any `git push` whose remote
  name is `upstream` or whose URL points at `Woahai321`. Enable it on any
  clone with `git config core.hooksPath githooks`.
- **`gh` defaults** to `Gloweet/list-sync` (`gh repo set-default`).
- The fork's `main` is branch-protected (PR review required) on GitHub.

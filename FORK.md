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

## Env

`TMDB_KEY` — **recommended** (free v3 API key from
<https://www.themoviedb.org/settings/api>). Without it, AniList/Letterboxd items
that don't already carry a TMDB id fall back to Overseerr's fuzzy search.

`TRAKT_CLIENT_ID` — optional now; only used as a last-resort resolver if present.

## Keeping up with upstream

```bash
git remote add upstream https://github.com/Woahai321/list-sync.git   # once
git fetch upstream && git merge upstream/main
```

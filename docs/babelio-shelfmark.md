# Babelio → Shelfmark integration (fork)

ListSync syncing a **Babelio** bookshelf to **Shelfmark**
(https://github.com/calibrain/shelfmark), the self-hosted book search &
request tool that plays Overseerr's role for books.

This page documents the integration end-to-end and, more importantly, every
fix that was needed to make it actually work — code, cluster (gitops) and
Shelfmark runtime config. If the book sync stops working, read the
[Diagnosis & fix log](#-diagnosis--fix-log) section first.

---

## 📚 Architecture

```
babelio.com/mabibliotheque.php (35 books, 3 pages)
        │  selenium login (captcha + cookies cache in data/babelio_session.json)
        ▼
list_sync/providers/babelio.py        → items: title, author, year, media_type="book"
        ▼
list_sync/api/shelfmark.py            → Shelfmark JSON API (admin API key)
        │  /api/metadata/search  (Open Library)
        │  /api/releases         (libgen / prowlarr sources)
        │  /api/releases/download (queue to qBittorrent via Prowlarr)
        ▼
list_sync/main.py  sync_books_to_shelfmark() / process_book_item()
        ▼
synced_items DB (author, external_id columns) + summary/display/Discord
```

New media type `book` is split out of the Overseerr pipeline in
`sync_media_to_overseerr()` and routed to Shelfmark instead — the two targets
never mix.

---

## 🔌 Environment

All in `.env` (see `.env.example`):

| Variable | Purpose |
| --- | --- |
| `BABELIO_LISTS=mabibliotheque` | Enables the provider (only the authenticated account's own library is supported). |
| `BABELIO_EMAIL` / `BABELIO_PASSWORD` | Babelio login (used by the Selenium fallback). |
| `SHELFMARK_URL` | Bare host works: `192.168.1.85` → `http://192.168.1.85:8084` (Shelfmark's default port auto-applied when none given). |
| `SHELFMARK_API_KEY` | Must equal the `SHELFMARK_API_KEY` set on the Shelfmark side (≥ v1.4.0). |
| `SHELFMARK_SEARCH_TIMEOUT` | Metadata search timeout, default `30`. |
| `SHELFMARK_RELEASE_TIMEOUT` | Release-search timeout, default `310` (Shelfmark's own search budget is 300s). |

`data/babelio_session.json` caches the authenticated Babelio session so
ongoing scrapes use plain `requests` without re-solving the captcha. It is
gitignored (it contains live cookies).

---

## 🔧 Diagnosis & fix log

Chronology of everything that blocked the feature and how each was fixed.

### 1. Babelio is behind a WAF "drag to verify" slider captcha

**Symptom:** plain `curl` gets a 403 "Vérification de sécurité" on every page,
even the homepage. The challenge is a server-side gate, not a cookie check.

**Fix (`list_sync/providers/babelio.py`):**
- Login via SeleniumBase in undetected-chromedriver mode (`SB(uc=True)`).
- The login page is **`/connection.php`**, not `/connexion.php` (the latter is
  a 404 "Page blanche").
- The login form fields are `name="Login"`, `name="Password"`. The submit
  control is a **`<button name="sub_btn">Se connecter</button>`**, not an
  `<input name="sub_btn">` — see fix #6.
- A full-screen **appconsent cookie banner** (iframe) blocks the submit
  button → dismissed by clicking *"Continue without accepting"* first, then
  waiting for the iframe to disappear.
- The slider captcha is flaky → `ActionChains` humanised drag, retried up to
  3× with page-state diagnostics logged (`_log_page_state`). The drag target
  is now the track's right edge (not just `data-drag-distance`, which sizes
  the fill bar and can leave the thumb a few pixels short).
- Successful session cookies are cached to `data/babelio_session.json`;
  subsequent runs reuse them (verified: second run does a plain `requests`
  fetch, no Selenium).

**Dead ends checked on 2026-10-30 (do not re-litigate):**
- **byparr** (FlareSolverr-compatible, used by Shelfmark) answers the Babelio
  WAF with a 200 + cookies, but the body is still the custom
  `/js/rcaptcha.js` drag challenge. It only knows Cloudflare-style
  challenges, so it cannot solve Babelio's slider.
- **Tor** (`arr/tor`, SOCKS 9050 / HTTP 8118) also gets the same 403
  challenge, so routing Selenium through it buys nothing (and it is slower).
- The WAF re-challenges aggressively after repeated logins. Hammering it
  from one egress IP temporarily turns 403 into connection timeouts; back off
  instead of adding retries in a tight loop.

### 2. Shelfmark API key rejected with 401

**Symptom:** `/api/status` returns 401 even with the correct key.

**Root cause:** the feature is a **static API key backed by
`SHELFMARK_API_KEY`** (`shelfmark/core/api_key.py`), which does **not exist
in Shelfmark ≤ v1.3.15** — it only landed in v1.4.0. The cluster was pinned
to `v1.3.15`.

**Fix (gitops-demo, commit `d22568b1`):**
`apps/base/arr/shelfmark/deployment.yaml`: image bumped to
`ghcr.io/calibrain/shelfmark:v1.4.0` (app container + iptables init
container). Then:

```
curl -H "Authorization: Bearer $SHELFMARK_API_KEY" .../api/status   → 200
curl -H "X-Api-Key: $SHELFMARK_API_KEY"        .../api/status       → 200
curl                                           .../api/status       → 401 (expected)
```

### 3. Shelfmark pod stuck in Init:0/2 (NFS mount timeout)

**Symptom:** the Shelfmark pod never starts; `MountVolume.SetUp failed …
mount volume …/shelfmark-config … timeout after 110s`.

**Root cause:** the pod kept scheduling on **`talos-worker-3`**, whose
node-local `csi-driver-nfs` daemon crash-loops (70+ restarts). The NFS share
itself is fine on the other nodes.

**Fix (gitops-demo, commit `4e08b274`):**
`nodeAffinity` on the shelfmark deployment keeping it off `talos-worker-3`:

```yaml
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: NotIn
              values: [talos-worker-3]
```

The pod now runs on `talos-worker-1`. (The same broken node can wedge any
NFS-mounted workload — worth a cluster-level fix later.)

### 4. Shelfmark egress dead (WireGuard tunnel carrying no traffic)

**Symptom:** everything external unreachable from the shelfmark pod
(openlibrary.org, libgen.gl, api.ipify.org all fail). Open Library metadata
searches would randomly timeout/reset.

**Root cause:** the ProtonVPN WireGuard tunnel on the pod showed a recent
handshake but ~0 data (≈11 KiB after an hour). The kill-switch iptables
policy is fail-closed (`OUTPUT policy DROP`, only tunnel endpoint + LAN
allowed), so any egress that isn't actually transiting the tunnel is killed.
This is a recurring theme — gitops history shows several ProtonVPN key
rotations (FR#180, FR#684, …).

**Fix:** rotate/restore the tunnel (egress confirmed back:
`curl openlibrary.org → 200`, tunnel transfer jumped to ~100+ KiB). Also the
Shelfmark **DNS was switched to Google DoH** in the Shelfmark web settings
(this is the change that restored name resolution for the download path).

### 5. Release search always 503: Anna's Archive dead-end + broken bypasser

**Symptom:** metadata search works, but `/api/releases` returns 503 after
spending the full 300s budget. Shelfmark log:

```
Release search failed for source direct_download: Unable to reach download
source. The release search ran out of time (300s). Anna's Archive is behind
a protection challenge the bypasser could not solve in that window.
```

**Root causes (three stacked):**
- Shelfmark's `direct_download` source is **Anna's Archive only** in v1.4.0
  (`PROVIDER_TYPES = (AnnasArchiveProvider,)` — libgen is a *separate*
  source, see below). Anna's Archive is behind a Cloudflare protection
  challenge.
- The bypasser **byparr** couldn't solve it: its Xvfb fails to start
  (`Xvfb failed to start after 10 attempts`) so it has no browser, and its
  own egress was failing too.
- Even a correctly-solved search then never reached **Prowlarr** — the
  budget was spent by Anna's Archive first, and Prowlarr itself was crash-
  looping (`Non-recoverable failure` during DryIoc startup).

**Fix (Shelfmark runtime config, on the pod `/config/plugins/`):**

`download_sources.json`:
```json
{ "DIRECT_DOWNLOAD_ENABLED": false }
```
This disables the Anna's Archive source entirely (disabling just the
`aa-fast` / `aa-slow-*` entries in `FAST_SOURCES_DISPLAY` / `SOURCE_PRIORITY`
does **not** stop the search — those flags only govern download mirrors).

`libgen_config.json`:
```json
{ "LIBGEN_SEARCH_ENABLED": true }
```
Libgen is a separate release source that works without any bypasser.
`mirrors.json` already listed `https://libgen.gl`, which responds 200 once
egress is up.

Restart the pod afterwards so Shelfmark reloads the plugin config (it
re-reads these files at startup). Backup is kept in
`/config/backup-20260926/` on the pod.

**Result:** `/api/releases` answers **200 in ~19s with 93 releases** (first
hit = the exact book, `La.petite.bonne.Berenice.Pichat.2024.FR.[EPUB`), from
Prowlarr indexers (which recovered) + libgen. `process_book_item` reports
`status=requested` and the POST `/api/releases/download` succeeds, handing
the torrent to qBittorrent (`book` category).

### 6. Babelio login never submitted (picked the header search button)

**Symptom:** after 2026-10-28 the slider was solved (the login form appeared),
but every attempt ended with `no id_user cookie` and the login page still
displayed. The provider retried 3× and recorded 0 books.

**Root cause:** the submit control changed to
`<button type="submit" name="sub_btn">Se connecter</button>`, but the code
looked for `input[name='sub_btn']` first. It then fell back to a **global**
`button[type='submit']` lookup, which matches the header's search button
*before* the login button. So it clicked the search icon (or the click was
intercepted by the consent iframe) and never POSTed the credentials.

**Fix:** scope every lookup to `form[action*='connection.php']`, and submit
with `form.submit()` (proven to work end-to-end: id_user cookie set, then
`mabibliotheque.php` returns 200 with the library). The button click path
sometimes navigates through `/captcha.php` and blocks ChromeDriver for its
full 120s read timeout. Also wait for the login form to appear and for
`id_user` up to 10s instead of using fixed sleeps.

### 7. list-sync fell into the interactive prompt on boot

**Symptom:** pod "healthy" (health endpoint on the API process still answers)
but no sync ever ran; `list_sync.log` ended at
`🔑 No saved credentials found. Let's set up your Overseerr connection.`

**Root cause:** `load_env_config()` tests Overseerr's `/api/v1/status` and
returns `None` when it fails. On 2026-10-30 Seerr was still starting
(`ECONNREFUSED`), so the config looked missing and the process dropped into
`get_credentials()` — an interactive prompt with no TTY, where it idled.

**Fix:**
- Code: startup reads config with `load_env_config(test_connection=False)`;
  in automated mode it retries Overseerr with exponential backoff (5s → 60s)
  and never falls through to the interactive menu. A sync failure exits for
  supervisor to restart instead of blocking on `input()`.
- Cluster: `apps/base/arr/list-sync/deployment.yaml` gained an init container
  `wait-for-seerr` that polls `http://seerr.seerr.svc.cluster.local/api/v1/status`
  before the app starts.

---

## ✅ Verification checklist

1. Babelio: `python -c "from list_sync.providers import babelio;
   print(len(babelio.fetch_babelio_list('mabibliotheque')))"` → 35 books.
   Second run should be fast (cached session).
2. Shelfmark API key: `/api/status` with `Authorization: Bearer $KEY` → 200.
3. Metadata: `/api/metadata/search?query=<title>` returns the right
   provider_id (Open Library).
4. Releases: `/api/releases?provider=openlibrary&book_id=<id>` → 200 with
   releases (not 503).
5. Full sync: run the app with `BABELIO_LISTS=mabibliotheque` set;
   `synced_items` rows carry `media_type='book'`, `author`,
   `external_id='openlibrary:...'`.

## ⚠️ Operational notes

- **Release-search latency is the budget, not a bug.** Shelfmark answers
  `/api/releases` only after its own 300s budget or when sources respond.
  A client timeout shorter than that budget manufactures a false
  "no releases". Keep `SHELFMARK_RELEASE_TIMEOUT` ≥ Shelfmark's
  `RELEASE_SEARCH_TIMEOUT`.
- **503 ≠ not_found.** Shelfmark returns 503 with `{"error": <cause>}` when
  no source could be reached. list-sync maps that to status `error` with the
  cause logged; a clean empty list is `not_found`. Don't "fix" it into
  `not_found`.
- **Metadata matching is title-first.** Open Library returns 0 for the
  concatenated `title author` query but good results for the bare title.
  `process_book_item` searches title-only, retries `title author` as a
  fallback, and `_pick_best_book()` prefers a result whose author list
  fuzzy-matches the Babelio author.
- **`talos-worker-3` is unhealthy for NFS.** Any workload with a
  `zfs-nvme1-nfs` PVC can wedge there (see fix #3).
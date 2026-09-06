# Nuvio recap catalog

Public editorial metadata only: YouTube IDs, approved channels, English language, exact series IDs and season coverage. No video files, account data, API credentials, or app source are published here.

## Live status

The catalog is live at https://nuvio-recaps.marvins-dashboard.workers.dev/v1/catalog.json (revision 3). GitHub validation and the daily review workflow are enabled and have passed. The tvOS bootstrap now contains this endpoint; its live update/cache/304 test passed.

Cloudflare Git builds are connected to `nobnobz/nuvio-recap-catalog`, branch `main`. Commit c2d7d2a automatically published revision 3; Cloudflare build e1e0810b-16d0-4087-8b30-cd68c73122d8 and GitHub validation both succeeded. The Workers Free account plan was confirmed in the dashboard. No Cloudflare deployment secret is stored in GitHub.

## Publishing

`catalog.json` is the editorial source. Change approved entries and increase `revision` for every change, including withdrawals. `enabled: false` removes a video from the app. Correct mistakes with a higher revision, not by restoring an older release number.

Cloudflare Workers Static Assets serves only `/v1/catalog.json`. There is no application script, KV, R2, database, or paid binding. Keep the account on Workers Free. Static asset requests are currently free and unlimited; GitHub standard hosted runners are free for public repositories. A paid domain is unnecessary.

Build configuration: production branch `main`, build command `npm test && npm run build`, deploy command `npm run deploy`. Build variable `CATALOG_URL` points to the live endpoint above. Include watch paths are `catalog.json`, `scripts/**`, `tests/**`, `package.json`, `package-lock.json`, and `wrangler.jsonc`. Daily `review/*` reports and documentation changes do not trigger builds. Preview branch builds are disabled. GitHub validates changes; Cloudflare alone deploys them. The scoped deployment token is managed inside Cloudflare Builds.

The first automatic deployment completed in about 20 seconds. Live HTTP 200 returned revision 3 and matching If-None-Match returned 304. The bundled app fallback deliberately remains revision 2, so subsequent published revisions arrive through the updater without rebuilding the app.

Manual fallback:

```sh
npm ci
npm test
CATALOG_URL=https://nuvio-recaps.marvins-dashboard.workers.dev/v1/catalog.json npm run build
CLOUDFLARE_ACCOUNT_ID=ddf3f2e63080257eff83d13a7a9f7666 npm run deploy
```

Wrangler authentication is stored in the macOS keychain with account/user read and Workers deployment scopes. Never place OAuth credentials or API tokens in commits, catalog data or the app.

One initial app build/install with the endpoint is necessary. Future catalog changes require no app build; schema/code changes can still need an app update. The bundled JSON is the bootstrap/fallback, not the ongoing editorial source.

## Automatic discovery and health checks

`Refresh review inbox` runs daily at 05:17 UTC, fetches the approved channels' recent RSS uploads, and checks enabled videos' watch-page metadata. It commits only `review/inbox.json`. It never modifies or deploys `catalog.json`, and the inbox is not included in the public Cloudflare assets.

Review candidates' spoken language, show identity, full season coverage and later-season spoilers before adding them to the catalog. Feed titles cannot prove these facts. RSS returns a limited recent window; it is incremental discovery, not a complete historical YouTube search. Temporary YouTube blocking is recorded as unknown availability and never automatically withdraws the catalog. The first GitHub runner found all 27 feed candidates, but YouTube blocked detailed watch-page checks there; actual native playback and local metadata checks are separate evidence. Candidates remain in the inbox until catalogued, removed, or the 500-entry bound is reached.

The daily stored health report records actual review state and maintains repository activity. GitHub may delay scheduled workflows or disable them after 60 days without repository activity; monitor Actions failures. These limits do not interrupt the already published static catalog or automatic app downloads.

## App behavior

The native app displays the cached catalog immediately, coalesces concurrent checks and uses ETags. It checks at startup, foregrounding and recap browsing when due: every 24 hours after success, one hour after failure. It persists the last good catalog and check time atomically. Downloads are capped at 1 MiB and 15 seconds; invalid schemas, conflicting IDs and revision rollbacks are rejected. Updates never change the trusted download endpoint. tvOS may evict disk caches; the bundle remains the final fallback. No promise of background execution while the app is suspended.

## Sources

- [Cloudflare static asset pricing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/)
- [Cloudflare ETag and headers](https://developers.cloudflare.com/workers/static-assets/headers/)
- [GitHub Actions billing](https://docs.github.com/en/actions/concepts/billing-and-usage)

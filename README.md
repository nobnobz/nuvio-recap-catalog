# Nuvio recap catalog

Public editorial metadata only: YouTube IDs, approved channels, English language, exact series IDs and season coverage. No video files, account data, API credentials, or app source are published here.

## Live status

The catalog is live at https://nuvio-recaps.marvins-dashboard.workers.dev/v1/catalog.json (revision 2). GitHub validation and the daily review workflow are enabled and have passed. The tvOS bootstrap now contains this endpoint; its live update/cache/304 test passed.

Cloudflare's Git integration is still waiting for the GitHub browser login. Until that connection is completed, pushes validate the catalog but do not automatically publish it to Cloudflare. Wrangler manual deployment works. No Cloudflare deployment secret is stored in GitHub.

## Publishing

`catalog.json` is the editorial source. Change approved entries and increase `revision` for every change, including withdrawals. `enabled: false` removes a video from the app. Correct mistakes with a higher revision, not by restoring an older release number.

Cloudflare Workers Static Assets serves only `/v1/catalog.json`. There is no application script, KV, R2, database, or paid binding. Keep the account on Workers Free. Static asset requests are currently free and unlimited; GitHub standard hosted runners are free for public repositories. A paid domain is unnecessary.

Complete the Git connection once in Cloudflare's `nuvio-recaps` Worker → Settings → Builds → GitHub. Choose only `nobnobz/nuvio-recap-catalog`, production branch `main`, build command `npm test && npm run build`, deploy command `npm run deploy`. Set build variable `CATALOG_URL` to the live endpoint above. Restrict build watch paths to `catalog.json`, `scripts/*`, `tests/*`, `package.json`, `package-lock.json`, and `wrangler.jsonc`; exclude the daily `review/*` report to avoid unnecessary builds. Disable preview branch builds for this catalog. The GitHub workflow validates changes; Cloudflare alone deploys them.

After connecting, publish a higher catalog revision through Git and verify Cloudflare picked it up without a manual deploy. That final Git-to-Cloudflare check is still pending.

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

Review candidates' spoken language, show identity, full season coverage and later-season spoilers before adding them to the catalog. Feed titles cannot prove these facts. RSS returns a limited recent window; it is incremental discovery, not a complete historical YouTube search. Temporary YouTube blocking is recorded as an error and never automatically withdraws the catalog. Candidates remain in the inbox until catalogued, removed, or the 500-entry bound is reached.

The daily stored health report records actual review state and maintains repository activity. GitHub may delay scheduled workflows or disable them after 60 days without repository activity; monitor Actions failures. These limits do not interrupt the already published static catalog or automatic app downloads.

## App behavior

The native app displays the cached catalog immediately, coalesces concurrent checks and uses ETags. It checks at startup, foregrounding and recap browsing when due: every 24 hours after success, one hour after failure. It persists the last good catalog and check time atomically. Downloads are capped at 1 MiB and 15 seconds; invalid schemas, conflicting IDs and revision rollbacks are rejected. Updates never change the trusted download endpoint. tvOS may evict disk caches; the bundle remains the final fallback. No promise of background execution while the app is suspended.

## Sources

- [Cloudflare static asset pricing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/)
- [Cloudflare ETag and headers](https://developers.cloudflare.com/workers/static-assets/headers/)
- [GitHub Actions billing](https://docs.github.com/en/actions/concepts/billing-and-usage)

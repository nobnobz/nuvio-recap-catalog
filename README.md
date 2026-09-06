# Nuvio recap catalog

Public editorial metadata only: YouTube IDs, approved channels, English language, exact series IDs and season coverage. No video files, account data, API credentials, or app source are published here.

## Publishing

`catalog.json` is the source of truth after this hosting repository is deployed. Change approved entries there and increase `revision` for every change, including withdrawals. `enabled: false` removes a video from the app. Correct mistakes with a higher revision, not by restoring an older release number.

Push to `main` runs validation and publishes `/v1/catalog.json` to Cloudflare Workers Static Assets. The Wrangler configuration has no application script, KV, R2, database, or paid bindings. Keep the Cloudflare account on Workers Free. Static asset requests are currently free and unlimited; GitHub standard hosted runners are free for public repositories. Do not enable paid plans for this project.

Setup, once:

1. Authenticate Wrangler with `npx wrangler login`, then run `npm ci`, `npm test`, `npm run build`, and `npm run deploy` on the intended Cloudflare account. Use the resulting stable workers.dev address; a paid domain is unnecessary.
2. In GitHub repository secrets, set `CLOUDFLARE_ACCOUNT_ID` and a scoped `CLOUDFLARE_API_TOKEN` with Workers Scripts edit permission for this account. Use GitHub's secret UI or `gh secret set`; never put tokens into catalog files, commits, the app, or chat. Protect the `production` environment against untrusted branches.
3. Set repository variable `CATALOG_URL` to the actual HTTPS `/v1/catalog.json` endpoint. This enables pre-deployment comparison with the currently published revision. Keep it unset only for the initial deployment.
4. Add that same URL as `updateURL` in `catalog.json`, increase its revision, and publish it. Copy the resulting catalog once into the app's bundled `SeasonRecapCatalog.json`. Build/install the app once with this bootstrap endpoint. Future catalog changes need no app build. Schema/code changes may still need an app update.
5. Verify HTTP 200 + JSON + ETag, then HTTP 304 with If-None-Match. Run the GitHub publication workflow once with the configured secrets. Confirm a higher revision reaches the app and that offline launch retains the last valid copy.

```sh
npm ci
npm test
npm run build
npm run deploy
```

Cloudflare Workers Builds with Git integration can alternatively run the same build/deploy commands without GitHub deployment secrets. Use one deployment pipeline, not both.

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

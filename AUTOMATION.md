# Automatic recap catalog

The daily `Update recap catalog` workflow uses YouTube Data API v3. It requires the repository Actions secret `YOUTUBE_API_KEY`, restricted to that API. No AI provider, paid Worker, video hosting, or user viewing-history upload is involved. Missing credentials or failed API requests fail the job visibly and preserve the published catalog.

## Admission

Only channels in `catalog.json` can publish automatically. The initial exact identity registry in `series.json` covers 16 series, including extra context for ambiguous remakes. Unknown titles are resolved through the same Cinemeta service used by the app: only one exact series-title match, confirmed by detail metadata with IMDb and TMDB IDs, is admitted. Multiple matches, changed IDs or missing IDs fail closed. Accepted identities persist in automation state; up to eight new title resolutions run daily, deferred/network-failed identities queue up to 500 videos. Negative resolutions are cached for 30 days. This is automatic growth from approved channels, not unrestricted YouTube search. New channels remain configuration changes; individual matching uploads and uniquely identified new series need no editorial approval.

Titles must explicitly name a single season or a cumulative range starting at season 1. `Before season 4` alone is ambiguous and rejected. `1 & 3` does not mean `1–3`. Episode recaps, trailers, theories, mixed shows, unsupported suffixes, contrary coverage, and unresolved titles stay out. Metadata must confirm English audio, public processed video, embeddability, valid duration, and no region/age restriction. Missing spoken-language metadata is not inferred from an English title. Metadata is evidence of the uploader's declarations, not a guarantee about every frame or spoiler-free content.

## Discovery and recovery

Each run checks the newest 50 uploads of every approved channel and advances up to four historical pages per channel (reduced for larger channel lists to stay within budget). Checkpoints persist in `review/automation-state.json`; every 30 days a completed archive is scanned again to reconsider changed metadata. Up to 180 YouTube API requests and 16 Cinemeta requests per run, 15–20-second request timeouts, bounded responses and a ten-minute workflow timeout bound the work. No global `search.list` requests are needed. The full archive fills over several runs, not necessarily the first day.

Catalog IDs, disabled editorial entries and `review/decisions.json` exclusions cannot be re-added by discovery. Automatic additions include policy-version evidence in the state file. Unknown candidates are skipped without a manual review queue.

Health checks rotate through at most 60 entries per run. Three successful API checks on distinct days that report a video missing/private/unlisted/deleted are required before withdrawal. API/network failures never count as evidence of removal. Auto-withdrawn videos can recover only with unchanged identity, title and duration and confirmed public, processed, embeddable status; editorially disabled videos stay disabled. Regional playback failures and native stream resolution are not proven by API status.

The entire result is validated before files are replaced. A content change increments the revision once. Idempotent runs do not increment it. The workflow validates against the live revision before a selective commit to main; a competing commit rejects the push instead of merging potentially conflicting revision state. Cloudflare Git integration deploys catalog changes. State and reports are excluded from public assets and do not independently trigger a deployment. GitHub's default workflow-failure notifications surface required account/quota intervention; no routine human approval is needed.

## App behavior

The standalone Recaps tab uses all matching catalog coverage, newest ending season first, dedicated season before cumulative coverage at the same ending season. Labels always show the actual range. Up to three distinct creators per range remain available. Episode selection does not hide catalog entries; the old `recaps(beforeSeason:)` API retains its previous-season semantics for contextual use. The app keeps its local-first cache and daily update interval.

## Validation

Run `python3 -m unittest discover -s tests`, then `YOUTUBE_API_KEY=... python3 scripts/automate.py` with the key injected through the environment (never commit it). `python3 scripts/build.py` publishes only schema-compatible catalog data. In CI, the secret is scoped to the discovery step. A successful API run and a separately verified live catalog revision are required before claiming end-to-end activation; app/device playback is separate evidence.

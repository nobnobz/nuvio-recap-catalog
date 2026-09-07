# Automatic recap catalog

The daily `Update recap catalog` workflow uses YouTube Data API v3. It requires the repository Actions secret `YOUTUBE_API_KEY`, restricted to that API. No AI provider, paid Worker, video hosting, or user viewing-history upload is involved. Missing credentials or failed API requests fail the job visibly and preserve the published catalog.

## Admission

Only channels in `catalog.json` can publish automatically. The initial exact identity registry in `series.json` covers 16 series, including extra context for ambiguous remakes. Unknown titles are resolved through the same Cinemeta service used by the app: only one exact series-title match, confirmed by detail metadata with IMDb and TMDB IDs, is admitted. Multiple matches, changed IDs or missing IDs fail closed. Accepted identities persist in automation state; up to eight new title resolutions run daily, deferred/network-failed identities queue up to 500 videos. Negative resolutions are cached for 30 days. This is automatic growth from approved channels, not unrestricted YouTube search. New channels remain configuration changes; individual matching uploads and uniquely identified new series need no editorial approval.

Titles must explicitly name a single season or a cumulative range starting at season 1. Recognized publisher suffixes are parsed independently, including repeated exact show names and correctly numbered “Before Season N” hints. Full-series titles require an explicit numeric range plus confirmation that every claimed season exists in Cinemeta episode metadata; film/part/volume mixtures and contradictory ranges remain excluded. Clearly delimited publisher service footers and standalone links to other recaps are excluded from content-description checks. `Before season 4` alone is ambiguous and rejected. `1 & 3` does not mean `1–3`. Episode recaps, trailers, theories, mixed shows, unsupported suffixes, contrary coverage, and unresolved titles stay out. Metadata must confirm English audio, public processed video, embeddability, valid duration, and no region/age restriction. Missing spoken-language metadata is not inferred from an English title. Metadata is evidence of the uploader's declarations, not a guarantee about every frame or spoiler-free content.

## Discovery and recovery

Each run checks the newest 50 uploads of every approved channel and advances up to four historical pages per channel (reduced for larger channel lists to stay within budget). Checkpoints persist in `review/automation-state.json`; a completed archive restarts after six calendar months (September 7, 2026 → March 7, 2027). Rejected candidate IDs and reason codes persist separately. Every 30 days, their current metadata is fetched in batches; unchanged metadata skips classification, while unresolved series identities can retry their external metadata lookup. Changed admission rules or the configured identity/channel registry trigger an earlier targeted retry. This avoids rescanning all uploads just to reconsider rejected candidates. Up to 180 YouTube API requests and 16 Cinemeta requests per run, 15–20-second request timeouts, bounded responses and a ten-minute workflow timeout bound the work. No global `search.list` requests are needed. The full archive fills over several runs, not necessarily the first day.

Catalog IDs, disabled editorial entries and `review/decisions.json` exclusions cannot be re-added by discovery. Automatic additions include policy-version evidence in the state file. Unknown candidates are skipped without a manual review queue.

Health checks rotate through at most 60 entries per run. Three successful API checks on distinct days that report a video missing/private/unlisted/deleted are required before withdrawal. API/network failures never count as evidence of removal. Auto-withdrawn videos can recover only with unchanged identity, title and duration and confirmed public, processed, embeddable status; editorially disabled videos stay disabled. Regional playback failures and native stream resolution are not proven by API status.

The entire result is validated before files are replaced. A content change increments the revision once. Idempotent runs do not increment it. The workflow validates against the live revision before a selective commit to main; a competing commit rejects the push instead of merging potentially conflicting revision state. Cloudflare Git integration deploys catalog changes. State and reports are excluded from public assets and do not independently trigger a deployment. GitHub's default workflow-failure notifications surface required account/quota intervention; no routine human approval is needed.

## App behavior

The standalone Recaps tab uses all matching catalog coverage, newest ending season first, dedicated season before cumulative coverage at the same ending season. Labels always show the actual range. Up to three distinct creators per range remain available. Episode selection does not hide catalog entries; the old `recaps(beforeSeason:)` API retains its previous-season semantics for contextual use. The app keeps its local-first cache and daily update interval.

## Validation

Run `python3 -m unittest discover -s tests`, then `YOUTUBE_API_KEY=... python3 scripts/automate.py` with the key injected through the environment (never commit it). `python3 scripts/build.py` publishes only schema-compatible catalog data. In CI, the secret is scoped to the discovery step. A successful API run and a separately verified live catalog revision are required before claiming end-to-end activation; app/device playback is separate evidence.

## Activation evidence — September 7

Google Cloud project `Nuvio Recap Catalog` (`crack-decorator-507908-u4`) has YouTube Data API v3 enabled. The dedicated key is restricted to this API and stored only as repository Actions secret `YOUTUBE_API_KEY`; no billing/trial subscription was activated during setup.

Run [34099342080](https://github.com/nobnobz/nuvio-recap-catalog/actions/runs/34099342080) checked 433 candidates, admitted 33 videos and verified public r5 (62 videos / 24 series). Follow-up [34099531302](https://github.com/nobnobz/nuvio-recap-catalog/actions/runs/34099531302) resumed stored state, checked 280 candidates, admitted another 29 and verified public r6 (91 videos / 31 series). Both completed all steps, including Cloudflare publication verification; neither withdrew any videos. The runs used 34 and 26 YouTube API calls respectively.

Archive scanning and deferred identity resolution continue daily at 05:17 UTC. The initial incremental archive was subsequently completed; see the completion evidence below. The app downloads catalog changes on its existing update interval (up to 24 hours); no further app build is needed for catalog-only updates. API availability checks do not prove playback on every device.

Manual initial backfill can select `archive_pages: 20` in workflow dispatch. The daily default remains four pages. Manual `identity_checks: 32` accelerates deferred identity/episode-numbering checks (up to 64 Cinemeta requests); the daily default stays eight checks and 16 requests. The same 180-call ceiling and per-channel budget calculation apply; saved cursors continue across runs.

## Initial archive completed — September 7

Final run [34101510865](https://github.com/nobnobz/nuvio-recap-catalog/actions/runs/34101510865) completed with all four channel archive cursors exhausted, `archivesComplete: true`, zero pending identities and zero identity errors. Live revision 20 was independently verified: 172 videos across 85 series. This covers the public uploads returned by the four approved channels, with metadata-based admission; it does not mean every video was watched or every offered recap was accepted. Daily head checks continue, and completed archives restart after six calendar months.


## Six-month archive and rejection recovery — September 7

Policy 5 is deployed. The initial 435 non-admitted candidates were revisited through YouTube metadata, with explicit reason codes and fingerprints now retained. Safe publisher suffixes and numeric full-series titles recovered 65 additional videos: public **r26 contains 237 videos across 115 series** (previously r20: 172 / 85). No video was withdrawn. Final recovery run [34103924437](https://github.com/nobnobz/nuvio-recap-catalog/actions/runs/34103924437) completed successfully, including Cloudflare publication verification, with zero pending identities and zero identity errors.

The 370 remaining candidates have these primary reasons (one reason per video, not necessarily the only issue): 252 unsupported/non-explicit title formats, 49 unresolved exact series identities, 19 suffix/context conflicts, 17 mixed-format/description flags, 12 live/region/age restrictions, 10 missing audio-language declarations, 10 duration-limit exclusions, and one season-numbering mismatch. Unsupported titles include movie recaps, episode recaps, partial seasons, and “full series” without numeric coverage; this category is not a count of valid recaps accidentally lost. Candidates remain eligible for targeted automatic reconsideration.

All 37 hosting tests passed. They cover six-calendar-month boundaries, changed-rule retries, unchanged-metadata caching, monthly recovery of series identities and episode numbering, safe versus conflicting title suffixes, and complete versus mixed/ambiguous full-series coverage. The public JSON was also independently compared with the committed catalog. Metadata-based verification does not establish audiovisual accuracy or physical-device playback.

Normal-default follow-up [34104058930](https://github.com/nobnobz/nuvio-recap-catalog/actions/runs/34104058930) succeeded: zero candidates and identity lookups, no catalog changes, no pending work, and 10 YouTube requests for channel heads and rotating health checks. This verifies that completed archives and recent rejections are not redundantly reprocessed every day.

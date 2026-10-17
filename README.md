# SGP Lineup Tracker

Research same-team, different-player **1+ run + 1+ RBI** FanDuel SGP prices around
MLB starting-lineup announcements. No bets are placed.

## Cloud execution and storage

GitHub Actions runs collection independently of the user's Mac. The workflow in
`.github/workflows/collect.yml` checks the MLB schedule hourly at minute 7. When a
baseline is due within 55 minutes, it keeps one runner alive, waits for five hours
before scheduled first pitch, and collects through the pregame window. Github can
delay scheduled starts, so actual per-quote timestamps and missing-stage flags
remain authoritative. Only October 17 through November 1, 2026 is in scope.

After baseline collection begins, the runner checks MLB lineups approximately every
five seconds until both teams' orders are detected, then returns to 30-second checks
for revisions. RotoWire page checks remain cached for about one minute. It prices the same
saved run–RBI pairs immediately after each team's lineup detection, ten minutes
later, and in a final sweep starting five minutes before first pitch. There is no
projected-order filter. Both player directions remain distinct. A complete sweep
of the saved sample's 144 candidates has a theoretical 36-second minimum at four
requests/sec; actual full-batch timing has not been benchmarked.

Collected data, including `sgp_price_changes.csv`, `lineup_changes.csv`, manifests,
source pages, and quote responses, is encrypted before upload. The latest complete
cloud checkpoint is saved to the repository's **Encrypted 2026 research data**
release as `season-2026-latest.enc`, refreshed approximately every five minutes
while a game is being collected and again when the run ends. It is restored by the
next run, so the CSVs accumulate all collected games. Releases have no automatic
Actions-artifact expiry. Each run also uploads an encrypted recovery artifact,
retained for up to 90 days. A forced runner termination can lose work since the last
checkpoint. Initial October 16 quote tests are archived separately and never
included as October 17 onward research observations.

The source repository remains public; **plaintext research files are never uploaded**.
GitHub Actions secrets hold `FD_API_KEY` and `SGP_ARCHIVE_PASSWORD`. The archive uses
OpenSSL AES-256-CBC with PBKDF2 and a verified HMAC-SHA256 over the encrypted payload;
archive authentication is checked before decryption. A local recovery copy of the
archive key is ignored by Git. Keep access to the repository's secrets secure.
To obtain readable CSVs, decrypt the archive using the recovery key or run an
explicitly authorized export in a trusted environment with the repository secret.
There is no public plaintext CSV download.

A separate `cloud-probe.yml` workflow checks real FanDuel, MLB, and RotoWire access.
Its probe can inspect in-play pricing solely for connectivity validation; it is
never exported as pregame research. The first GitHub-hosted probe passed.
A full live lineup transition remains pending.

Scheduled workflows run from the default branch. Manual **Run workflow** supports
`storage-check` (authenticated archive recovery and re-upload) and `collect` (run a
due game, otherwise exit). A repository-level concurrency group prevents duplicate
cloud collectors. After November 2 the workflow disables its own schedule. GitHub
standard hosted runners are free for public repositories under GitHub's current
policy; external data access can still fail or become unavailable.

## Local development

Python 3.10+ standard library plus the `openssl` and `gh` programs for cloud storage.
The collection engine itself uses only the Python standard library.

```sh
python3 -m unittest discover -s tests -v
export FD_STATE=nj
export FD_API_KEY='<frontend key from the existing scraper>'
python3 -m src.snapshots                         # list MLB events
python3 -m src.snapshots --event-id <FD_ID> --limit 10
```

A local-only collector can be configured by copying `collector.example.json` to
`collector.local.json` and filling in the frontend key. It is a development/fallback
option; it must not run concurrently with the cloud collector.

```sh
python3 -m src.watch --config collector.local.json --once
python3 -m src.watch --config collector.local.json --ensure
```

The private local configuration, encryption recovery key, `.env` files, generated
outputs and datasets are ignored by Git. Do not commit account tokens, quote
references, raw datasets or archive passwords. Never bypass access restrictions.

## Pricing, lineups, and matching

The FanDuel adapter reuses event-page retrieval and implyBets requests from
[the original scraper](https://github.com/haroldbergner-lang/kalshi-sgp-pricer/blob/7fe41f729ec14b740c474dbe80e703a8131ae158/fd_api_pricer.py).
The website's application header and PREVIEW/SUGGESTED parameters are required.
Each quote must report SUCCESS and an `isSGM=true` DOUBLE containing exactly the
requested market/selection IDs. Singles, ordinary parlays, multiplied prices,
in-play responses, and invalid prices cannot become verified SGP quotes.
Team identity comes from FanDuel's runner team-logo metadata; unknown identities
are excluded. Names are not used to substitute market selections or thresholds.

Projected orders are frozen from RotoWire's public daily-lineups page, requiring an
explicit expected status, a matching date, and nine distinct players. Actual orders
come from MLB's pregame boxscore battingOrder with MLB player IDs. RotoWire's
confirmed status is retained as corroboration. Complete and empty future orders
were checked live, but the first live empty-to-confirmed transition is still pending.
Missing projections are never filled using actual lineups. Full-name matching
normalizes accents, punctuation and suffixes and requires a unique team match.
Unmatched players get blank derived lineup fields. Later lineup revisions are kept. Identical MLB responses share one saved source file;
failed requests never advance the last-observed-unconfirmed timestamp.

Detection time is not publication time. Scheduled first pitch may change; schedule
refreshes occur every 15 minutes. Missing baselines, already-confirmed lineups,
late quotes, and missing prices are flagged rather than silently filled.

## CSV interpretation

`sgp_price_changes.csv` contains one row per fixed candidate pair across all games:
before/after odds and implied probabilities, all intermediate quote stages,
request/response times, availability, player projected/actual batting positions,
starter flags, and both teams' full orders as JSON inside CSV cells.

Primary `odds_after` is the sweep due ten minutes after the second team's detection.
`probability_change` is measured in percentage points; probability values themselves
are fractions and include sportsbook margin. Price changes are not established EV
or proof that a lineup announcement caused the movement.

`lineup_changes.csv` includes every player in either expected or actual order,
including players without available props. FanDuel event and selection IDs are
stored separately from MLB game and player IDs. The quote reference is not a stable
cross-snapshot identity. Raw evidence remains inside the encrypted archive.

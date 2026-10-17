# SGP Lineup Tracker

Research whether MLB two-leg FanDuel SGP prices move around lineup announcements.
No betting or bet placement is implemented.

## Current status: first live batch collected

On October 16, 2026, the collector found 72 eligible selections for FanDuel event
36196493 (Rays–Guardians) and saved 10/10 verified two-leg SGP quotes to CSV.
These are actual FanDuel combined prices, not multiplied individual odds.
Comparison against prices displayed in the website remains pending.
The automated multi-snapshot workflow below is implemented and locally configured;
its first live lineup-transition capture is still pending.

The old scraper omitted the `X-Application` frontend-key header and the website's
`scope=PREVIEW&pricePolicy=SUGGESTED` parameters. Without the header the endpoint
returned only individual prices/invalid combinations. Adding the website's header
and preview parameters produced `isSGM=true`, DOUBLE quotes with exact runner IDs.
The website's public JavaScript was inspected to establish this request format.
Some responses also include generic `INVALID_COMBINATION` failures alongside a
valid SGP result; the collector validates the specific SGP combination itself.

## Run

Python 3.10+, no third-party packages. From the repository root:

```sh
export FD_STATE=nj
export FD_API_KEY='<frontend API key from your existing scraper>'
python3 -m src.snapshots                         # list MLB events
python3 -m src.snapshots --event-id <FD_ID> --limit 10
python3 -m unittest discover -s tests -v
```

The original scraper has the frontend API key in `config.py`. This project reads
it from the environment and does not commit it. Do not copy account cookies or
authentication tokens into the repository. Network access and the selected
jurisdiction must permit access; no access restrictions are bypassed.

Each run creates `output/<UTC timestamp>-<run ID>/` containing:

- `quotes.csv`: one row per attempted pair, including exact selection IDs,
  market IDs, individual prices, combined prices only when verified, status,
  failure codes, and individual request/response times.
- `selections.json`: the candidate universe saved before quoting.
- `raw/`: source markets and quote responses for inspection. These may contain
  quote references; all collected data stays ignored by Git.

The active scope uses explicit FanDuel market types for 1+ run and 1+ RBI. No projected-order filter or surname matching is used.
Pairs require one player to score 1+ run and a different teammate to record 1+ RBI; only a valid quote proves support.
Team identity is read from FanDuel’s runner team-logo metadata; missing identity is excluded, never guessed.
The default ten pairs are a diagnostic sample in stable ID order, not a representative sample.
Use `--limit` to increase the batch after pricing validation. Requests are sequential,
at least one second apart (`--delay`); a request error stops the batch without retries.
Exit code 2 means no valid quotes or a request error; inspect the saved rows.

A quote is accepted only when FanDuel reports SUCCESS and an `isSGM=true`, DOUBLE
combination contains exactly the two requested market/selection IDs, with finite,
consistent decimal and American prices. Other responses retain blank combined odds.
Probabilities are raw implied probabilities including margin, not fair probabilities.

`fd_event_id` is FanDuel's identifier. `game_id` remains blank until a verified MLB
gamePk mapping exists. Missing player IDs remain blank; selection IDs are stored
separately and are not asserted to be MLB player IDs. The `quote_id` is FanDuel's
betReference when present; it is not a stable cross-snapshot identifier.

## Source reuse

The event-page retrieval and `implyBets` request construction adapt
[fd_api_pricer.py](https://github.com/haroldbergner-lang/kalshi-sgp-pricer/blob/7fe41f729ec14b740c474dbe80e703a8131ae158/fd_api_pricer.py).
NBA/Kalshi matching, trading logic, fuzzy thresholds and the ordinary-multiple
fallback were omitted. Unit-test quote responses are synthetic fixtures, not
collected odds or evidence that live pricing works.

## Automated postseason collection

The persistent collector covers games from October 17 onward, excluding October 16.
It refreshes MLB's schedule every 15 minutes, includes ALCS and World Series games
that are actually scheduled, and skips cancelled/postponed games. Conditional games
remain candidates until MLB updates their status. Team matching requires both full
team names and a unique FanDuel event within three hours of scheduled first pitch.

Create a private local configuration by copying `collector.example.json` to
`collector.local.json`, fill in your existing scraper's frontend key, and run:

```sh
python3 -m src.watch --config collector.local.json --once   # schedule preflight
python3 -m src.watch --config collector.local.json --ensure # start only if absent
```

The running collector is protected by a process lock, resumes completed quote rows
without duplicating them, and stops after the configured collection window. The
local config and data are ignored by Git. `--ensure` starts a detached process;
it does not install a system service. A Codex chat heartbeat can supervise it and
restart it after a crash. The Mac must remain on, awake and online; scheduled Codex
supervision additionally requires the desktop app to remain running. A shutdown,
sleep, source failure or network restriction can cause missed collection windows.

### Per-game timing

- **Baseline:** starts five hours before scheduled first pitch. Save RotoWire's
  then-current *expected* orders and enumerate all same-team, different-player
  run–RBI combinations. Preserve both scorer/RBI-batter directions. Preserve the manifest unchanged afterward.
- **Immediate:** check MLB every approximately 30 seconds during the collection
  window; when a complete nine-player battingOrder first appears for either team,
  start requoting that exact manifest. Capture both teams' announcements separately.
- **Ten minutes:** another sweep becomes due ten minutes after each team's first
  detected order, regardless of whether the earlier sweep has finished.
- **Pregame:** start a final sweep shortly before scheduled first pitch. Its lead
  is the larger of five minutes or the estimated full-batch duration plus 90 seconds.
  In the saved sample, run–RBI-only filtering yields 144 candidates, taking at least 36 seconds at four starts per second. The final sweep starts five minutes before first pitch. This is
  a sweep, not one simultaneous instant. Overlapping jobs or network delays can
  extend it; no new requests start at/after scheduled first pitch.

Quote requests share a four-per-second start limit and four in-flight limit across
all batches. HTTP access/throttling/service errors back off for five minutes.
This conservative initial rate has not been load-tested across a full game universe.
Each row has its own request/response time; completed responses arriving at or
beyond scheduled start are flagged and excluded from verified pregame prices.
A weather delay may change the schedule; MLB's refreshed scheduled time is recorded,
but this collector does not infer a new actual first pitch from a delay announcement.

### Lineup sources and identity

Expected orders come from the publicly accessible RotoWire daily-lineups page, not
its paid API. The parser requires an explicit expected status, the matching date,
and nine distinct players. It was checked against tomorrow's projected orders and
today's confirmed orders. Unavailable projections remain missing, rather than being
filled with a confirmed lineup. Actual orders come from MLB's pregame boxscore
`battingOrder`, with MLB player IDs; RotoWire confirmed status is retained as
corroboration when it agrees. Today's MLB feed had nine players per side while
tomorrow's had no published orders. This behavior still needs observation during
an actual transition tomorrow. Detection times are not publication times.

Full-name matching normalizes accents, punctuation and common suffixes, never uses
surname-only or closest-threshold matching, and requires a unique team match.
Unmatched/ambiguous players have blank derived lineup fields. Known complete
lineups can establish that a matched player did not start; missing lineups cannot.
Later MLB lineup revisions are retained, with the latest order in the combined CSV.

### Final CSVs

`data/season-2026/sgp_price_changes.csv` combines all collected games, one row per
manifest pair. It includes before/after prices, each leg's projected/actual batting
position and starter status, both full projected/actual orders as JSON inside CSV
cells, source/timing fields, availability and all intermediate snapshots.

The primary `odds_after` is the sweep due ten minutes after the **second** team's
lineup detection. Separate columns preserve immediate and ten-minute prices for
each team and the final pregame sweep. `probability_change` is in percentage points;
probability columns are fractions. A missing or unsupported quote stays blank with
its status. `baseline_already_confirmed` and timing checks identify compromised
baselines; a price comparison is not proof of a lineup effect.

`lineup_changes.csv` contains one row per player appearing in either source order,
including players with no available prop. Original manifests, source pages,
boxscores, per-stage CSVs and per-quote responses remain locally available.

The separate health file records heartbeat time and errors. CSVs are rebuilt
atomically as data arrives; they are incomplete until the relevant games finish.

## Current research scope

Only 1+ run for player A plus 1+ RBI for a different player B on the same team. Both player directions remain included. All available players are eligible regardless of projected batting position; only verified FanDuel SGP quotes count as supported.

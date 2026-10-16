# SGP Lineup Tracker

Research whether MLB two-leg FanDuel SGP prices move around lineup announcements.
No betting or bet placement is implemented.

## Current status: first live batch collected

On October 16, 2026, the collector found 72 eligible selections for FanDuel event
36196493 (Rays–Guardians) and saved 10/10 verified two-leg SGP quotes to CSV.
These are actual FanDuel combined prices, not multiplied individual odds.
Comparison against prices displayed in the website remains pending.

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

All four requested prop types use explicit FanDuel market types: 1+ hit, 1+ run,
1+ RBI, and 2+ total bases. No projected-order filter or surname matching is used.
Pairs include both teams and same-player pairs; only a valid quote proves support.
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

## Incremental plan

1. Expand the successful live quote batch and compare prices to the FanDuel UI.
2. Add projected and confirmed batting orders, source IDs and retrieval times.
   RotoWire has a documented expected/confirmed MLB lineup feed; credentials,
   cost and payload status fields need verification. Evaluate MLB Stats API for
   confirmed orders. Leave unavailable publication timestamps blank.
3. Requote a saved combination manifest with exact IDs, record missing selections,
   and produce `sgp_price_changes.csv` and `lineup_changes.csv`.
4. Only then add scheduling and announcement detection. Capture team announcements
   separately; 2 PM ET is a configurable future baseline, not a guarantee that
   every game is pre-announcement.

## Source reuse

The event-page retrieval and `implyBets` request construction adapt
[fd_api_pricer.py](https://github.com/haroldbergner-lang/kalshi-sgp-pricer/blob/7fe41f729ec14b740c474dbe80e703a8131ae158/fd_api_pricer.py).
NBA/Kalshi matching, trading logic, fuzzy thresholds and the ordinary-multiple
fallback were omitted. Unit-test quote responses are synthetic fixtures, not
collected odds or evidence that live pricing works.

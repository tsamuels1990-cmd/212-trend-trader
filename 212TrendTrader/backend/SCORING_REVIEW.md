# Paper re-entry and scoring review — 2 October 2026

Observed account events show two AAPL stop-loss exits followed by automatic
AAPL purchases, including repeated score 76.51. Older event strings have no
timestamps, and do not support a reliable full performance reconstruction.

## Implemented

- A stop-loss creates a persistent symbol block. Entry requires at least two
  distinct provider daily bars after the exit date, an observed nonqualifying
  post-exit signal, then a qualifying signal on a later daily bar. This is a
  conservative reset rule, not evidence that a later entry will be profitable.
- Cooldown is configurable through stop_cooldown_sessions (1–20); omitted values
  preserve the current setting. Existing Android requests remain compatible.
- Both automated and manual paper buys enforce the block. Other symbols remain
  eligible; the account may hold cash if no eligible signal exists.
- Signals over five calendar days old, missing dates, or future dates are blocked.
  This is a conservative age bound, not a complete exchange holiday calendar.
- New buys require a live-source quote and price above the daily SMA20.
  The existing provider labels quotes up to 20 minutes old as live: this is not
  an exchange execution feed, and paper fills remain approximate.
- Paper buy/sell/check mutations are serialized to prevent duplicate actions
  while quote requests are awaiting a network response.
- New exit records include time, reason, entry/exit, quantity, return, and
  available entry score/settings. Older history is preserved.
- Existing paper positions are not liquidated or retroactively blocked.
- Scoring weights remain unchanged. The score is explicitly labelled a heuristic,
  not a success probability. ATR multiplied by sqrt(10) is a volatility proxy,
  not a directional profit forecast.

## Offline evaluation

review_scoring.py reads cached data only, with no provider requests or account
writes. 48 usable symbols span 21 April–30 September 2026. Last 30% of dates
(starting 13 August) are used as the later comparison period. Calibration trades
crossing the split are excluded. Signals use prior daily bars, entries use next
available opening prices, adverse opening gaps fill at open, and ambiguous
same-bar stop/target hits assume the stop first. Unlike the older backtester,
there is no arbitrary ten-session exit absent from the deployed system.

At assumed 0.10% round-trip friction:

| Variant | Later completed trades | Mean return per completed trade |
|---|---:|---:|
| Current weights | 4 | -2.10% |
| Current weights with new guards | 4 | -2.10% |
| RSI restricted to 55–65 | 6 | -1.10% |
| Stop at least 1.5 ATR | 0 | Not measurable |
| Minimum score 85 | 0 | Not measurable |

0.30% friction worsens the losing results. Open trades are reported separately,
excluded from closed-trade metrics; no whole-portfolio performance claim is made.
The new guards prevent the observed reuse pattern but did not improve this small
historical sample. This sample cannot support picking an optimal strategy.
Current-cache selection/survivorship bias, short histories, daily OHLC ambiguity,
provider delays, omitted FX/dividends and assumed costs limit the results.

Next validation needs longer point-in-time histories across market conditions,
untouched evaluation periods, realistic quote/execution costs, and prospective
paper results. Do not select a new weight set merely because it fits this sample.

## Verification

Seven offline tests cover persisted cooldowns, reset/new-signal sequencing, other
symbols, target exits, stale/future data, simultaneous stop checks, blocked re-buy,
scanner exclusion of blocked candidates, and refusal to buy from a cached quote. Python compilation and server import pass.

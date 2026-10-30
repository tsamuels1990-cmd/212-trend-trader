# Isolated momentum paper experiment

This is an opt-in standalone runner. It is not imported by `server.py`, does not
call Trading 212, and never touches the existing paper account or Android UI.
The default starting balance is USD 1,000. It is not yet a matched comparison
with the existing strategy: the universe, starting date and execution models
differ. No profitability claim is made.

## Session policy revision (v0.2, 30 October 2026)

- Entries require more than the configured holding period plus 15 minutes before
  the provider-reported regular session end (75 minutes with the current settings).
- Positions record their entry session end. Five minutes before that close,
  the runner attempts an exit using a fresh regular-session quote.
- A timeout or session-close exit that cannot execute is persisted as
  `exit_pending`; status starts with `EXIT OVERDUE`. The runner retries on later
  polls and closes only on valid data. It never fabricates a fill at a stale price.
- Existing v0.1 state is upgraded in place, preserving balances and closed trades.
  `policy_changes` records the activation time, trade count and cash. Results before
  and after this change must be evaluated separately. Old positions without a
  recorded session end adopt the first available snapshot's session end; their
  original timeout still applies.
- This reduces overnight exposure but cannot guarantee liquidation during feed
  outages or missed polls. Targets/stops remain triggers, not guaranteed loss caps.

## Experimental rules

- A deliberately small fixed watchlist: AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA.
  This is not a scan of all Trading 212 instruments or a verified current
  tradability check.
- Completed five-minute bars from the current regular session; at least 22
  contiguous bars, so this setup cannot enter during the first 110 minutes.
- Close above EMA9 above EMA21, three-bar high breakout, relative volume >=1.2,
  average prior 20-bar dollar volume >=$1m, last three-bar advance <=1.5%, and
  quote within 0.25% of signal close. These are uncalibrated hypotheses.
- Last-price timestamp must be between zero and 90 seconds old. Completed bar
  start must be between 300 and 660 seconds old. Both gates apply at entry;
  exits require a fresh quote during regular hours.
- Rank qualifying candidates by capped relative volume plus recent advance.
  This score is not a probability or an estimate of expected return.
- One position using the experiment's cash. Target +1% and stop -0.75% are based
  on modeled net liquidation return. Timeout 60 minutes if a fresh quote is
  available. These are triggers, not guaranteed execution prices or loss caps.
- No same-poll replacement. Every exit starts a 30-minute symbol cooldown and
  requires a later valid non-qualifying signal followed by a new qualifying bar.
  Previously used bars cannot produce another entry.

## Execution and data limitations

The Yahoo chart feed is experimental. A timestamp age check does not establish
exchange real-time entitlement, executable liquidity, or reliability. No bid/ask
spread is supplied. Buys are modeled at last price +0.10%; sells at last price
-0.10%. That is an assumed adverse cost per side, not measured slippage or a
broker fee schedule. `--fx-per-side-pct 0.15` can add a conversion-cost scenario;
the default zero assumes capital remains in USD. Other taxes/levies are not
modeled. Rerun with higher costs using a separate state file for sensitivity.

The loop polls at most once per minute under normal responses, not continuously.
Outside regular hours it probes one symbol every five minutes. While holding,
it requests only the held symbol.
Targets/stops between polls may be missed. Missing/stale data or closed sessions
pause exits as well as entries. Timeouts do not force a fill without fresh data;
positions may remain overnight, including through outages and price gaps. There
is no end-of-day liquidation guarantee. Corporate actions and instrument
tradability are not handled in this experimental runner. A gap can produce a
loss much larger than the configured stop. These limitations preclude live use.

## Run and inspect

```bash
python3 -m unittest -v test_momentum_paper
python3 momentum_paper.py --state research/momentum-paper.json
# Explicitly opt in to repeated polling:
python3 -u momentum_paper.py --state research/momentum-paper.json --loop
```

The default command performs one pass and exits. `--loop` repeats until stopped.
Keep the runtime state and logs outside tracked source paths. A nonblocking
file lock prevents simultaneous runners using the same state. State saves use
an atomic replacement; invalid state/config changes stop instead of resetting
the account. Do not delete state to hide losses.

State records cash, modeled equity, position, all closed trades, diagnostics,
assumptions, quote ages, errors and last poll time. Equity may be stale while a
quote is unavailable; read status and timestamps alongside it. Closed-trade
history is not truncated. Symbols/settings are fixed for each state file.

The first milestone is reliable data capture and forward paper observation.
Next: matched-start baseline, recorded intraday data for out-of-sample replay,
cost sensitivity and a dashboard comparison. This implementation does not yet
provide historical backtesting, an API endpoint or Android controls.

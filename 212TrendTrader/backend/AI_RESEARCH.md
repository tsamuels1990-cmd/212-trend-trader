# AI selection research — 30 October 2026

This is a separate research pipeline. Neither paper selector imports it.
It has no broker integration and cannot place orders or modify paper balances.

## Current deployment

Source: experiment/momentum-paper-20261027 branch.
Runtime: /home/tsamuels1990/212-ai-research
Archive: runtime/intraday.sqlite (SQLite WAL; include WAL when backing up an
active database, or use SQLite's backup API).
Collector log and PID: runtime/collector.log and runtime/collector.pid.

The collector is a detached low-priority process supervised by the user's cron.
An @reboot entry starts it after server boot and a per-minute watchdog restarts
it if it exits. Recovery requires the host and cron daemon to be running.
The watchdog checks the archive lock rather than trusting potentially stale PIDs;
a second watchdog lock serialises launch checks. Existing trading workers are
never inspected or restarted by this watchdog. No root service is installed.
Watchdog events: runtime/watchdog.log; cron output: runtime/watchdog-cron.log.
The managed crontab block is labelled BEGIN/END 212 AI COLLECTOR. Remove that
block before intentionally stopping collection; otherwise cron restarts it.
The prior crontab is saved in runtime/crontab.before-watchdog.txt.
A process crash is recovered; a live but stalled process or repeated data-source
errors are not automatically repaired. Inspect fetch logs for data health.

## Data

Seven symbols: AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA.
Initial backfill uses one month of Yahoo five-minute regular-session data.
Subsequent polls request five days every five minutes during a weekday clock
window. The clock is only a request budget, not an exchange calendar.
Completed valid OHLCV bars are deduplicated; revised bars retain an audit row.
Fetch outcomes are logged. Source timestamps do not establish executable quotes,
exchange entitlement, or point-in-time historical availability.
No paid data subscription or model API is configured.

## Offline training

Copy the archive consistently to a separate training machine.
Install lightgbm, numpy, scikit-learn and joblib in a separate environment.
Run:
python3 ai_train_candidate.py --db intraday.sqlite --output candidate-001

The trainer refuses fewer than 60 usable sessions. This is an engineering
minimum, not statistical proof. It produces an uncalibrated LightGBM candidate
and validation/test log loss compared with historical class frequencies.
The first 60% of sessions train, the next 20% validate, the final 20% test;
one session is excluded at each split boundary. No automatic promotion exists.

Features use only the completed signal bar and earlier bars. Outcomes use the
next bar's open and the next twelve contiguous bars in the same session.
Current fixed assumptions: +1% target, -0.75% stop, 60 minutes,
0.1% adverse cost per side. Entries need more than 75 minutes to the inferred
observed session end. Stop/target order within a candle can be ambiguous;
those examples are excluded. Revised bars and missing-bar windows are excluded.
Intrabar barrier fills remain idealised; this is NOT an execution backtest.
No daily-loss limit, spreads, FX, taxes, corporate-action validation, market-wide
context, probability calibration or portfolio performance evaluation is provided.

## Next validation work

Acquire longer verified intraday history, or accumulate forward observations.
Train off the constrained backend, compare against simple baselines, calibrate
probabilities, and run realistic chronological portfolio replays.
Only then add a versioned shadow predictor, which must be unable to submit orders.
Both existing paper strategies remain independent.

Tests: python3 -m unittest -v test_ai_research test_momentum_paper

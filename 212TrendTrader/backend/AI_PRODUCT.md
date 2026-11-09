# AI research product checkpoint — 9 November 2026

This is an isolated development pipeline. It does not import either trading
strategy or any broker client, and cannot send real orders. The original trend
and short-term momentum workers and their balances are unchanged.

## What is implemented

- A configurable default universe of 30 US stocks across several sectors.
  This is a development basket, not the complete Trading 212 universe, and
  current selection has survivorship bias. Listing/tradability must be checked
  before any broker integration.
- Free five-minute historical import to a separate SQLite archive, with a
  provenance report and per-symbol error/coverage recording. No subscriptions.
- A small gradient-boosted classifier, separate sigmoid probability calibration,
  and chronological train/calibration/validation/test partitions. A whole session
  is omitted at each boundary. Fixed thresholds are not tuned on the test set.
- An account replay with a single position, 25% allocation, 1% net target,
  0.75% net stop, 60-minute hold, assumed 0.1% adverse execution per side,
  five entries per day, a 30-minute post-loss cooldown and a realised daily
  loss lock at 1%. This lock is not a guarantee of maximum loss.
- Gap exits use observed opens. If a candle touches both barriers, the stop wins.
  Missing execution bars cause a conservative exit, never interpolation.
- A momentum baseline, double-cost stress replay, portfolio returns and estimated
  drawdown alongside prediction metrics. No profit claim is made.
- Forward shadow logging, isolated ongoing collection for all model symbols,
  checks for fresh USD regular-session quotes, and a separate simulated order
  engine. Its stop switch closes a simulated position at the next fresh observed
  quote. Missing or stale prices cannot guarantee stops or session-close exits.

## Current checkpoint

The historical import contains 140,028 bars for 30 stocks across 60 sessions,
17 August–9 November. The incomplete 9 November session is excluded, leaving
59 complete usable sessions. A **development-only** candidate was trained to
exercise the full pipeline; the original 60-session trainer remains untouched.

On the held-out test period (28 October–6 November), calibrated log loss was
0.6151 versus 0.7266 for the class-frequency baseline. The model selected zero
trades under the fixed rules (target probability >=55%, estimated net return
>=0.1%). Zero trades means no demonstrated trading edge. The simple momentum
comparison lost approximately 3.39% over 35 simulated trades under the same
capital limits. Do not interpret this as a reliable forecast.

The default deployed observer is **shadow only**. Its balance stays $1,000
because it logs predictions without submitting simulated orders.

## Paper gates

`ai_forward.py --mode paper` requires all of the following:

1. A research (not development) model with at least 60 usable complete sessions.
2. At least 30 held-out trades, positive base-cost and double-cost returns,
   better prediction loss than the frequency baseline, better return than the
   momentum baseline and <=5% estimated held-out drawdown. These are engineering
   gates, not statistical proof of profitability.
3. At least ten forward sessions and 100 logged predictions for that same model.
   Counts establish operational observation only; outcome and execution quality
   still need review before approval.
4. Explicit reviewed model SHA-256 and enabled entries in the control file.

The report always starts with `paper_eligible=false`; it is never automatically
promoted. The runner derives research checks from the report blockers and
requires a separate matching control approval. A model update requires a new
state path and new forward observation. Never edit reports to bypass gates.

Runtime control default:

```json
{"entries_enabled": false, "halt": false, "approved_model_sha256": null}
```

Set `halt=true` to prevent entries and request closure of any simulated position
on the next fresh quote. The watchdog always starts shadow mode. Do not switch
to shadow with an open simulated position: the runner refuses that transition.

## Commands

Run from `/home/tsamuels1990/212-ai-research`. Use the separate `.venv-product`
environment and one CPU thread to avoid loading the existing backend.

```bash
python3 -m unittest -v test_ai_research test_ai_product
python3 ai_product.py backfill --output runtime/new-history.sqlite
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv-product/bin/python ai_product.py train \
  --db runtime/new-history.sqlite --output runtime/new-research-candidate
```

`--symbols AAPL MSFT ...` selects an explicit import basket. A new backfill
requires a new output filename. `--development` permits >=25 sessions for
plumbing tests, but permanently blocks that candidate from paper promotion.
Production/research training retains the 60-session minimum.

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv-product/bin/python ai_forward.py \
  --model runtime/product-candidate-20261109 \
  --state runtime/product-shadow-20261109.json \
  --control runtime/product-control.json \
  --history runtime/product-history-20261109.sqlite --mode shadow --loop
```

On first start, `--history` seeds a **new** isolated forward archive. The observer
then saves current-session observations for all 30 symbols, retaining revisions.
Use SQLite's backup API to snapshot its `.bars.sqlite` archive for later training;
do not copy a live SQLite file without its WAL. Do not retrain automatically:
new evaluations and threshold changes must preserve a genuinely unseen period.

`ai_product_watchdog.py` uses locks to restart only this shadow observer. It is
run at reboot and once per minute in its own labelled user cron block. The
observer requests full stock snapshots once per five-minute batch during the
weekday regular-session clock window. The provider's session bounds and quote
freshness, rather than that clock, decide whether any prediction/fill is usable.

Only load trusted local `model.pkl` files. Their checksum and exact scikit-learn
version must match their manifest. The dependency lock is in
`requirements-ai-product.txt`.

## Remaining work

- Collect and evaluate forward prediction outcomes and execution behaviour.
- Investigate the lack of qualifying signals using development/validation data;
  do not tune against the already inspected final test period.
- Validate corporate actions, history completeness, exchange holidays and
  early-close behaviour. Historical session-end inference is approximate.
- Add bid/ask or other suitable execution data and realistic broker/FX costs.
- Acquire more market regimes and a broader point-in-time stock universe.
- Review multiple chronological walk-forward periods and untouched future data.
- Add user-facing status/dashboard integration after the strategy earns paper
  promotion. This checkpoint does not change the Android APK.
- Real-money broker integration is not implemented or enabled.

# Background paper scanning

The paper backend scans the US stock universe using cached completed daily history even while a position is open. It preserves the configured scan interval (currently 900 seconds), ranks candidates by daily score, and keeps at most 20 candidates. A separate task refreshes their quotes, with three concurrent requests, then waits 60 seconds before the next refresh. Provider delays can lengthen a refresh cycle.

Position monitoring remains a separate task using the configured check interval. Entries revalidate history, cooldown and quotes immediately before buying and remain restricted to one open paper position. Scan errors disable shortlist entries until a successful refresh; settings changes require a new scan. Daily-history freshness and stop-loss cooldown rules remain in force.

Yahoo quotes older than 120 seconds are classified as delayed and cannot trigger automatic paper entries or exits. This is a quote eligibility limit, not a guarantee of exchange real-time data or executable broker prices. Missing/stale quotes pause automatic exits. No live broker-order automation was added.

`GET /paper/automation` includes background scan status, scanned/universe counts, shortlist quote ages and eligibility, last shortlist refresh, and last successful position check/error. Position state includes last checked time and quote timestamp/age. Existing Android labels do not yet display these new fields; its next-scan text may still say it waits for the open position to close.

Validation: `python -m unittest test_background_scanning test_broad_market test_entry_rules` (21 tests). Tests cover open-position scans, a 20-quote cap, settings invalidation, stale/unavailable quotes, automatic target exits, and no duplicate buy while holding a position. An actual sell/rescan/rebuy cycle still requires forward paper observation.

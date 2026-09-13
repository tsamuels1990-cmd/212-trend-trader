# 212 Trend Trader — Android first version

This is a first-version Android app for the user's Trading 212 Invest account.

## Current capabilities

- Android/Compose UI
- Paper mode as the default
- Configurable 3% profit target
- Configurable stop loss
- Trend scoring using:
  - price vs 20-day SMA
  - 20-day vs 50-day SMA
  - RSI
  - 20-day momentum
  - recent direction
- Market scan against an initial universe of large US stocks
- Paper buy/sell
- One-position-at-a-time paper risk control
- Trading 212 API client for account summary, positions, instruments and market orders
- Demo/live API environment switch
- API credentials are entered locally and are never hard-coded

## Important limitations

1. The first version uses Stooq daily CSV data for research/scanning. It may be delayed and does not provide a guaranteed real-time quote stream.
2. The Trading 212 public API does not itself provide a general live quote/candle feed in the documented v0 endpoints. A production version should use a dedicated market-data provider for current prices and historical candles.
3. LIVE mode is intentionally not wired to automatic order execution. The API client exists, but the first version requires a later explicit safety-reviewed automation layer.
4. No investment return is guaranteed. A 3% take-profit strategy can lose money, including through gaps, slippage, spreads, fees and false trend signals.
5. Never put a Trading 212 API secret into source code or share it with anyone.

## Trading 212 API facts

The current official API documentation lists:
- demo: https://demo.trading212.com/api/v0
- live: https://live.trading212.com/api/v0
- instrument metadata
- account summary
- positions
- market/limit/stop/stop-limit orders
- API rate limits
- API-key/secret Basic authentication

The API is available for Invest and Stocks ISA accounts. Orders are executed in the account's primary currency.

## Build

Open the project in Android Studio, allow Gradle sync, then Run on the Samsung S25 Ultra.

The app package is `uk.co.twelvetrendtrader`.

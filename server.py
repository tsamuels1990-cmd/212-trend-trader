
@app.get("/scanner/scan")
async def scanner_scan(
    symbols: str = "AAPL"
):
    results = []

    symbol_list = [
        s.strip().upper()
        for s in symbols.split(",")
        if s.strip()
    ]

    for symbol in symbol_list:
        try:
            candles_response = await market_candles(
                symbol=symbol,
                outputsize="compact"
            )

            analysis = analyse(candles_response["candles"])

            results.append({
                "symbol": symbol,
                "analysis": analysis
            })

        except HTTPException as exc:
            results.append({
                "symbol": symbol,
                "error": exc.detail
            })

    results.sort(
        key=lambda x: x.get("analysis", {}).get("score", -1),
        reverse=True
    )

    return {
        "count": len(results),
        "results": results
    }

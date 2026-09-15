import argparse,json,time,urllib.parse,urllib.request,random
from datetime import datetime,timezone
from pathlib import Path

def yahoo_symbol(symbol):
    return symbol.replace(".","-").replace("/","-")

def download(symbol,years):
    url=("https://query2.finance.yahoo.com/v8/finance/chart/"
         +urllib.parse.quote(yahoo_symbol(symbol))
         +f"?range={years}y&interval=1d&events=history")
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=30) as response:
        result=json.load(response)["chart"]["result"][0]
    stamps=result["timestamp"]; quote=result["indicators"]["quote"][0]; candles=[]
    for i,stamp in enumerate(stamps):
        try:
            values=[quote[k][i] for k in ("open","high","low","close","volume")]
            if any(v is None for v in values): continue
            candles.append(dict(time=datetime.fromtimestamp(stamp,timezone.utc).date().isoformat(),
                                open=values[0],high=values[1],low=values[2],
                                close=values[3],volume=values[4]))
        except (IndexError,KeyError,TypeError): pass
    return candles

def load_state(path):
    try:
        state=json.loads(path.read_text())
        return state if isinstance(state,dict) else {}
    except (OSError,ValueError): return {}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--batch-size",type=int,default=250)
    p.add_argument("--years",type=int,default=5)
    p.add_argument("--min-price",type=float,default=5.0)
    p.add_argument("--min-dollar-volume",type=float,default=5_000_000)
    p.add_argument("--min-candles",type=int,default=750)
    p.add_argument("--volume-days",type=int,default=60)
    p.add_argument("--delay",type=float,default=.35)
    p.add_argument("--retry-failed",action="store_true")
    a=p.parse_args()
    instruments=json.load(open("trading212_instruments.json"))
    symbols=sorted({x["shortName"].upper() for x in instruments
                    if x.get("type")=="STOCK"
                    and str(x.get("ticker","")).endswith("_US_EQ")
                    and x.get("shortName")})
    folder=Path("research_cache"); folder.mkdir(exist_ok=True)
    state_file=folder/"download_state.json"; state=load_state(state_file)
    for f in folder.glob("*_compact.json"):
        state.setdefault(f.stem.replace("_compact",""),{"status":"accepted"})
    pending=[s for s in symbols if s not in state or
             (a.retry_failed and state.get(s,{}).get("status")=="failed")]
    random.Random(212).shuffle(pending)
    selected=pending[:a.batch_size]
    accepted=rejected=failed=0
    print(f"Universe {len(symbols)} | processed {len(state)} | this batch {len(selected)}")
    for n,symbol in enumerate(selected,1):
        try:
            candles=download(symbol,a.years)
            recent=candles[-a.volume_days:]
            price=float(candles[-1]["close"]) if candles else 0
            dollar_volume=(sum(float(c["close"])*float(c["volume"]) for c in recent)
                           /len(recent) if recent else 0)
            if len(candles)>=a.min_candles and price>=a.min_price and dollar_volume>=a.min_dollar_volume:
                payload={"symbol":symbol,"count":len(candles),"candles":candles}
                (folder/f"{symbol}_compact.json").write_text(json.dumps(payload))
                status="accepted"; accepted+=1
            else:
                status="rejected"; rejected+=1
            state[symbol]={"status":status,"candles":len(candles),
                           "price":round(price,2),"avg_dollar_volume":round(dollar_volume)}
            print(f"[{n}/{len(selected)}] {symbol}: {status} ({len(candles)} candles, USD {price:.2f}, USD {dollar_volume:,.0f}/day)")
        except Exception as error:
            failed+=1; state[symbol]={"status":"failed","error":str(error)[:160]}
            print(f"[{n}/{len(selected)}] {symbol}: failed ({error})")
        state_file.write_text(json.dumps(state,indent=2))
        time.sleep(a.delay)
    remaining=sum(s not in state for s in symbols)
    total_accepted=sum(x.get("status")=="accepted" for x in state.values())
    print(f"DONE: {accepted} accepted, {rejected} rejected, {failed} failed")
    print(f"TOTAL: {total_accepted} eligible | {remaining} remaining")

if __name__=="__main__": main()

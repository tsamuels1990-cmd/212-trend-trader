import argparse,json,math
from collections import defaultdict
from pathlib import Path
from strategy import analyse,sma,sma

def load_market(folder):
    out={}
    for f in Path(folder).glob("*_compact.json"):
        try:
            c=sorted(json.loads(f.read_text())["candles"],key=lambda x:x["time"])
            if len(c)>=52: out[f.stem.replace("_compact","")]=c
        except (OSError,ValueError,KeyError,TypeError): pass
    return out

def wilson(w,n,z=1.96):
    if not n:return 0,0
    p=w/n; d=1+z*z/n; c=(p+z*z/(2*n))/d
    m=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/d
    return 100*(c-m),100*(c+m)

def simulate(market,target,stop,minimum,hold,friction):
    bydate=defaultdict(dict); histories={s:[] for s in market}
    for s,cs in market.items():
        for c in cs: bydate[c["time"]][s]=c
    position=None; trades=[]
    for date in sorted(bydate):
        today=bydate[date]
        was_open = position is not None
        if position and position["symbol"] in today:
            c=today[position["symbol"]]; position["days"]+=1
            high,low,close=map(float,(c["high"],c["low"],c["close"]))
            outcome=raw=None
            if low<=position["stop_price"]: outcome,raw="stop",-stop
            elif high>=position["target_price"]: outcome,raw="target",target
            elif position["days"]>=hold:
                outcome="timeout"; raw=(close/position["entry_price"]-1)*100
            if outcome:
                trades.append({**position,"exit_date":date,"outcome":outcome,
                               "return_pct":raw-friction})
                position=None
        if position is None and not was_open:
            spy_history=histories.get("SPY",[])
            if len(spy_history)>=200:
                spy_closes=[float(c["close"]) for c in spy_history]
                spy50=sma(spy_closes,50)
                spy200=sma(spy_closes,200)
                if spy_closes[-1]>spy200 and spy50>spy200:
                    regime="bullish"
                elif spy_closes[-1]<spy200 and spy50<spy200:
                    regime="bearish"
                else:
                    regime="sideways"
            else:
                regime="unknown"
            candidates=[]
            for s,c in today.items():
                if s=="SPY" or len(histories[s])<51: continue
                r=analyse(histories[s],min_score=minimum,
                          profit_target_pct=target,stop_loss_pct=stop)
                if r.get("qualified"): candidates.append((r["score"],s))
            if candidates:
                score,s=max(candidates); entry=float(today[s]["open"])
                position={"symbol":s,"entry_date":date,"entry_price":entry,
                          "target_price":entry*(1+target/100),
                          "stop_price":entry*(1-stop/100),"score":score,"days":0,"regime":regime}
        for s,c in today.items(): histories[s].append(c)
    return trades

def stats(trades):
    n=len(trades); wins=[t for t in trades if t["return_pct"]>0]
    losses=[t for t in trades if t["return_pct"]<=0]
    gp=sum(t["return_pct"] for t in wins); gl=abs(sum(t["return_pct"] for t in losses))
    eq=peak=1.0; dd=0.0
    for t in trades:
        eq*=1+t["return_pct"]/100; peak=max(peak,eq); dd=min(dd,eq/peak-1)
    lo,hi=wilson(len(wins),n)
    return n,100*len(wins)/n if n else 0,lo,hi,sum(t["return_pct"] for t in trades)/n if n else 0,(eq-1)*100,gp/gl if gl else float("inf"),dd*100

def show(label,trades):
    n,w,lo,hi,av,comp,pf,dd=stats(trades)
    p="inf" if math.isinf(pf) else f"{pf:.2f}"
    print(f"{label}: trades={n}, win={w:.1f}% (95% CI {lo:.1f}-{hi:.1f}), avg={av:+.3f}%, compound={comp:+.2f}%, PF={p}, maxDD={dd:.2f}%")

def run(a):
    market=load_market(a.cache_dir)
    dates=sorted({c["time"] for cs in market.values() for c in cs})
    split=dates[int(len(dates)*a.split)] if dates else ""
    print(f"Loaded {len(market)} symbols | validation starts {split}")
    print(f"Friction {a.friction:.3f}% round trip | holding limit {a.hold_days} sessions")
    for target in a.targets:
        for stop in a.stops:
            trades=simulate(market,target,stop,a.min_score,a.hold_days,a.friction)
            print(f"\nTARGET {target:.1f}% | STOP {stop:.1f}%")
            show("Calibration",[t for t in trades if t["entry_date"]<split])
            show("Validation ",[t for t in trades if t["entry_date"]>=split])
            for regime in ("bullish","sideways","bearish"):
                subset=[t for t in trades if t.get("regime")==regime]
                show(f"  {regime.title():8}",subset)
            for regime in ("bullish","sideways","bearish"):
                subset=[t for t in trades if t.get("regime")==regime]
                show(f"  {regime.title():8}",subset)

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--cache-dir",default="market_cache")
    p.add_argument("--targets",nargs="+",type=float,default=[1,2,4,6,8])
    p.add_argument("--stops",nargs="+",type=float,default=[1,1.5,2,3])
    p.add_argument("--min-score",type=float,default=75)
    p.add_argument("--hold-days",type=int,default=10)
    p.add_argument("--friction",type=float,default=.10)
    p.add_argument("--split",type=float,default=.70)
    run(p.parse_args())

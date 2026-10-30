"""Offline research trainer. Never imported by the trading backend.
Run on a separate machine with lightgbm, numpy, scikit-learn, joblib installed.
This produces a candidate only; it does not enable AI trading.
"""
import argparse
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

FEATURES = ["return_5m", "return_15m", "return_60m", "volume_ratio",
            "range_pct", "distance_mean20", "minute_of_session"]


def examples(db):
    groups = defaultdict(list)
    for row in db.execute("""SELECT symbol,session,stamp,open,high,low,close,volume
                             FROM bars WHERE revisions=0 ORDER BY symbol,stamp"""):
        groups[row[:2]].append(row[2:])
    result = []
    # Class 0 = stop first; 1 = timeout; 2 = target first.
    # Cost assumptions match paper: 0.1% adverse per side.
    for (symbol, day), bars in groups.items():
        for i in range(20, len(bars)-12):
            history, future = bars[i-20:i+1], bars[i+1:i+13]
            window = history + future
            if any(b[0]-a[0] != 300 for a,b in zip(window,window[1:])):
                continue
            # Conservative observed-session boundary, also handles short sessions.
            if bars[-1][0]+300-bars[i][0]-300 <= 4500:
                continue
            closes = [r[4] for r in history]
            mean_volume = sum(r[5] for r in history[:-1])/20
            if mean_volume <= 0:
                continue
            entry = future[0][1]*1.001
            target, stop = entry*1.01/.999, entry*.9925/.999
            outcome, net = 1, (future[-1][4]*.999/entry-1)*100
            ambiguous = False
            for r in future:
                _,o,h,l,c,v = r
                if o >= target or o <= stop:
                    outcome = 2 if o >= target else 0
                    net = (o*.999/entry-1)*100
                    break
                if h >= target and l <= stop:
                    ambiguous = True
                    break
                if h >= target:
                    outcome,net = 2,1.
                    break
                if l <= stop:
                    outcome,net = 0,-.75
                    break
            if ambiguous:
                continue
            last = history[-1]
            features = [(closes[-1]/closes[-2]-1)*100,
                        (closes[-1]/closes[-4]-1)*100,
                        (closes[-1]/closes[-13]-1)*100,
                        last[5]/mean_volume,
                        (last[2]-last[3])/last[4]*100,
                        (closes[-1]/(sum(closes[:-1])/20)-1)*100,
                        (last[0]-bars[0][0])/60]
            if all(math.isfinite(x) for x in features):
                result.append((day,symbol,features,outcome,net))
    return sorted(result, key=lambda r:(r[0],r[1]))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    db=sqlite3.connect("file:"+str(a.db.resolve())+"?mode=ro",uri=True)
    rows=examples(db)
    days=sorted({r[0] for r in rows})
    if len(days)<60:
        raise SystemExit("NOT TRAINED: need at least 60 usable sessions for an initial research split; found "+str(len(days))+
                         ". This minimum is an engineering gate, not proof of statistical sufficiency.")
    import numpy as np
    from lightgbm import LGBMClassifier
    from sklearn.metrics import log_loss
    import joblib
    cut1,cut2=int(len(days)*.6),int(len(days)*.8)
    train_days=set(days[:cut1])
    validation_days=set(days[cut1+1:cut2])
    test_days=set(days[cut2+1:])
    def split(selected):
        data=[r for r in rows if r[0] in selected]
        return np.asarray([r[2] for r in data]),np.asarray([r[3] for r in data]),data
    x,y,train=split(train_days)
    if set(y)!={0,1,2}:
        raise SystemExit("NOT TRAINED: all three outcome classes required")
    model=LGBMClassifier(n_estimators=100,max_depth=3,num_leaves=7,
                         min_child_samples=100,n_jobs=1,verbosity=-1,random_state=212)
    model.fit(x,y)
    frequency=np.bincount(y,minlength=3)/len(y)
    metrics={}
    for name,selected in [("validation",validation_days),("test",test_days)]:
        xx,yy,data=split(selected)
        if not len(data):raise SystemExit("Empty evaluation split")
        probabilities=model.predict_proba(xx)
        metrics[name]={"examples":len(data),"first":min(selected),"last":max(selected),
                       "log_loss":float(log_loss(yy,probabilities,labels=[0,1,2])),
                       "baseline_log_loss":float(log_loss(yy,np.tile(frequency,(len(yy),1)),labels=[0,1,2]))}
    a.output.mkdir(parents=True,exist_ok=False)
    joblib.dump(model,a.output/"candidate.joblib")
    report={"status":"RESEARCH CANDIDATE ONLY; NOT APPROVED FOR TRADING",
            "features":FEATURES,"classes":{"0":"stop","1":"timeout","2":"target"},
            "metrics":metrics,"sessions":len(days),"train_examples":len(train),
            "limitations":["Historical feed is not point-in-time verified; survivorship bias possible",
                           "OHLC path ambiguity excluded; barrier fills are idealised",
                           "No measured spreads, latency, FX, fees or taxes",
                           "Probabilities are uncalibrated; no portfolio backtest or profitability claim",
                           "One session excluded between each temporal split",
                           "Current small watchlist; minimum history gate is not statistical validation"]}
    (a.output/"report.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()

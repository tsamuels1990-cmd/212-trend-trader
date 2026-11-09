"""Forward shadow predictions and gated simulated trading. No broker integration.

Default mode is shadow. Paper mode requires a reviewed model hash, passing
research gates, and at least ten forward sessions in this model's own log.
Control halt=true closes a simulated position on the next fresh observed quote.
Stale quotes pause fills; they cannot guarantee stop or daily loss caps.
"""
import argparse
import fcntl
import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_product import Policy, NY, atomic_json, features, load_bundle, score


def snapshot(chart, now):
    from ai_research_data import clean_bars
    meta=chart["meta"]
    regular=meta["currentTradingPeriod"]["regular"]
    start,end=int(regular["start"]),int(regular["end"])
    rows=clean_bars(chart,now)
    bars=[(r[0],*r[2:]) for r in rows if start<=r[0]<end]
    bars.sort()
    result={"bars":bars,"price":float(meta["regularMarketPrice"]),
            "quote_time":int(meta["regularMarketTime"]),"session_start":start,
            "session_end":end,"currency":meta.get("currency")}
    if not math.isfinite(result["price"]) or result["price"]<=0:
        raise ValueError("Invalid quote")
    return result


def fresh(s, now):
    return (s.get("currency")=="USD" and s["session_start"]<=now<s["session_end"]
            and 0<=now-s["quote_time"]<=90
            and math.isfinite(s["price"]) and s["price"]>0)


def initial_state(model_hash, policy):
    return {"version":1,"mode":"shadow","model_sha256":model_hash,"seed":policy.seed,
            "cash":policy.seed,"equity":policy.seed,"peak":policy.seed,"position":None,
            "trades":[],"daily":{},"blocked":{},"seen":{},"forward_sessions":[],
            "prediction_count":0,"status":"Starting shadow observation"}


def paper_blockers(manifest, state, control):
    blockers=[b for b in manifest["blockers"] if b!="Forward shadow observation and execution review still required"]
    if manifest["mode"]!="research" or manifest["usable_sessions"]<60:
        blockers.append("Research model/history gate unmet")
    if len(state["forward_sessions"])<10 or state["prediction_count"]<100:
        blockers.append("Need ten forward sessions and 100 predictions")
    if control.get("approved_model_sha256")!=manifest["model_sha256"]:
        blockers.append("Model has not been reviewed for paper use")
    if not control.get("entries_enabled",False):
        blockers.append("Paper entries disabled in control file")
    return blockers


def step(state, snapshots, predictions, now, policy, mode, control, manifest):
    """Pure state transition: observed fresh quotes only, no historical fill replay."""
    day=datetime.fromtimestamp(now,NY).date().isoformat()
    state["last_poll"]=datetime.fromtimestamp(now,timezone.utc).isoformat()
    state["mode"]=mode
    p=state["position"]
    if p:
        s=snapshots.get(p["symbol"])
        if not s or not fresh(s,now):
            state["status"]="Position quote stale/unavailable; fills paused"
            return
        net=s["price"]*(1-policy.cost)
        equity=state["cash"]+p["quantity"]*net
        state["equity"]=equity
        state["peak"]=max(state["peak"],equity)
        ledger=state["daily"].setdefault(day,{"start":equity,"trades":0,"locked":False})
        drawdown=1-equity/state["peak"]
        if equity<=ledger["start"]*(1-policy.daily_loss): ledger["locked"]=True
        reason=None
        if control.get("halt",False): reason="KILL_SWITCH"
        elif ledger["locked"]: reason="DAILY_LOSS"
        elif drawdown>=.05: reason="DRAWDOWN_LIMIT"
        elif net<=p["entry_price"]*(1-policy.stop): reason="STOP"
        elif net>=p["entry_price"]*(1+policy.target): reason="TARGET"
        elif now>=p["session_end"]-120: reason="SESSION_EXIT"
        elif now-p["opened_at"]>=policy.max_hold: reason="TIMEOUT"
        if reason:
            proceeds=p["quantity"]*net
            profit=proceeds-p["invested"]
            state["cash"]+=proceeds
            state["equity"]=state["cash"]
            state["trades"].append(dict(p,closed_at=now,exit_price=net,profit=profit,reason=reason))
            state["position"]=None
            if profit<0: state["blocked"][p["symbol"]]=now+policy.cooldown
            state["status"]="Simulated position closed: "+reason
        else: state["status"]="Simulated position monitored"
        return  # never close and reopen in the same polling cycle
    state["equity"]=state["cash"]
    ledger=state["daily"].setdefault(day,{"start":state["equity"],"trades":0,"locked":False})
    if state["equity"]<=ledger["start"]*(1-policy.daily_loss): ledger["locked"]=True
    if mode=="shadow":
        state["status"]="Shadow only; no simulated orders"
        return
    blockers=paper_blockers(manifest,state,control)
    if control.get("halt",False): blockers.append("Kill switch active")
    if ledger["locked"]: blockers.append("Daily loss lock")
    if ledger["trades"]>=policy.max_trades: blockers.append("Daily trade limit")
    if state["equity"]<=state["peak"]*.95: blockers.append("Drawdown limit")
    if blockers:
        state["status"]="Paper entry blocked: "+"; ".join(blockers)
        return
    for candidate in sorted(predictions,key=lambda r:(-r["expected_return"],r["symbol"])):
        symbol=candidate["symbol"]
        s=snapshots.get(symbol)
        if (not s or not fresh(s,now) or now-s["session_start"]<6300
                or s["session_end"]-now<4500 or now<state["blocked"].get(symbol,0)
                or candidate["target_probability"]<policy.min_probability
                or candidate["expected_return"]<policy.min_expected_return
                or not 0<=now-(candidate["signal_time"]+300)<=360):
            continue
        # No late fills, replayed predictions, or entries on evaluated history.
        if day<=manifest["latest_evaluation_day"]: continue
        invested=state["cash"]*policy.allocation
        price=s["price"]*(1+policy.cost)
        state["position"]={"symbol":symbol,"opened_at":now,"session_end":s["session_end"],
                           "entry_price":price,"invested":invested,"quantity":invested/price,
                           "signal_time":candidate["signal_time"]}
        state["cash"]-=invested
        state["equity"]=state["cash"]+invested*(1-policy.cost)/(1+policy.cost)
        ledger["trades"]+=1
        state["status"]="Simulated buy from fresh observed quote: "+symbol
        return
    state["status"]="No qualifying fresh entry"


def run(args):
    from ai_research_data import fetch,clean_bars,connect,save
    bundle,manifest=load_bundle(args.model)
    policy=Policy(**manifest["policy"])
    state_path=Path(args.state)
    state_path.parent.mkdir(parents=True,exist_ok=True)
    with state_path.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=json.loads(state_path.read_text()) if state_path.exists() else initial_state(manifest["model_sha256"],policy)
        if state["model_sha256"]!=manifest["model_sha256"]:
            raise ValueError("Use a new state path for a new model")
        if args.mode=="shadow" and state["position"]:
            raise ValueError("Paper position exists; continue paper monitoring until flat")
        archive_path=state_path.with_suffix(".bars.sqlite")
        if args.history and not archive_path.exists():
            source=sqlite3.connect("file:"+str(args.history.resolve())+"?mode=ro",uri=True)
            target=sqlite3.connect(archive_path)
            source.backup(target)
            target.close()
            source.close()
        archive=connect(archive_path)
        last_batch=None
        while True:
            predictions=[]
            snapshots={}
            errors={}
            batch=int(time.time()//300)
            local=datetime.now(NY)
            active=local.weekday()<5 and 570<=local.hour*60+local.minute<960
            symbols=manifest["symbols"] if active and batch!=last_batch else []
            if state["position"]:
                held=state["position"]["symbol"]
                symbols=[held]+[s for s in symbols if s!=held]
            for symbol in symbols:
                try:
                    now=time.time()
                    chart=fetch(symbol,"5d")
                    s=snapshot(chart,now)
                    # Keep forward observations for all configured stocks in an
                    # isolated archive, retaining revisions through save().
                    current=[r for r in clean_bars(chart,now)
                             if s["session_start"]<=r[0]<s["session_end"]]
                    save(archive,symbol,current,now)
                    snapshots[symbol]=s
                    if not fresh(s,time.time()) or s["session_end"]-time.time()<4500:
                        continue
                    x=features(s["bars"],s["session_start"])
                    if x is None: continue
                    signal=s["bars"][-1][0]
                    if not 0<=time.time()-(signal+300)<=360 or signal<=state["seen"].get(symbol,0):
                        continue
                    if datetime.fromtimestamp(now,NY).date().isoformat()<=manifest["latest_evaluation_day"]:
                        continue
                    prob,expected=score(bundle,[x])[0]
                    pred={"observed_at":time.time(),"symbol":symbol,"signal_time":signal,
                          "target_probability":prob,"expected_return":expected,
                          "model_sha256":manifest["model_sha256"]}
                    predictions.append(pred)
                    with state_path.with_suffix(".predictions.jsonl").open("a") as log:
                        log.write(json.dumps(pred)+"\n")
                    state["seen"][symbol]=signal
                    state["prediction_count"]+=1
                    day=datetime.fromtimestamp(now,NY).date().isoformat()
                    if day not in state["forward_sessions"]: state["forward_sessions"].append(day)
                except Exception as e:
                    errors[symbol]=type(e).__name__
                time.sleep(.5)
            last_batch=batch
            try:
                control=json.loads(Path(args.control).read_text())
            except (OSError,ValueError):
                control={"entries_enabled":False,"halt":True}
            step(state,snapshots,predictions,time.time(),policy,args.mode,control,manifest)
            state["data_errors"]=errors
            state["archive"]=str(archive_path)
            state["research_blockers"]=manifest["blockers"]
            state["execution_model"]="Observed last price plus assumed costs; not executable bid/ask"
            atomic_json(state_path,state)
            print(json.dumps({"status":state["status"],"predictions":state["prediction_count"],
                              "mode":args.mode,"errors":errors}),flush=True)
            if not args.loop: break
            time.sleep(60)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model",type=Path,required=True)
    p.add_argument("--state",type=Path,required=True)
    p.add_argument("--control",type=Path,required=True)
    p.add_argument("--history",type=Path,help="Seed a new isolated forward archive from this historical snapshot")
    p.add_argument("--mode",choices=["shadow","paper"],default="shadow")
    p.add_argument("--loop",action="store_true")
    run(p.parse_args())


if __name__=="__main__": main()

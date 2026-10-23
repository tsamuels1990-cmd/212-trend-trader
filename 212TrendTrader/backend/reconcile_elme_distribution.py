"""Dry-run by default. Stop the backend before using --apply.

Example: python reconcile_elme_distribution.py --state paper_state.json
  --evidence review_backup_20261013_195254/paper_state.json [--apply]
The source state is backed up and replaced atomically; repeats are no-ops.
"""
import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from paper_distributions import reconcile_elme


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raw = args.evidence.read_bytes()
    historical = json.loads(raw)
    held = historical.get("position") or {}
    if held.get("symbol") != "ELME" or held.get("entry") != 1.75:
        raise ValueError("Evidence must show the historical ELME position")
    if "20261013" not in str(args.evidence):
        raise ValueError("Expected the inspected October 13 state backup")
    state = json.loads(args.state.read_text())
    changed = reconcile_elme(state, {
        "quantity": held["quantity"], "held_on": "2026-10-13",
        "backup": str(args.evidence.resolve()), "backup_sha256": hashlib.sha256(raw).hexdigest(),
    })
    if args.apply and changed:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = args.state.with_name(args.state.name + ".before_distribution_" + stamp)
        shutil.copy2(args.state, backup)
        temp = args.state.with_suffix(".reconciliation.tmp")
        temp.write_text(json.dumps(state, indent=2, allow_nan=False))
        temp.replace(args.state)
        print("Original state backup:", backup)
    trade = next(t for t in state["trades"] if t["symbol"] == "ELME")
    print(json.dumps({"applied": args.apply and changed, "already_reconciled": not changed,
                      "currency": "USD", "cash": state["cash"],
                      "elme_gross_return_pct": trade["return_pct"],
                      "credit_id": trade["reconciliation_id"]}, indent=2))


if __name__ == "__main__":
    main()

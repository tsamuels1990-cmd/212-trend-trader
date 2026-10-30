"""Cron watchdog for the isolated AI archive; never touches trading processes."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time


def ensure_running(root):
    root = root.resolve()
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / "watchdog.lock").open("a") as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "another watchdog is checking"
        # The collector owns this lock for its whole lifetime. Do not trust a
        # PID file: a stale PID can belong to an unrelated process after reboot.
        with (runtime / "intraday.lock").open("a") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "collector already running"
            fcntl.flock(probe, fcntl.LOCK_UN)
        script = root / "ai_research_data.py"
        if not script.is_file():
            raise FileNotFoundError(script)
        with (runtime / "collector.log").open("ab") as log:
            child = subprocess.Popen(
                ["/usr/bin/nice", "-n", "10", "/usr/bin/python3", "-u",
                 str(script), "--db", str(runtime / "intraday.sqlite"), "--loop"],
                cwd=str(root), stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True)
        # Hold the watchdog lock until the child has acquired the archive lock.
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError("Collector exited during startup: " + str(child.returncode))
            with (runtime / "intraday.lock").open("a") as probe:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    temporary = runtime / ("collector.pid." + str(os.getpid()))
                    temporary.write_text(str(child.pid) + "\n")
                    temporary.replace(runtime / "collector.pid")
                    with (runtime / "watchdog.log").open("a") as events:
                        events.write(json.dumps({"at": time.time(), "event": "collector_started",
                                                 "pid": child.pid}) + "\n")
                    return "started collector " + str(child.pid)
            time.sleep(.1)
        # Leave a slow child alone; a later check will observe its archive lock.
        raise RuntimeError("Collector startup did not acquire archive lock within 10 seconds")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(ensure_running(args.root))


if __name__ == "__main__":
    main()

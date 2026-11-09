"""Restart the isolated shadow observer only. Never enables paper entries."""
import fcntl
import os
import subprocess
from datetime import datetime,timezone
from pathlib import Path


def main():
    base=Path(__file__).resolve().parent
    runtime=base/'runtime'
    runtime.mkdir(exist_ok=True)
    with (runtime/'product-watchdog.lock').open('w') as supervisor:
        try: fcntl.flock(supervisor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        state=runtime/'product-shadow-20261109.json'
        with state.with_suffix('.lock').open('a') as worker:
            try: fcntl.flock(worker,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: return
            # The worker acquires its own lock before any state change.
        model=runtime/'product-candidate-20261109'
        if not (model/'report.json').exists(): return
        env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        with (runtime/'product-shadow.log').open('a') as log:
            process=subprocess.Popen([
                str(base/'.venv-product/bin/python'),'-u',str(base/'ai_forward.py'),
                '--model',str(model),'--state',str(state),
                '--control',str(runtime/'product-control.json'),
                '--history',str(runtime/'product-history-20261109.sqlite'),
                '--mode','shadow','--loop'],cwd=base,env=env,
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        (runtime/'product-shadow.pid').write_text(str(process.pid)+'\n')
        print(datetime.now(timezone.utc).isoformat(),'started shadow observer',process.pid)


if __name__=='__main__': main()

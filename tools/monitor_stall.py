#!/usr/bin/env python3
"""Serial, persistent soak runner; no LLM/API on the ordinary sampling path."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('idle', []),
    ('particle-main', ['--load', 'particle', '--run']),
    ('particle-thread', ['--load', 'particle', '--run', '--exec-mode', 'thread']),
    ('audio-cycle-main', ['--workdir', str(ROOT/'tools/stall_cases'), '--load', 'audio_cycle', '--run']),
    ('audio-cycle-thread', ['--workdir', str(ROOT/'tools/stall_cases'), '--load', 'audio_cycle', '--run', '--exec-mode', 'thread']),
    ('puzzle-main', ['--load', 'puzzle', '--run']),
    ('cube-main', ['--load', 'cube', '--run']),
    ('idle-no-capture', []),
]

def atomic(path, obj):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    tmp.replace(path)

def read_json(path, default):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return default

def anomalies(result):
    kinds=[]
    for key in ('update','draw','frame_interval'):
        if (result.get(key) or {}).get('max_ms',0)>=2000: kinds.append(key+'-2s')
    warm=[s for s in result.get('samples',[]) if s.get('elapsed_s',0)>=60 and s.get('rss_bytes')]
    if len(warm)>=60 and warm[-1]['rss_bytes']-warm[0]['rss_bytes']>=64*1024**2:
        kinds.append('rss-growth-64MiB')
    if result.get('session_thread_alive'): kinds.append('vm-thread-left-alive')
    screen='\n'.join(result.get('screen',[]))
    if any(s in screen for s in ('?SYNTAX','?ERROR','?ILLEGAL','?UNDEFINED','?TYPE','?OUT OF')):
        kinds.append('basic-error')
    return kinds

def event(state_dir, state, key, data, now=None):
    now=time.time() if now is None else now
    # One pending record per category; repeated anomaly evidence remains in run files.
    notified=state.setdefault('event_times',{})
    if now-notified.get(key,0)<6*3600: return False
    queue=read_json(state_dir/'pending.json', {'events':[]})
    queue['events'].append({'key':key,'at':now,**data})
    atomic(state_dir/'pending.json',queue)
    notified[key]=now
    return True

def trim_runs(state_dir, keep=24):
    # Only runner-owned run directories. Never touches the earlier investigation.
    runs=sorted((state_dir/'runs').glob('run-*'))
    normal=[p for p in runs if (p/'normal.json').exists()]
    anomalous=[p for p in runs if (p/'anomaly.json').exists()]
    for group in (normal,anomalous):
        for p in group[:-keep]: shutil.rmtree(p)

def child_rss(pid):
    try: return int(Path(f'/proc/{pid}/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
    except (OSError,IndexError,ValueError): return 0

def host_sample():
    item={'at':time.time(),'load':os.getloadavg()}
    for name,path in [('memory','/proc/meminfo'),('pressure_cpu','/proc/pressure/cpu'),
                      ('pressure_memory','/proc/pressure/memory'),('pressure_io','/proc/pressure/io'),
                      ('cpu_stat','/proc/stat')]:
        try: item[name]=Path(path).read_text()[:4096]
        except OSError: pass
    return item

def trial(state_dir, case, seconds, status):
    name, app_args=case
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    run=state_dir/'runs'/('run-'+stamp+'-'+name)
    run.mkdir(parents=True)
    output=run/'result.json'
    cmd=[str(ROOT/'.venv/bin/python'),str(ROOT/'tools/diagnose_stall.py'),
         '--seconds',str(seconds),'--output',str(output)]
    if name=='idle-no-capture': cmd+=['--capture-sec','0']
    cmd+=['--',*app_args]
    env={**os.environ,'SDL_VIDEODRIVER':'offscreen','SDL_AUDIODRIVER':'dummy'}
    meta={'case':name,'command':cmd,'seconds':seconds,'started':time.time(),
          'headless':True,'audio':'dummy','cpu_quota':'systemd 75% of one logical CPU',
          'note':'Constrained Linux soak, not Windows hardware reproduction'}
    atomic(run/'meta.json',meta)
    kinds=[]
    with (run/'process.log').open('w') as log, (run/'host.jsonl').open('w') as host:
        p=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        started=time.monotonic()
        try:
            while p.poll() is None:
                elapsed=time.monotonic()-started
                sample_file=output.with_suffix('.samples.jsonl')
                rss=child_rss(p.pid)
                status.update(case=name, child_pid=p.pid, run=str(run), heartbeat=time.time(), elapsed_s=elapsed, child_rss_bytes=rss)
                atomic(state_dir/'health.json',status)
                host.write(json.dumps(host_sample())+'\n');host.flush()
                if rss>384*1024**2: kinds.append('rss-limit-384MiB')
                if elapsed>seconds+45: kinds.append('trial-timeout')
                if elapsed>30 and (not sample_file.exists() or time.time()-sample_file.stat().st_mtime>15):
                    kinds.append('heartbeat-stall-15s')
                if kinds:
                    os.killpg(p.pid,signal.SIGTERM)
                    try: p.wait(timeout=5)
                    except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL)
                    break
                time.sleep(1)
        finally:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGKILL)
            p.wait()
    result=read_json(output,{})
    if p.returncode: kinds.append('process-exit-'+str(p.returncode))
    if not result: kinds.append('missing-result')
    else:
        kinds+=anomalies(result)
        if result.get('elapsed_s',0)<seconds*.9: kinds.append('early-end')
    record={'case':name,'run':str(run),'kinds':sorted(set(kinds)),
            'elapsed_s':result.get('elapsed_s'),'returncode':p.returncode,
            'update':result.get('update'),'draw':result.get('draw'),
            'frame_interval':result.get('frame_interval')}
    atomic(run/('anomaly.json' if kinds else 'normal.json'),record)
    return record

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--state-dir',type=Path,required=True)
    ap.add_argument('--seconds',type=int,default=1200)
    ap.add_argument('--once',action='store_true')
    ap.add_argument('--case',choices=[c[0] for c in CASES])
    args=ap.parse_args()
    if not 1<=args.seconds<=3600: ap.error('seconds must be 1..3600')
    d=args.state_dir.resolve();d.mkdir(parents=True,exist_ok=True)
    with (d/'monitor.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=read_json(d/'state.json',{'next_case':0,'completed':0,'cases_seen':[]})
        if state.get('active_run'):
            event(d,state,'monitor-restarted-during-trial',{'interrupted':state['active_run']})
        while True:
            case=next(c for c in CASES if c[0]==args.case) if args.case else CASES[state['next_case']%len(CASES)]
            # Advance before starting so a service OOM/reboot cannot permanently trap one case.
            state['next_case']=(state['next_case']+1)%len(CASES)
            state['active_run']={'case':case[0],'started':time.time()}
            atomic(d/'state.json',state)
            record=trial(d,case,args.seconds,{'monitor_pid':os.getpid(),'completed':state['completed']})
            state['completed']+=1
            state['cases_seen']=sorted(set(state['cases_seen']+[case[0]]))
            for kind in record['kinds']: event(d,state,case[0]+':'+kind,record)
            # Coverage is new evidence too, but not an LLM call every pass.
            if len(state['cases_seen'])==len(CASES) and time.time()-state.get('coverage_at',0)>=86400:
                event(d,state,'coverage-review',{'completed':state['completed'],'cases':state['cases_seen'],
                      'instruction':'Review accumulated evidence; extend hypotheses/cases if useful. Keep monitor running.'})
                state['coverage_at']=time.time()
            state['last_result']=record
            state.pop('active_run',None)
            state['updated']=time.time()
            atomic(d/'state.json',state)
            atomic(d/'health.json',{'monitor_pid':os.getpid(),'heartbeat':time.time(),
                   'completed':state['completed'],'last_result':record})
            trim_runs(d)
            print(json.dumps(record),flush=True)
            if args.once: break
            time.sleep(3)

if __name__=='__main__': main()

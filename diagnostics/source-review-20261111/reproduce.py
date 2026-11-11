"""Issue #90: bounded headless reproductions; no product/service changes."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'tools')]
from test_core import MockIO, run_program, _Recorder
from pyxelbasic.interpreter import Interpreter, tokenize
from pyxelbasic.runtime import CommandQueue, DirectGraphics, InputRing, KeyState, EV_DOWN, EV_UP, EV_CHAR
from pyxelbasic.session import Session
import monitor_stall as monitor
from main import build_parser


def main():
    evidence = {}
    io, vm = run_program([(10, 'DIM M(1,1)'), (20, 'M(0)=7'), (30, 'PRINT M(0,0)')])
    assert vm.arrays['M'][1][0] == 7 and any('ERROR' in s for s in io.out)
    evidence['array_rank_corruption'] = {'array': vm.arrays['M'], 'output': io.out}

    vm = Interpreter(MockIO())
    vm.dim_array('A', [200000])
    vm.store_line(10, 'GOSUB 10')
    vm.prepare_run()
    for _ in range(2000): vm.step()
    assert len(vm.gosub_stack) == 2000 and not vm.arrays
    evidence['resources'] = {'gosub_after_2000_steps': len(vm.gosub_stack), 'run_cleared_arrays': not vm.arrays}
    vm.new_program()
    assert not vm.gosub_stack
    vm.store_line(10, 'FOR I=1 TO 10:GOTO 10')
    vm.prepare_run()
    for _ in range(2000): vm.step()
    evidence['resources']['for_after_2000_steps'] = len(vm.for_stack)
    assert len(vm.for_stack) == 1000
    vm.execute(tokenize('A$="X"'))
    for _ in range(20): vm.execute(tokenize('A$=A$+A$'))
    evidence['resources']['string_after_20_doublings'] = len(vm.vars['A$'])
    assert len(vm.vars['A$']) == 1048576

    q = CommandQueue()
    class Broken:
        def playing(self, ch): raise RuntimeError('injected backend failure')
    t = threading.Thread(target=lambda: q.call('playing', (0,)), daemon=True)
    t.start()
    deadline = time.monotonic()+1
    while not q._dq and time.monotonic()<deadline: time.sleep(.001)
    assert q._dq
    try: q.drain(Broken())
    except RuntimeError as exc: error = str(exc)
    else: raise AssertionError('expected injected exception')
    t.join(.03)
    evidence['queue_exception'] = {'error': error, 'queue_empty': not q._dq, 'waiter_still_alive': t.is_alive()}
    assert t.is_alive() and not q._dq
    q.stop(); t.join(1)
    assert not t.is_alive()

    ring, keys = InputRing(), KeyState()
    ring.push((EV_DOWN, 'UP')); keys.apply_all(ring.drain())
    for _ in range(256): ring.push((EV_CHAR, 'x'))
    dropped = not ring.push((EV_UP, 'UP'))
    keys.apply_all(ring.drain())
    assert dropped and keys.is_down('UP')
    evidence['lost_key_release'] = {'release_dropped': dropped, 'up_still_down': keys.is_down('UP')}

    s = Session(64,42,DirectGraphics(_Recorder()),InputRing(),vsync_enabled=True)
    s.interp.store_line(10,'A=A+1:GOTO 10'); s._start_run()
    before = s.interp.step
    entered, release = threading.Event(), threading.Event()
    def slow_step():
        entered.set(); assert release.wait(1); before()
    s.interp.step = slow_step
    worker = threading.Thread(target=lambda:s.run_frame(1))
    worker.start(); assert entered.wait(1)
    s.request_break()
    evidence['single_statement_cancellation'] = {'break_pending_while_step_blocked': s._break, 'mode':s.mode}
    assert s.mode == 'RUN'
    release.set();worker.join(1)
    s.run_frame(1);assert s.mode == 'EDIT'

    s = Session(64,42,DirectGraphics(_Recorder()),InputRing(),vsync_enabled=True)
    s.interp.store_line(10,'PRINT A$');s._start_run()
    s.interp.set_var('A$', 'X'*262144)
    started=time.perf_counter();s.run_frame(1);elapsed=time.perf_counter()-started
    evidence['one_print_statement']={'characters':262144,'elapsed_ms':elapsed*1000,'note':'wall time includes host scheduling; not an OS stall reproduction'}

    vm = Interpreter(MockIO()); vm.store_line(10,'PRINT 1');vm.store_line(20,'PRINT 2')
    vm.renum(10,0)
    assert len(vm.program)==1
    evidence['renum_zero_loses_line'] = vm.list_lines()

    s = Session(64,42,DirectGraphics(_Recorder()),InputRing())
    try: s._submit_line('9'*5000+' END')
    except ValueError as exc: error = str(exc)
    else: raise AssertionError('expected line-number conversion failure')
    evidence['uncaught_line_number'] = error
    class BadStore:
        def save(self,*args): pass
        def list_names(self): return []
        def load(self,name): return name,'10 PRINT 1\n'+'9'*5000+' END\n'
    s = Session(64,42,DirectGraphics(_Recorder()),InputRing(),filestore=BadStore())
    s.interp.store_line(100,'PRINT "ORIGINAL"')
    s._submit_line('LOAD "bad"')
    assert s.interp.list_lines()==[(10,'PRINT 1')]
    evidence['partial_load_loses_original']={'remaining_program':s.interp.list_lines(),'error_reported':any('?ERROR' in ''.join(row) for row in s.screen.chars)}
    evidence['cli_nonfinite'] = {x: str(build_parser().parse_args(['--vm-cycle-ms', x]).vm_cycle_ms) for x in ['nan','inf']}

    with tempfile.TemporaryDirectory() as tmp:
        d=Path(tmp); state={}
        monitor.event(d,state,'old',{},now=30000)
        original_atomic=monitor.atomic
        def interleaved_atomic(path,obj):
            if path.name=='pending.json': (d/'pending.json').replace(d/'inflight.json')
            original_atomic(path,obj)
        with patch.object(monitor,'atomic',side_effect=interleaved_atomic):
            monitor.event(d,state,'new',{},now=30001)
        inflight=monitor.read_json(d/'inflight.json',{})['events']
        pending=monitor.read_json(d/'pending.json',{})['events']
        assert [x['key'] for x in inflight]==['old']
        assert [x['key'] for x in pending]==['old','new']
        evidence['monitor_handoff_race']={'inflight':[x['key'] for x in inflight],'pending':[x['key'] for x in pending]}
    # Final-screen-only scanning cannot detect an earlier scrolled-away error.
    assert monitor.anomalies({'screen':['OK'],'samples':[]})==[]
    evidence['monitor_error_history']={'final_screen':['OK'],'detected':[],'note':'synthetic final state, not live UI reproduction'}
    print(json.dumps({'python':sys.version,'evidence':evidence},ensure_ascii=False,indent=2))


if __name__=='__main__': main()

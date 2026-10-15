"""Measure actual local performance; --inference opts into one CPU and GPU request."""
import argparse
import json
from pathlib import Path
import statistics
import threading
import time
from htmai.app import ROOT, open_app
from htmai.analyst import Analyst
from htmai.backend import hardware, BackendError


def run(inference=False):
    config=json.loads((ROOT/'config.json').read_text())
    data=ROOT/'data';store=open_app(data)
    a=Analyst(store,data,config)
    report=dict(measured_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),hardware_before=hardware(),
        scope='Single machine, single inference per mode. Includes model load. GPU samples are observed, not a hard memory cap.',cases=[])
    timings=[]
    for _ in range(30):
        start=time.perf_counter();store.search('haxy','SSH authentication');timings.append(time.perf_counter()-start)
    report['cases'].append(dict(name='deterministic_search',runs=30,median_seconds=statistics.median(timings)))
    if inference:
        question='What SSH key type was unsupported? Cite the actual report and resolution.'
        for mode in ('cpu','gpu'):
            # A benchmark-specific config keeps production caches intact and avoids a warmed result.
            cfg=dict(config,temperature=.201)
            agent=Analyst(store,data,cfg)
            model=agent.backend.select()
            active=agent.backend.running()
            if any(m['name']!=model['name'] for m in active):
                report['cases'].append(dict(name=mode,error='Another model is running; benchmark skipped'));continue
            if active:agent.backend.unload(model['name'])
            stop=threading.Event();samples=[]
            def sample():
                while not stop.is_set():
                    try:
                        samples.append(dict(elapsed=time.perf_counter()-started,hardware=hardware(),ollama=agent.backend.running()))
                    except BackendError:pass
                    stop.wait(.5)
            started=time.perf_counter();thread=threading.Thread(target=sample,daemon=True);thread.start()
            try:
                result=agent.ask('haxy',question,mode)
                case=dict(name=mode,wall_seconds=time.perf_counter()-started,result=result,samples=samples)
                report['cases'].append(case)
                if result['backend']=='ollama':
                    times=[]
                    for _ in range(5):
                        tick=time.perf_counter();cached=agent.ask('haxy',question,mode);times.append(time.perf_counter()-tick)
                    report['cases'].append(dict(name=mode+'_cache',median_seconds=statistics.median(times),runs=5,cache_hit=cached['cached']))
            finally:
                stop.set();thread.join(timeout=5)
                try:agent.backend.unload(model['name'])
                except BackendError:pass
    report['hardware_after']=hardware()
    store.close()
    target=ROOT/'benchmarks.json';target.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('Measurements saved to',target)
    for case in report['cases']:
        print(case['name'],case.get('wall_seconds',case.get('median_seconds',case.get('error'))))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inference',action='store_true')
    run(parser.parse_args().inference)

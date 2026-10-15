"""Repeated local benchmarks. No cache hits count as inference. No model downloads."""
import argparse
import json
from pathlib import Path
import statistics
import threading
import time
from htmai.app import ROOT,open_app
from htmai.historian import Historian
from htmai.backend import hardware,BackendError


def aggregate(samples):
    values=[s['wall_seconds'] for s in samples]
    return dict(n=len(values),mean_seconds=statistics.mean(values),median_seconds=statistics.median(values),
        min_seconds=min(values),max_seconds=max(values)) if values else dict(n=0)


def plan(warm_runs):
    if warm_runs<5:raise ValueError('At least five warm uncached runs per configuration are required')
    return ['cold']+['warm']*warm_runs


def run_benchmark(repo='xitui',warm_runs=5,inference=False,question='How did scrollToRect change?'):
    phases=plan(warm_runs)
    config=json.loads((ROOT/'config.json').read_text())
    config.update(num_predict=384,temperature=.2,keep_alive='5m',evidence_bytes=1800,retrieval_limit=4)
    data=ROOT/'data';store=open_app(data);historian=Historian(store,data,config)
    report_dir=data/'benchmarks';report_dir.mkdir(parents=True,exist_ok=True)
    report=dict(version=2,created=time.strftime('%Y-%m-%dT%H:%M:%S%z'),repository=repo,
        question=question,configuration=config,
        methodology='One cold + at least five warm uncached calls per mode. use_cache=False bypasses both reads and writes. Cold is explicitly unloaded. Warm cases must report effective requested mode; fallback cases are excluded.',
        hardware_before=hardware(),runs=[],search=[],cached=[])
    for _ in range(30):
        tick=time.perf_counter();historian.index.retrieve(repo,'scrollToRect');report['search'].append(dict(wall_seconds=time.perf_counter()-tick))
    if inference:
        model=historian.backend.select();report['model']=model
        for mode in ('cpu','gpu'):
            active=historian.backend.running()
            if any(m['name']!=model['name'] for m in active):raise BackendError('Another model is loaded; benchmark aborted')
            if active:historian.backend.unload(model['name'])
            for phase in phases:
                if mode=='gpu':
                    # Device utilization includes recent work by this benchmark.
                    # Let the rolling counter settle; never override the backend guard.
                    if phase=='warm':time.sleep(5)
                    for _ in range(10):
                        hw=hardware()
                        if hw['gpu'] and all(g['utilization_percent']<=30 and g['total_mib']-g['used_mib']>=4608 for g in hw['gpu']):break
                        time.sleep(2)
                samples=[];stop=threading.Event();tick=time.perf_counter()
                def sample():
                    while not stop.is_set():
                        try:samples.append(dict(elapsed=time.perf_counter()-tick,hardware=hardware(),models=historian.backend.running()))
                        except BackendError:pass
                        stop.wait(.5)
                thread=threading.Thread(target=sample,daemon=True);thread.start()
                try:
                    response=historian.ask(repo,report['question'],ai=True,mode=mode,use_cache=False)
                finally:stop.set();thread.join(timeout=5)
                elapsed=time.perf_counter()-tick;metrics=response.get('metrics',{})
                duration=metrics.get('eval_duration_ns') or 0
                allocation=[m.get('size_vram',0) for s in samples for m in s['models'] if m['name']==model['name']]
                ram=[s['hardware'].get('ram_available_bytes') for s in samples if s['hardware'].get('ram_available_bytes') is not None]
                row=dict(requested_mode=mode,phase=phase,wall_seconds=elapsed,cached=response.get('cached',False),
                    valid_uncached=response['backend']=='ollama' and not response.get('cached') and metrics.get('mode')==mode,
                    tokens_per_second=metrics.get('eval_count',0)*1e9/duration if duration else None,
                    peak_sampled_model_vram_bytes=max(allocation,default=None),min_observed_available_ram_bytes=min(ram,default=None),
                    metrics=metrics,samples=samples,response=response)
                row['accepted_interpretations']=len(response.get('interpretations',[]))
                report['runs'].append(row)
                # Write progress after every actual request so interruptions preserve measurements.
                (report_dir/'git-benchmarks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                print(mode,phase,'seconds',round(elapsed,3),'valid_uncached',row['valid_uncached'],flush=True)
                if not row['valid_uncached']:break
            historian.backend.unload(model['name'])
        # A separate production-cache measurement, never included in inference groups.
        seed=historian.ask(repo,report['question'],ai=True,mode='cpu')
        if seed.get('backend')=='ollama':
            for _ in range(10):
                tick=time.perf_counter();response=historian.ask(repo,report['question'],ai=True,mode='cpu')
                report['cached'].append(dict(wall_seconds=time.perf_counter()-tick,cache_hit=response.get('cached',False)))
                if not response.get('cached'):break
        historian.backend.unload(model['name'])
    report['groups']={mode:{phase:aggregate([s for s in report['runs'] if s['requested_mode']==mode and s['phase']==phase and s['valid_uncached']]) for phase in ('cold','warm')} for mode in ('cpu','gpu')}
    report['search_summary']=aggregate(report['search'])
    report['cache_summary']=aggregate([r for r in report['cached'] if r['cache_hit']])
    report['hardware_after']=hardware();store.close()
    (report_dir/'git-benchmarks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    lines=['# Repeated Git Intelligence benchmarks','',report['methodology'],'',
        '| Mode | Phase | n | Mean seconds | Median | Min | Max |','|---|---|---:|---:|---:|---:|---:|']
    for mode,groups in report['groups'].items():
        for phase,g in groups.items():
            if g['n']:lines.append(f"| {mode} | {phase} | {g['n']} | {g['mean_seconds']:.3f} | {g['median_seconds']:.3f} | {g['min_seconds']:.3f} | {g['max_seconds']:.3f} |")
    lines+=['','Search: '+json.dumps(report['search_summary']),'','Cached: '+json.dumps(report['cache_summary']),
        '', 'Full token rates, sampled model VRAM, available physical RAM, settings and responses are in git-benchmarks.json.',
        'VRAM peaks are sampled, not guaranteed transient peaks. RAM is system-wide available physical RAM, not model-only working set.',
        'Warm runs use comparable identical prompts and inference settings. Ollama may reuse its prompt/KV cache; answer caching is bypassed.',
        'The one cold sample per mode is descriptive only. Do not generalize the observed difference to other repositories or workloads.']
    (report_dir/'GIT_PERFORMANCE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Reports saved.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',default='xitui');p.add_argument('--runs',type=int,default=5);p.add_argument('--inference',action='store_true')
    p.add_argument('--question',default='How did scrollToRect change?')
    a=p.parse_args();run_benchmark(a.repo,a.runs,a.inference,a.question)

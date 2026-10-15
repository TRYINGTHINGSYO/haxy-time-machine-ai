import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from time_machine import clean
from .analyst import Analyst, citation, priorities
from .backend import Ollama, BackendError, hardware
from .store import Store, timestamp
from .sync import sync, code_history
from .git_intelligence import GitIndex,format_citation
from .historian import Historian

ROOT = Path(__file__).resolve().parent.parent


def dump(value):
    print(clean(json.dumps(value,ensure_ascii=False,indent=2)))


def performance_report(data_dir):
    local=Path(data_dir)/'benchmarks'/'git-benchmarks.json'
    path=local if local.exists() else ROOT/'git-benchmarks.json'
    return json.loads(path.read_text(encoding='utf-8'))


def open_app(data_dir):
    data_dir.mkdir(parents=True,exist_ok=True)
    store = Store(data_dir/'memory.sqlite3')
    if not store.repos():
        fixture = json.loads((ROOT/'events.json').read_text(encoding='utf-8'))
        store.import_rows('haxy',fixture['source'],fixture['events'])
    return store


def monitor(analyst):
    result = hardware()
    try:
        result['running_models'] = analyst.backend.running()
    except BackendError as exc:
        result['ollama_error'] = str(exc)
    result['last_inference'] = analyst.last_metrics
    result['session_cache_hit_rate'] = analyst.hits / max(1,analyst.calls)
    result['requests'] = analyst.calls
    return result


def dashboard(store, analyst, selected):
    mode = analyst.config.get('mode','cpu')
    use_ai = True
    gi=GitIndex(store);historian=Historian(store,analyst.data_dir,analyst.config)
    last_sources=[]
    while True:
        print('\n'+'='*76+'\n H A X Y   T I M E   M A C H I N E   A I         .     *      +\n'+'='*76)
        stats = store.overview(selected)
        print(clean(f" [{selected}] ISSUES {stats['issues']} | OPEN {stats['open']} | CLOSED {stats['closed']} | DISCUSS {stats['discussions']} | EVENTS {stats['events']}"))
        if store.meta(selected)['source'].startswith('synthetic:'):
            print(' SYNTHETIC DEMO: fictional reports and test hashes; index a local project for real history.')
        print(clean(f" AI {'enabled on demand' if use_ai else 'off'} | {mode} | model {analyst.config.get('model','auto')} | cache {analyst.hits}/{analyst.calls}"))
        if store.db.execute('SELECT 1 FROM git_repos WHERE name=?',(selected,)).fetchone():
            commits=gi.commits(selected)
            print(clean(f" GIT INTELLIGENCE: {len(commits)} indexed commits | tip {gi.registered(selected)['tip'][:12]}"))
            for c in commits[:2]:print(clean(' '+c['sha'][:12]+' '+c['message'].splitlines()[0]))
        print(' RECENT:')
        for r in store.rows(selected)[-3:]:
            e=r['event']; data=e['data'] or {}
            print(clean(f" {r['commit'][:8]} {e['kind']}: {data.get('title',data.get('body','removed'))[:90]}"))
        print('-'*76)
        print('ask QUESTION | search WORDS | rank | timeline | time YYYY-MM-DD')
        print('diff DATE DATE | repos | use NAME | sync NAME HTTPS_URL | monitor')
        print('mode cpu/auto/gpu/deterministic | model NAME/auto | ai on/off | quit')
        print('GIT INTELLIGENCE: git-add NAME PATH | git-search QUERY | git-ask QUERY')
        print('git-ai QUERY | git-show SHA | git-compare SHA SHA | git-timeline | cite N')
        print('git-performance | monitor')
        try:
            line=input('observatory> ').strip()
            command,_,arg=line.partition(' ')
            if command in ('quit','q','exit'): break
            if command=='git-add':
                name,local=arg.split(' ',1);dump(gi.index(name,local.strip('"')));selected=name
            elif command in ('git-ask','git-ai'):
                result=historian.ask(selected,arg,ai=command=='git-ai' and use_ai and mode!='deterministic',mode='cpu' if mode=='deterministic' else mode)
                dump(result);last_sources=result['sources']
                for number,source in enumerate(last_sources,1):print(clean(f"[{number}] {source['label']}"))
            elif command=='cite':
                number=int(arg)
                if not 1<=number<=len(last_sources):raise ValueError('Citation number is not in the last answer')
                dump(gi.open_evidence(selected,last_sources[number-1]['id']))
            elif command=='git-search':
                rows=gi.retrieve(selected,arg);dump(rows);last_sources=[format_citation(e) for e in rows]
                for number,source in enumerate(last_sources,1):print(clean(f"[{number}] {source['label']}"))
            elif command=='git-show':dump(dict(commit=gi.commit(selected,arg),changes=gi.changes(selected,arg)))
            elif command=='git-compare':
                a,b=arg.split();dump(gi.compare(selected,a,b))
            elif command=='git-timeline':dump(gi.timeline(selected))
            elif command=='git-performance':
                report=performance_report(analyst.data_dir)
                dump({k:report.get(k) for k in ('configuration','groups','search_summary','cache_summary','hardware_after')})
            elif command=='ask': dump(analyst.ask(selected,arg,mode,use_ai))
            elif command=='search': dump(store.search(selected,arg))
            elif command=='rank': dump(priorities(store,selected))
            elif command=='timeline':
                for r in store.rows(selected): print(clean(f"{r['timestamp']} [{citation(r)}] {r['event']['kind']} {str(r['event']['data'])[:160]}"))
            elif command=='time': dump(dict(overview=store.overview(selected,timestamp(arg,True)),state=store.state(selected,timestamp(arg,True))))
            elif command=='diff':
                a,b=arg.split(); dump(store.compare(selected,timestamp(a,True),timestamp(b,True)))
            elif command=='repos': dump(store.repos())
            elif command=='use': store.meta(arg); selected=arg; last_sources=[]
            elif command=='sync':
                name,source=arg.split(' ',1); dump(sync(store,name,source,analyst.data_dir)); selected=name
            elif command=='monitor':
                metrics=monitor(analyst)
                metrics['git_historian']=dict(requests=historian.calls,cache_hits=historian.hits,
                    cache_hit_rate=historian.hits/max(1,historian.calls),last_inference=historian.last_metrics)
                dump(metrics)
            elif command=='mode' and arg in ('cpu','auto','gpu','deterministic'): mode=arg
            elif command=='model': analyst.config['model']=arg
            elif command=='ai' and arg in ('on','off'): use_ai=arg=='on'
            elif line: print('Unknown command. Use one of the commands above.')
        except (ValueError,KeyError,OSError,BackendError,subprocess.SubprocessError) as exc:
            print(clean('Error: '+str(exc)))


def main():
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description='Local Haxy repository analyst. No cloud inference or automatic downloads.')
    p.add_argument('--data',type=Path,default=ROOT/'data')
    p.add_argument('--config',type=Path,default=ROOT/'config.json')
    p.add_argument('--repo',default='haxy')
    p.add_argument('--mode',choices=['cpu','auto','gpu','deterministic'])
    p.add_argument('--model')
    sub=p.add_subparsers(dest='command')
    sub.add_parser('dashboard');sub.add_parser('overview');sub.add_parser('doctor');sub.add_parser('rank');sub.add_parser('repos')
    ask=sub.add_parser('ask');ask.add_argument('question')
    search=sub.add_parser('search');search.add_argument('query')
    imp=sub.add_parser('import');imp.add_argument('name');imp.add_argument('snapshot',type=Path)
    sy=sub.add_parser('sync');sy.add_argument('name');sy.add_argument('source')
    ci=sub.add_parser('index-commits');ci.add_argument('path');ci.add_argument('--ref',default='HEAD');ci.add_argument('--limit',type=int,default=200)
    tm=sub.add_parser('time');tm.add_argument('date')
    diff=sub.add_parser('diff');diff.add_argument('start');diff.add_argument('end')
    sub.add_parser('timeline')
    gx=sub.add_parser('git-index');gx.add_argument('path');gx.add_argument('--ref',default='HEAD');gx.add_argument('--limit',type=int,default=200)
    ga=sub.add_parser('git-ask');ga.add_argument('question');ga.add_argument('--ai',action='store_true')
    ga.add_argument('--path');ga.add_argument('--author');ga.add_argument('--since');ga.add_argument('--until')
    gs=sub.add_parser('git-search');gs.add_argument('query');gs.add_argument('--path');gs.add_argument('--author')
    show=sub.add_parser('git-show');show.add_argument('sha')
    op=sub.add_parser('git-open');op.add_argument('citation_id')
    gc=sub.add_parser('git-compare');gc.add_argument('left');gc.add_argument('right')
    sub.add_parser('git-timeline')
    sub.add_parser('git-performance')
    args=p.parse_args()
    store=None
    try:
        config=json.loads(args.config.read_text(encoding='utf-8'))
        if args.mode: config['mode']=args.mode
        if args.model: config['model']=args.model
        store=open_app(args.data.resolve())
        analyst=Analyst(store,args.data.resolve(),config)
        gi=GitIndex(store)
        if args.command in (None,'dashboard'):
            dashboard(store,analyst,args.repo)
        elif args.command=='overview':dump(store.overview(args.repo))
        elif args.command=='doctor':
            result=monitor(analyst)
            try:result['installed_models']=analyst.backend.models();result['selected_model']=analyst.backend.select()
            except BackendError as exc:result['ollama_error']=str(exc)
            dump(result)
        elif args.command=='repos':dump(store.repos())
        elif args.command=='ask':dump(analyst.ask(args.repo,args.question,config['mode']))
        elif args.command=='search':dump(store.search(args.repo,args.query))
        elif args.command=='rank':dump(priorities(store,args.repo))
        elif args.command=='import':
            fixture=json.loads(args.snapshot.read_text(encoding='utf-8'))
            dump(store.import_rows(args.name,fixture.get('source','offline snapshot'),fixture['events']))
        elif args.command=='sync':dump(sync(store,args.name,args.source,args.data.resolve()))
        elif args.command=='index-commits':dump(store.index_commits(args.repo,code_history(args.path,args.ref,args.limit)))
        elif args.command=='time':dump(dict(overview=store.overview(args.repo,timestamp(args.date,True)),state=store.state(args.repo,timestamp(args.date,True))))
        elif args.command=='diff':dump(store.compare(args.repo,timestamp(args.start,True),timestamp(args.end,True)))
        elif args.command=='timeline':dump(store.rows(args.repo))
        elif args.command=='git-index':dump(gi.index(args.repo,args.path,args.ref,args.limit))
        elif args.command=='git-search':dump(gi.retrieve(args.repo,args.query,path=args.path,author=args.author))
        elif args.command=='git-ask':dump(Historian(store,args.data.resolve(),config).ask(args.repo,args.question,
            ai=args.ai and config['mode']!='deterministic',mode='cpu' if config['mode']=='deterministic' else config['mode'],
            path=args.path,author=args.author,start=timestamp(args.since),end=timestamp(args.until,True)))
        elif args.command=='git-show':dump(dict(commit=gi.commit(args.repo,args.sha),changes=gi.changes(args.repo,args.sha)))
        elif args.command=='git-open':dump(gi.open_evidence(args.repo,args.citation_id))
        elif args.command=='git-compare':dump(gi.compare(args.repo,args.left,args.right))
        elif args.command=='git-timeline':dump(gi.timeline(args.repo))
        elif args.command=='git-performance':
            report=performance_report(args.data)
            dump({k:report.get(k) for k in ('configuration','groups','search_summary','cache_summary','hardware_after')})
    except (ValueError,KeyError,OSError,BackendError,subprocess.SubprocessError) as exc:
        p.exit(1,clean('Error: '+str(exc))+'\n')
    except (KeyboardInterrupt,EOFError): print('\nObservatory closed.')
    finally:
        if store:store.close()


if __name__=='__main__':main()

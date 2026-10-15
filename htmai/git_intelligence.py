"""Bounded, incremental, read-only Git indexing and offline code citations."""
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from .store import digest, timestamp

MAX_OUTPUT = 2 * 1024 * 1024
VERSION = 3


def run(path, *args, limit=MAX_OUTPUT):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never', GIT_NO_REPLACE_OBJECTS='1', GIT_OPTIONAL_LOCKS='0',GIT_NO_LAZY_FETCH='1')
    cmd = ['git','--no-pager','-c','core.hooksPath=NUL','-c','credential.helper=',
        '-c','core.fsmonitor=false','-c','diff.external=','-C',str(path),*args]
    # Spool to a temporary file: a giant diff cannot grow Python's memory without bound.
    import tempfile
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        p = subprocess.Popen(cmd,stdout=output,stderr=errors,env=env)
        try: p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill();p.wait();raise
        if p.returncode:
            errors.seek(0)
            raise ValueError('Git command failed: '+errors.read(2000).decode('utf-8','replace'))
        output.seek(0);raw=output.read(limit+1)
    if len(raw)>limit:
        raise ValueError('Git output exceeds the configured per-command bound')
    return raw


def resolve(path, ref):
    return run(path,'rev-parse','--verify','--end-of-options',ref+'^{commit}',limit=200).decode().strip()


def migrate(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS schema_versions(component TEXT PRIMARY KEY,version INTEGER);
    CREATE TABLE IF NOT EXISTS git_repos(name TEXT PRIMARY KEY,path TEXT,tip TEXT,generation TEXT,options TEXT);
    CREATE TABLE IF NOT EXISTS git_commits(repo TEXT,sha TEXT,body TEXT,PRIMARY KEY(repo,sha));
    CREATE TABLE IF NOT EXISTS git_reach(repo TEXT,generation TEXT,ord INTEGER,sha TEXT,PRIMARY KEY(repo,generation,ord));
    CREATE TABLE IF NOT EXISTS git_changes(repo TEXT,sha TEXT,parent_index INTEGER,ordinal INTEGER,body TEXT,
      PRIMARY KEY(repo,sha,parent_index,ordinal));
    CREATE TABLE IF NOT EXISTS git_evidence(id TEXT PRIMARY KEY,repo TEXT,sha TEXT,parent_index INTEGER,
      path TEXT,snapshot_sha TEXT,side TEXT,start INTEGER,end INTEGER,text TEXT);
    CREATE INDEX IF NOT EXISTS git_evidence_repo ON git_evidence(repo,sha);
    CREATE VIRTUAL TABLE IF NOT EXISTS git_fts USING fts5(id UNINDEXED,repo UNINDEXED,text);
    INSERT OR REPLACE INTO schema_versions VALUES('git_intelligence',2);
    ''')


def paths(raw):
    parts=raw.split(b'\0');i=0;result=[]
    while i<len(parts) and parts[i]:
        status=parts[i].decode('ascii');i+=1
        first=parts[i].decode('utf-8','surrogateescape');i+=1
        old=first
        if status[0] in ('R','C'):
            new=parts[i].decode('utf-8','surrogateescape');i+=1
        else:new=first
        # Invalid UTF-8 paths are explicitly unsupported by SQLite text storage.
        try:old.encode('utf-8');new.encode('utf-8')
        except UnicodeEncodeError:raise ValueError('Non-UTF-8 Git path is unsupported')
        result.append(dict(status=status,path=new,old_path=old))
    return result


def parse_diff(patch):
    """Resolve old/new line numbers directly from unified Git hunk headers."""
    hunks=[];current=None;old=new=0
    for line in patch.splitlines():
        match=re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)',line)
        if match:
            old,new=int(match[1]),int(match[3])
            current=dict(old_start=old,new_start=new,old_count=int(match[2] or 1),
                new_count=int(match[4] or 1),heading=match[5].strip(),lines=[])
            hunks.append(current);continue
        if current is None:continue
        if line.startswith('\\ No newline'):continue
        if line.startswith('+'):
            current['lines'].append(dict(kind='added',old=None,new=new,text=line[1:]));new+=1
        elif line.startswith('-'):
            current['lines'].append(dict(kind='deleted',old=old,new=None,text=line[1:]));old+=1
        elif line.startswith(' '):
            current['lines'].append(dict(kind='snapshot',old=old,new=new,text=line[1:]));old+=1;new+=1
    for h in hunks:
        if sum(l['old'] is not None for l in h['lines'])!=h['old_count'] or sum(l['new'] is not None for l in h['lines'])!=h['new_count']:
            raise ValueError('Diff hunk line counts do not match its header')
    return hunks


def segments(hunks):
    for h in hunks:
        group=[]
        for line in h['lines']:
            if group and (line['kind']!=group[-1]['kind'] or len(group)>=16):
                yield group;group=[]
            group.append(line)
        if group:yield group


class GitIndex:
    def __init__(self,store):
        self.store,self.db=store,store.db
        migrate(self.db)

    def registered(self,repo):
        row=self.db.execute('SELECT * FROM git_repos WHERE name=?',(repo,)).fetchone()
        if not row:raise ValueError('No ordinary Git index for '+repo+'; use git-index first')
        return dict(row)

    def index(self,repo,path,ref='HEAD',limit=200,max_files=100,max_patch=256*1024):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',repo) or repo in ('.','..'):raise ValueError('Invalid repository name')
        path=str(Path(path).resolve());tip=resolve(path,ref)
        if not 1<=limit<=100000:raise ValueError('Commit limit must be 1..100000')
        old=self.db.execute('SELECT * FROM git_repos WHERE name=?',(repo,)).fetchone()
        if old and Path(old['path'])!=Path(path):raise ValueError('This repository name already identifies another local path')
        shallow=run(path,'rev-parse','--is-shallow-repository').decode().strip()=='true'
        shas=run(path,'rev-list','--topo-order','--max-count='+str(limit+1),tip,limit=8*1024*1024).decode().splitlines()
        limited=len(shas)>limit;shas=shas[:limit]
        options=dict(version=VERSION,max_files=max_files,max_patch=max_patch,shallow=shallow,limited=limited)
        generation=digest(dict(tip=tip,commits=shas,options=options))
        added=0;warnings=[]
        # Per-commit transactions permit restart after an interruption. Active generation changes only on success.
        for sha in reversed(shas):
            existing=self.db.execute('SELECT body FROM git_commits WHERE repo=? AND sha=?',(repo,sha)).fetchone()
            if existing and json.loads(existing[0]).get('index_options')==options:continue
            raw=run(path,'cat-file','commit',sha,limit=512*1024).decode('utf-8','replace')
            headers,message=raw.split('\n\n',1)
            parents=re.findall(r'^parent ([0-9a-f]+)$',headers,re.M)
            author=re.search(r'^author (.*) <(.*)> (\d+) ([+-]\d+)$',headers,re.M)
            commit=dict(sha=sha,parents=parents,author=author[1] if author else '',
                timestamp=int(author[3]) if author else 0,message=message.rstrip(),warnings=[],index_options=options)
            comparisons=list(enumerate(parents)) if parents else [(0,None)]
            changes=[];evidence=[]
            for pi,parent in comparisons:
                try:
                    if parent:run(path,'cat-file','-e',parent+'^{commit}',limit=20)
                    args=['diff-tree','--no-commit-id','-r','-M','--name-status','-z']
                    args+=([parent,sha] if parent else ['--root',sha])
                    found=paths(run(path,*args))
                except ValueError:
                    commit['warnings'].append('Parent unavailable: '+str(parent)+' (shallow boundary or missing object); no invented root diff')
                    continue
                if len(found)>max_files:commit['warnings'].append('File limit reached: '+str(len(found))+' files, indexed '+str(max_files))
                for ordinal,change in enumerate(found[:max_files]):
                    change.update(parent_index=pi,parent=parent,hunks=[],patch='',warnings=[])
                    literal=[':(literal)'+p for p in dict.fromkeys([change['old_path'],change['path']])]
                    args=['diff','--no-ext-diff','--no-textconv','--no-color','--submodule=short','--unified=3','-M']
                    if parent:args += [parent,sha,'--',*literal]
                    else:args=['show','--format=','--root','--no-ext-diff','--no-textconv','--no-color','--submodule=short','--unified=3','-M',sha,'--',*literal]
                    try:
                        patch=run(path,*args,limit=max_patch).decode('utf-8','strict')
                        change['binary']='Binary files ' in patch or 'GIT binary patch' in patch
                        change['submodule']='Subproject commit ' in patch or 'mode 160000' in patch
                        change['patch']=patch
                        if change['binary']:change['warnings'].append('Binary change; no code line citations')
                        elif change['submodule']:change['warnings'].append('Submodule pointer change; submodule contents are not indexed')
                        else:change['hunks']=parse_diff(patch)
                        if not change['binary'] and not change['submodule'] and not change['hunks'] and change['status'].startswith(('R','C')):
                            snapshot=run(path,'show',sha+':'+change['path'],limit=max_patch).decode('utf-8','strict').splitlines()[:16]
                            if snapshot:
                                change['hunks']=[dict(old_start=1,new_start=1,old_count=len(snapshot),new_count=len(snapshot),heading='Historical snapshot after rename/copy',
                                    lines=[dict(kind='snapshot',old=i+1,new=i+1,text=t) for i,t in enumerate(snapshot)])]
                    except (ValueError,UnicodeDecodeError) as exc:
                        change['warnings'].append('Patch not indexed: '+str(exc))
                    for h in change['hunks']:
                        for line in h['lines']:line['scope']=h['heading']
                    for group in segments(change['hunks']):
                        side=group[0]['kind'];deleted=side=='deleted'
                        snapshot=parent if deleted else sha
                        numbers=[l['old'] if deleted else l['new'] for l in group]
                        text='\n'.join(l['text'] for l in group)
                        ep=change['old_path'] if deleted else change['path']
                        eid=digest([repo,sha,pi,ep,side,numbers,text])
                        evidence.append(dict(id=eid,repo=repo,sha=sha,parent_index=pi,path=ep,
                            snapshot_sha=snapshot,side=side,start=numbers[0],end=numbers[-1],text=text,scope=group[0].get('scope','')))
                    changes.append(change)
                    warnings.extend(sha+': '+change['path']+': '+w for w in change['warnings'])
            with self.db:
                old_ids=[r[0] for r in self.db.execute('SELECT id FROM git_evidence WHERE repo=? AND sha=?',(repo,sha))]
                self.db.executemany('DELETE FROM git_fts WHERE id=?',[(i,) for i in old_ids]+[('commit:'+repo+':'+sha,)])
                self.db.execute('DELETE FROM git_changes WHERE repo=? AND sha=?',(repo,sha))
                self.db.execute('DELETE FROM git_evidence WHERE repo=? AND sha=?',(repo,sha))
                self.db.execute('INSERT OR REPLACE INTO git_commits VALUES(?,?,?)',(repo,sha,json.dumps(commit)))
                self.db.execute('INSERT INTO git_fts VALUES(?,?,?)',('commit:'+repo+':'+sha,repo,message))
                for i,c in enumerate(changes):
                    self.db.execute('INSERT INTO git_changes VALUES(?,?,?,?,?)',(repo,sha,c['parent_index'],i,json.dumps(c)))
                for e in evidence:
                    self.db.execute('INSERT INTO git_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',tuple(e[k] for k in ('id','repo','sha','parent_index','path','snapshot_sha','side','start','end','text')))
                    self.db.execute('INSERT INTO git_fts VALUES(?,?,?)',(e['id'],repo,e['path']+' '+e['scope']+' '+e['text']))
            warnings.extend(commit['warnings']);added+=1
        with self.db:
            if not self.db.execute('SELECT 1 FROM repos WHERE name=?',(repo,)).fetchone():
                self.db.execute('INSERT INTO repos VALUES(?,?,?)',(repo,path,digest([])))
            self.db.executemany('INSERT OR REPLACE INTO git_reach VALUES(?,?,?,?)',[(repo,generation,i,s) for i,s in enumerate(shas)])
            self.db.execute('INSERT OR REPLACE INTO git_repos VALUES(?,?,?,?,?)',(repo,path,tip,generation,json.dumps(options)))
        return dict(repository=repo,indexed=added,reused=len(shas)-added,commits=len(shas),tip=tip,
            shallow=shallow,limit_reached=limited,warnings=warnings,
            merge_policy='Separate diff against every available parent; never flatten a merge into a linear change')

    def commits(self,repo):
        meta=self.registered(repo)
        return [json.loads(r[0]) for r in self.db.execute('''SELECT c.body FROM git_reach r JOIN git_commits c
          ON r.repo=c.repo AND r.sha=c.sha WHERE r.repo=? AND r.generation=? ORDER BY r.ord''',(repo,meta['generation']))]

    def commit(self,repo,ref):
        matches=[c for c in self.commits(repo) if c['sha'].startswith(ref)]
        if len(matches)!=1:raise ValueError('Commit prefix must identify exactly one indexed commit')
        return matches[0]

    def changes(self,repo,ref):
        sha=self.commit(repo,ref)['sha']
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM git_changes WHERE repo=? AND sha=? ORDER BY parent_index,ordinal',(repo,sha))]

    def evidence(self,repo,eid):
        row=self.db.execute('SELECT * FROM git_evidence WHERE id=? AND repo=?',(eid,repo)).fetchone()
        if not row:raise ValueError('Unknown historical citation')
        return dict(row)

    def open_evidence(self,repo,eid):
        e=self.evidence(repo,eid)
        changes=self.db.execute('SELECT body FROM git_changes WHERE repo=? AND sha=? AND parent_index=?',(repo,e['sha'],e['parent_index']))
        patch=next((json.loads(r[0])['patch'] for r in changes if e['path'] in (json.loads(r[0])['path'],json.loads(r[0])['old_path'])),'')
        return dict(citation=format_citation(e),evidence=e,original_diff=patch,
            note='Deleted lines resolve against parent snapshot; added/context lines against change snapshot. Stored offline diff is authoritative.')

    def retrieve(self,repo,query,limit=8,path=None,author=None,start=None,end=None):
        commits=self.commits(repo);active={c['sha']:c for c in commits}
        for key,pattern in [('path',r'file:([^\s]+)'),('author',r'author:"([^"]+)"|author:([^\s]+)')]:
            m=re.search(pattern,query)
            if m:
                if key=='path':path=m[1]
                else:author=next(g for g in m.groups() if g)
                query=query.replace(m[0],'')
        dates=re.findall(r'\b\d{4}-\d{2}-\d{2}\b',query)
        if dates:start=start or timestamp(dates[0]);end=end or timestamp(dates[-1],True)
        stop={'the','this','that','why','how','what','when','which','was','were','has','have','did','does','and','for','with','from','into','before','after','between','function','modified','changed','changes','change','code','project','module','evolve','evolved','explain','history','commit','commits','feature','probably','introduced','behavior','regression'}
        tokens=[t for t in re.findall(r'[A-Za-z_][A-Za-z_0-9]*',query.lower()) if len(t)>1 and t not in stop][:30]
        hits={}
        if tokens:
            expr=' OR '.join('"'+t+'"' for t in tokens)
            for i,r in enumerate(self.db.execute('SELECT id FROM git_fts WHERE git_fts MATCH ? AND repo=? ORDER BY bm25(git_fts) LIMIT 300',(expr,repo))):hits[r[0]]=max(1,20-i*.03)
        ranked=[]
        seeds={c['sha'] for c in commits if 'commit:'+repo+':'+c['sha'] in hits}
        for row in self.db.execute('SELECT sha,id FROM git_evidence WHERE repo=?',(repo,)):
            if row['id'] in hits:seeds.add(row['sha'])
        neighbors={p for c in commits if c['sha'] in seeds for p in c['parents']}
        neighbors.update(c['sha'] for c in commits if set(c['parents']) & seeds)
        # Candidate commits are bounded by the indexed window; patches remain in SQLite.
        explicit=[c['sha'] for c in commits if any(c['sha'].startswith(s) for s in re.findall(r'\b[0-9a-f]{8,64}\b',query))]
        for c in commits:
            sha=c['sha']
            if author and author.casefold() not in c['author'].casefold():continue
            if start is not None and c['timestamp']<start:continue
            if end is not None and c['timestamp']>end:continue
            msgscore=hits.get('commit:'+repo+':'+sha,0)*1.5
            es=[dict(r) for r in self.db.execute('SELECT * FROM git_evidence WHERE repo=? AND sha=?',(repo,sha))]
            if path:es=[e for e in es if path.casefold() in e['path'].casefold()]
            for e in es:
                score=hits.get(e['id'],0)+msgscore
                if path:score+=20
                score+=sum(4 for t in tokens if t in e['path'].lower())
                if sha in explicit:score+=50
                if sha in neighbors:score+=1
                if score and e['side'] in ('added','deleted'):score+=2
                if not tokens and (not path or es):score+=1
                if score:ranked.append((score,e,c))
        ranked.sort(key=lambda r:(-r[0],-r[2]['timestamp'],r[1]['id']))
        result=[];seen=set();per_commit=collections.Counter()
        evolution=any(t in query.lower() for t in ('evolv','history','contributed','feature'))
        for score,e,c in ranked:
            # Exact sources are unique, even when different retrieval signals select them.
            key=(e['sha'],e['path'],e['side'],e['start'],e['end'],e['parent_index'])
            if key in seen:continue
            if evolution and per_commit[e['sha']]>=2:continue
            seen.add(key);e['commit_message']=c['message'];e['author']=c['author'];e['timestamp']=c['timestamp'];e['score']=round(score,3)
            result.append(e);per_commit[e['sha']]+=1
            if len(result)>=limit:break
        return result

    def timeline(self,repo):
        files=collections.Counter();pairs=collections.Counter();clusters=collections.defaultdict(list);pair_refs=collections.defaultdict(list)
        for c in self.commits(repo):
            paths_changed={r['path'] for r in self.changes(repo,c['sha'])}
            files.update(paths_changed)
            # Avoid quadratic work on bulk vendor commits; report that sampling rule.
            import itertools
            pairs.update(itertools.combinations(sorted(paths_changed)[:50],2))
            for pair in itertools.combinations(sorted(paths_changed)[:50],2):pair_refs[pair].append(c['sha'])
            import datetime as dt
            day=dt.datetime.fromtimestamp(c['timestamp'],dt.timezone.utc).strftime('%Y-%m-%d')
            clusters[day].append(c['sha'])
        return dict(commits=len(self.commits(repo)),contributors=len({c['author'] for c in self.commits(repo)}),
            frequent_files=files.most_common(20),cochanges=[dict(files=list(p),count=n,commits=pair_refs[p]) for p,n in pairs.most_common(20)],
            clusters=dict(sorted(clusters.items())),cochange_policy='Each commit counted once, merge paths unioned; at most first 50 paths per commit')

    def compare(self,repo,left,right):
        a,b=self.commit(repo,left),self.commit(repo,right)
        path=self.registered(repo)['path']
        raw=run(path,'diff','--no-ext-diff','--no-textconv','--no-color','--unified=3','-M',a['sha'],b['sha'])
        return dict(from_sha=a['sha'],to_sha=b['sha'],diff=raw.decode('utf-8','replace'),
            note='Direct endpoint diff; old lines use from_sha, new lines use to_sha. Requires the registered local Git objects.')


def format_citation(e):
    return dict(id=e['id'],commit=e['sha'],path=e['path'],lines=[e['start'],e['end']],
        evidence_kind=e['side'],snapshot_commit=e['snapshot_sha'],parent_index=e['parent_index'],
        label=f"{e['path']} @ {e['sha']} : lines {e['start']}-{e['end']} ({e['side']}; snapshot {e['snapshot_sha']})")

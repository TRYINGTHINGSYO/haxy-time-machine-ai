import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit


def git(path, *args):
    # Repository code, hooks, external diff drivers and submodules are never executed.
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never')
    return subprocess.check_output(['git', '-c', 'core.hooksPath=NUL', '-c', 'credential.helper=', '-c', 'protocol.ext.allow=never',
        '-C', str(path), *args], stderr=subprocess.PIPE, timeout=60,env=env).decode('utf-8')


def read_history(path, ref):
    oid = git(path, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').strip()
    # Refuse divergent event DAGs: last-write replay cannot emulate Haxy's merge semantics.
    if git(path, 'rev-list', '--min-parents=2', oid).strip():
        raise ValueError('Merged event histories require Haxy conflict processing; previous data preserved')
    rows = []
    raw=git(path,'log','--reverse','--topo-order','--format=%x1e%H%x00%ct%x00%an%x00%B',oid)
    for block in raw.split('\x1e')[1:]:
        commit, timestamp, author, body = block.split('\x00', 3)
        try:
            event = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError('Malformed JSON in event commit ' + commit) from exc
        rows.append(dict(commit=commit, timestamp=int(timestamp), author=author, event=event))
    return rows


def code_history(path, ref='HEAD', limit=200):
    oid=git(path,'rev-parse','--verify','--end-of-options',ref+'^{commit}').strip()
    rows=[]
    for block in git(path,'log','--topo-order','--max-count='+str(max(1,min(2000,limit))),
        '--format=%x1e%H%x00%ct%x00%an%x00%B',oid).split('\x1e')[1:]:
        commit,ts,author,body=block.split('\x00',3)
        rows.append(dict(commit=commit,timestamp=int(ts),author=author,
            event=dict(id=commit[:32],kind='commit',data=dict(message=body.strip()))))
    return rows


def sync(store, repo, source, data_dir):
    url = urlsplit(source)
    if Path(source).is_dir():
        source=str(Path(source).resolve())
    elif url.scheme:
        if url.scheme != 'https' or url.username or url.password or not url.hostname:
            raise ValueError('Remote sync accepts credential-free HTTPS public URLs only')
    else:
        raise ValueError('Source must be a public HTTPS Git URL or an existing local repository')
    if not repo or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in repo) or repo in ('.','..'):
        raise ValueError('Invalid repository name')
    cache = Path(data_dir).resolve() / 'git' / (repo + '.git')
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / 'HEAD').exists():
        git(cache, 'init', '--bare')
    # Fetch into a dedicated cache, never the user's checkout; forced updates are retained in SQLite generations.
    git(cache, 'fetch', '--no-tags', '--no-recurse-submodules', '--', source,
        '+refs/heads/haxy/events:refs/heads/haxy/events')
    rows = read_history(cache, 'refs/heads/haxy/events')
    try:
        previous_ids={r['commit'] for r in store.rows(repo)}
    except ValueError:
        previous_ids=set()
    result = store.import_rows(repo, source, rows)
    result['changes'] = [dict(commit=r['commit'], event=r['event']) for r in rows if r['commit'] not in previous_ids][-20:]
    return result

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def evidence_commit(row):
    return json.dumps(row['event']['data'],ensure_ascii=False)


def validate(row):
    if not isinstance(row,dict) or not {'event','commit','timestamp'} <= row.keys():
        raise ValueError('Event record must contain event, commit and timestamp')
    e = row['event']
    if not isinstance(e,dict) or not {'id','kind','data'} <= e.keys():
        raise ValueError('Event must contain id, kind and data')
    if not isinstance(row['commit'],str) or not re.fullmatch(r'[0-9a-f]{40,64}', row['commit']):
        raise ValueError('Invalid commit ID')
    if not isinstance(row['timestamp'], int) or row['timestamp'] < 0:
        raise ValueError('Invalid timestamp')
    if not isinstance(e['id'],str) or not re.fullmatch(r'[0-9a-f]{32}', e['id']):
        raise ValueError('Invalid event ID')
    if e['kind'] not in ('issue', 'discuss', 'comment', 'attach', 'assign', 'patch', 'patchrev', 'user', 'repo', 'fork', 'grant', 'notify'):
        raise ValueError('Unknown event kind')
    if e['data'] is not None and not isinstance(e['data'], dict):
        raise ValueError('Event payload must be an object or null')
    if e['data'] is not None:
        for field in ('title','description','labels','status','body','thread_id','parent_id','name'):
            if field in e['data'] and not isinstance(e['data'][field],str):
                raise ValueError('Invalid event text field: '+field)
        if e['kind']=='issue' and e['data'].get('status','open') not in ('open','closed'):
            raise ValueError('Unsupported issue status')
    if len(json.dumps(e).encode()) > 100 * 1024:
        raise ValueError('Oversized event')


def reconstruct(rows):
    state = {}
    for r in rows:
        e = r['event']
        key = (e['kind'], e['id'])
        if e['data'] is None:
            state.pop(key, None)
        else:
            state[key] = r
    return list(state.values())


def timestamp(value, end=False):
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    if len(value) == 10 and end:
        parsed += dt.timedelta(days=1, microseconds=-1)
    return int(parsed.timestamp())


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS repos(name TEXT PRIMARY KEY, source TEXT, revision TEXT);
        CREATE TABLE IF NOT EXISTS events(repo TEXT, oid TEXT, timestamp INTEGER, body TEXT,
          author TEXT DEFAULT '', PRIMARY KEY(repo,oid));
        CREATE TABLE IF NOT EXISTS histories(repo TEXT, revision TEXT, ord INTEGER, oid TEXT,
          PRIMARY KEY(repo,revision,ord));
        CREATE TABLE IF NOT EXISTS snapshots(repo TEXT, revision TEXT, position INTEGER, state TEXT,
          PRIMARY KEY(repo,revision,position));
        CREATE TABLE IF NOT EXISTS states(repo TEXT, kind TEXT, id TEXT, body TEXT,
          PRIMARY KEY(repo,kind,id));
        CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, repo TEXT, answer TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS commits(repo TEXT, oid TEXT, body TEXT, PRIMARY KEY(repo,oid));
        CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(repo UNINDEXED, oid UNINDEXED, text);
        ''')
        from .git_intelligence import migrate
        migrate(self.db)

    def close(self):
        self.db.close()

    def repos(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM repos ORDER BY name')]

    def meta(self, repo):
        row = self.db.execute('SELECT * FROM repos WHERE name=?', (repo,)).fetchone()
        if not row:
            raise ValueError('Unknown repository: ' + repo)
        return dict(row)

    def import_rows(self, repo, source, rows):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', repo):
            raise ValueError('Repository name must contain only letters, numbers, dots, underscores or hyphens')
        seen = set()
        unique = []
        for row in rows:
            validate(row)
            if row['commit'] not in seen:
                seen.add(row['commit'])
                unique.append(row)
        rows = unique
        revision = digest([r['commit'] for r in rows])
        old = self.db.execute('SELECT revision FROM repos WHERE name=?', (repo,)).fetchone()
        previous = self.rows(repo) if old else []
        previous_ids = {r['commit'] for r in previous}
        if old and old[0] == revision:
            with self.db:
                for r in rows:
                    if r.get('author'):
                        self.db.execute("UPDATE events SET author=? WHERE repo=? AND oid=? AND author=''",(r['author'],repo,r['commit']))
            return dict(new=0, total=len(rows), rewritten=False, unchanged=True)
        # All validation happens before the transaction. Failure preserves the old board.
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO repos VALUES(?,?,?)', (repo, source, revision))
            self.db.execute('DELETE FROM search WHERE repo=?', (repo,))
            for existing in self.db.execute('SELECT oid,body FROM commits WHERE repo=?',(repo,)).fetchall():
                self.db.execute('INSERT INTO search VALUES(?,?,?)',(repo,existing['oid'],evidence_commit(json.loads(existing['body']))))
            self.db.execute('DELETE FROM states WHERE repo=?', (repo,))
            for i, row in enumerate(rows):
                e = row['event']
                self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)',
                    (repo, row['commit'], row['timestamp'], json.dumps(e), row.get('author', '')))
                self.db.execute('INSERT OR REPLACE INTO histories VALUES(?,?,?,?)', (repo, revision, i, row['commit']))
                text = json.dumps(e['data'], ensure_ascii=False)
                self.db.execute('INSERT INTO search VALUES(?,?,?)', (repo, row['commit'], text))
                # Checkpoint every 100 events; all previous generations remain available.
                if (i + 1) % 100 == 0:
                    self.db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?)',
                        (repo, revision, i + 1, json.dumps(reconstruct(rows[:i + 1]))))
            state = reconstruct(rows)
            self.db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?)',
                (repo, revision, len(rows), json.dumps(state)))
            for row in state:
                e = row['event']
                self.db.execute('INSERT INTO states VALUES(?,?,?,?)', (repo, e['kind'], e['id'], json.dumps(row)))
        return dict(new=len(seen - previous_ids), total=len(rows),
            rewritten=bool(previous_ids - seen), unchanged=False)

    def rows(self, repo, revision=None, until=None):
        revision = revision or self.meta(repo)['revision']
        rows = [dict(commit=r['oid'], timestamp=r['timestamp'], author=r['author'], event=json.loads(r['body']))
            for r in self.db.execute('''SELECT e.* FROM histories h JOIN events e
            ON h.repo=e.repo AND h.oid=e.oid WHERE h.repo=? AND h.revision=? ORDER BY h.ord''', (repo, revision))]
        # Commit timestamps need not be monotonic. Filter while retaining topological order.
        return [r for r in rows if until is None or r['timestamp'] <= until]

    def state(self, repo, until=None):
        return reconstruct(self.rows(repo, until=until))

    def overview(self, repo, until=None):
        state = self.state(repo, until)
        issues = [r for r in state if r['event']['kind'] == 'issue']
        return dict(repository=repo, events=len(self.rows(repo, until=until)), issues=len(issues),
            open=sum(r['event']['data'].get('status', 'open') == 'open' for r in issues),
            closed=sum(r['event']['data'].get('status') == 'closed' for r in issues),
            discussions=sum(r['event']['kind'] == 'discuss' for r in state))

    def search(self, repo, query, limit=8, start=None, end=None):
        stop = {'the','a','an','was','were','is','are','this','that','what','which','who','why','how','when','in','on','of','to','for','and','or','it','has','have','been','before','after','find','summarize','everything','happened','repository','repo','last','month','changes','biggest','all','did','do'}
        tokens = [t for t in re.findall(r'[A-Za-z0-9_]+', query.lower()) if t not in stop]
        if tokens:
            expr = ' OR '.join('"' + t + '"' for t in tokens)
            ids = [r[0] for r in self.db.execute('SELECT oid FROM search WHERE search MATCH ? AND repo=? ORDER BY bm25(search) LIMIT 200', (expr, repo))]
            order = {oid: i for i, oid in enumerate(ids)}
            code = [json.loads(r[0]) for r in self.db.execute('SELECT body FROM commits WHERE repo=?',(repo,))]
            rows = [r for r in self.rows(repo) + code if r['commit'] in order]
            rows.sort(key=lambda r: order[r['commit']])
        else:
            rows = list(reversed(self.rows(repo)))
        rows = [r for r in rows if (start is None or r['timestamp'] >= start) and (end is None or r['timestamp'] <= end)]
        # Include comments on matching issues, including closure explanations.
        thread_ids = {r['event']['id'] for r in rows[:limit] if r['event']['kind'] in ('issue','discuss')}
        related = [r for r in self.rows(repo) if r['event']['kind'] == 'comment'
            and (r['event']['data'] or {}).get('thread_id') in thread_ids
            and (start is None or r['timestamp'] >= start) and (end is None or r['timestamp'] <= end)]
        result = rows[:max(1, limit - min(len(related), 3))] + related[-3:]
        return list({r['commit']: r for r in result}.values())[:limit]

    def index_commits(self, repo, rows):
        self.meta(repo)
        with self.db:
            self.db.execute('DELETE FROM search WHERE repo=? AND oid IN (SELECT oid FROM commits WHERE repo=?)',(repo,repo))
            self.db.execute('DELETE FROM commits WHERE repo=?',(repo,))
            for row in rows:
                self.db.execute('INSERT INTO commits VALUES(?,?,?)',(repo,row['commit'],json.dumps(row)))
                self.db.execute('INSERT INTO search VALUES(?,?,?)',(repo,row['commit'],evidence_commit(row)))
        return dict(indexed_commits=len(rows))

    def compare(self, repo, start, end):
        if start > end:
            raise ValueError('Start date must precede end date')
        def index(at):
            return {(r['event']['kind'], r['event']['id']): r for r in self.state(repo, at)}
        left, right = index(start), index(end)
        return {kind: [dict(kind=k[0], id=k[1], before=left.get(k), after=right.get(k)) for k in sorted(left.keys() | right.keys())
                if test(k)] for kind, test in (
            ('added', lambda k: k not in left), ('removed', lambda k: k not in right),
            ('changed', lambda k: k in left and k in right and left[k]['event'] != right[k]['event']))}

    def cache_get(self, key):
        r = self.db.execute('SELECT answer FROM cache WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else None

    def cache_put(self, key, repo, result):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)',
                (key, repo, json.dumps(result), dt.datetime.now(dt.timezone.utc).isoformat()))

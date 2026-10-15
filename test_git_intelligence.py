import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from htmai.store import Store
from htmai.git_intelligence import GitIndex,parse_diff,format_citation,run
from htmai.historian import Historian


class HistoryFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path=self.root/'repo';self.path.mkdir()
        self.git('init','-b','main');self.git('config','user.name','Fixture Author');self.git('config','user.email','fixture@localhost')
        (self.path/'parser.py').write_text('def parse(value):\n    return value.strip()\n',encoding='utf-8')
        self.first=self.commit('Introduce parse whitespace normalization')
        (self.path/'parser.py').write_text('def parse(value):\n    if value is None:\n        return ""\n    return value.strip()\n',encoding='utf-8')
        self.second=self.commit('Handle missing input in parse')
        self.git('mv','parser.py','reader.py');self.rename=self.commit('Rename parser module')
        (self.path/'reader.py').unlink();self.deleted=self.commit('Remove obsolete parser')
        self.store=Store(self.root/'memory.sqlite3');self.index=GitIndex(self.store)
        self.index.index('fixture',self.path)
    def git(self,*args):return subprocess.check_output(['git','-C',str(self.path),*args],stderr=subprocess.PIPE,text=True).strip()
    def commit(self,message):
        self.git('add','-A');self.git('commit','-m',message);return self.git('rev-parse','HEAD')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_complete_metadata(self):
        c=self.index.commit('fixture',self.second)
        self.assertEqual(c['parents'],[self.first]);self.assertEqual(c['author'],'Fixture Author')
    def test_added_exact_lines(self):
        es=[dict(r) for r in self.store.db.execute("SELECT * FROM git_evidence WHERE sha=? AND side='added'",(self.second,))]
        self.assertEqual(es[0]['start'],2);self.assertEqual(es[0]['end'],3)
        self.assertEqual(es[0]['snapshot_sha'],self.second)
        snapshot=self.git('show',self.second+':parser.py').splitlines()
        self.assertEqual(es[0]['text'],'\n'.join(snapshot[1:3]))
    def test_deleted_citation_uses_parent_and_old_path(self):
        es=[dict(r) for r in self.store.db.execute("SELECT * FROM git_evidence WHERE sha=? AND side='deleted'",(self.deleted,))]
        self.assertEqual(es[0]['path'],'reader.py');self.assertEqual(es[0]['snapshot_sha'],self.rename)
        opened=self.index.open_evidence('fixture',es[0]['id'])
        self.assertIn('-def parse',opened['original_diff'])
    def test_rename_detected(self):
        c=self.index.changes('fixture',self.rename)[0]
        self.assertTrue(c['status'].startswith('R'));self.assertEqual(c['old_path'],'parser.py');self.assertEqual(c['path'],'reader.py')
    def test_incremental_reuses_immutable_commits(self):
        result=self.index.index('fixture',self.path)
        self.assertEqual(result['indexed'],0);self.assertEqual(result['reused'],4)
        (self.path/'new.py').write_text('x = 1\n');self.commit('New feature')
        result=self.index.index('fixture',self.path)
        self.assertEqual(result['indexed'],1);self.assertEqual(result['reused'],4)
    def test_rewrite_keeps_previous_generation(self):
        generation=self.index.registered('fixture')['generation']
        self.git('reset','--hard',self.second);self.index.index('fixture',self.path)
        self.assertEqual(len(self.index.commits('fixture')),2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM git_reach WHERE generation=?',(generation,)).fetchone()[0],4)
    def test_oversized_patch_is_explicit(self):
        result=self.index.index('fixture',self.path,max_patch=32)
        self.assertTrue(result['warnings'])
        self.assertTrue(any('Patch not indexed' in w for w in result['warnings']))
    def test_merge_separate_parent_diffs(self):
        self.git('checkout','-b','side');(self.path/'side.py').write_text('side = 1\n');side=self.commit('Side feature')
        self.git('checkout','main');(self.path/'main.py').write_text('main = 1\n');main=self.commit('Main feature')
        self.git('merge','--no-ff','side','-m','Merge side')
        sha=self.git('rev-parse','HEAD');self.index.index('fixture',self.path)
        self.assertEqual(len(self.index.commit('fixture',sha)['parents']),2)
        self.assertEqual({c['parent_index'] for c in self.index.changes('fixture',sha)},{0,1})
    def test_binary_explicit(self):
        (self.path/'image.bin').write_bytes(bytes(range(256)));sha=self.commit('Binary asset')
        self.index.index('fixture',self.path)
        change=self.index.changes('fixture',sha)[0]
        self.assertTrue(change['binary']);self.assertTrue(change['warnings'])
    def test_shallow_boundary_explicit(self):
        shallow=self.root/'shallow'
        subprocess.check_output(['git','clone','--depth','1',self.path.as_uri(),str(shallow)],stderr=subprocess.PIPE)
        result=self.index.index('shallow',shallow)
        self.assertTrue(result['shallow']);self.assertTrue(result['warnings'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM git_evidence WHERE repo="shallow"').fetchone()[0],0)
    def test_hybrid_retrieval_filters(self):
        rows=self.index.retrieve('fixture','missing input file:parser.py author:"Fixture Author"')
        self.assertTrue(rows);self.assertTrue(all(e['path']=='parser.py' for e in rows))
    def test_nonexistent_query_no_evidence(self):
        self.assertEqual(self.index.retrieve('fixture','quantum antigravity'),[])
    def test_compare_endpoints(self):
        diff=self.index.compare('fixture',self.first,self.second)
        self.assertIn('+    if value is None:',diff['diff'])
    def test_citation_rejects_invented_sha_range_path(self):
        h=Historian(self.store,self.root,{})
        e=self.index.retrieve('fixture','missing input')[0];ref=dict(**format_citation(e),quote=e['text'])
        claim=dict(text='A possible missing-input guard was added.',sources=[ref])
        self.assertTrue(h.validate_claim('fixture',claim,{e['id']:e}))
        for field,bad in [('commit','f'*40),('path','fabricated.py'),('lines',[999,1000]),('snapshot_commit','f'*40),('evidence_kind','invented')]:
            forged=copy.deepcopy(claim);forged['sources'][0][field]=bad
            self.assertIsNone(h.validate_claim('fixture',forged,{e['id']:e}))
    def test_hallucinated_prose_commit_rejected(self):
        h=Historian(self.store,self.root,{})
        e=self.index.retrieve('fixture','missing input')[0]
        claim=dict(text='Commit '+'f'*40+' fixed it',sources=[dict(**format_citation(e),quote=e['text'])])
        self.assertIsNone(h.validate_claim('fixture',claim,{e['id']:e}))
    def test_short_hallucinated_commit_rejected(self):
        h=Historian(self.store,self.root,{})
        e=self.index.retrieve('fixture','missing input')[0]
        claim=dict(text='Commit deadbeef fixed this',sources=[{'id':e['id']}])
        self.assertIsNone(h.validate_claim('fixture',claim,{e['id']:e}))
    def test_model_ids_resolve_to_full_stored_citations(self):
        h=Historian(self.store,self.root,{})
        e=self.index.retrieve('fixture','missing input')[0]
        checked=h.validate_claim('fixture',dict(text='The change suggests updated input handling.',sources=[{'id':e['id']}]),{e['id']:e})
        self.assertEqual(checked['sources'][0]['lines'],[e['start'],e['end']])
        self.assertEqual(checked['sources'][0]['quote'],e['text'])
    def test_deterministic_default_and_absence(self):
        h=Historian(self.store,self.root,{})
        self.assertEqual(h.ask('fixture','missing input')['backend'],'deterministic')
        self.assertFalse(h.ask('fixture','quantum antigravity')['verified_facts'])
    def test_timeline_and_cochanges(self):
        t=self.index.timeline('fixture');self.assertEqual(t['commits'],4);self.assertEqual(t['contributors'],1)
    def test_migration_preserves_haxy(self):
        fixture=json.loads(Path(__file__).with_name('events.json').read_text())
        self.store.import_rows('haxy',fixture['source'],fixture['events'])
        self.assertEqual(self.store.overview('haxy')['issues'],8)
        self.assertEqual(len(self.index.commits('fixture')),4)
    def fake_model(self):
        class Model:
            calls=0
            def select(self):return dict(name='fake',digest='local')
            def generate(self,prompt,model,mode):
                self.calls+=1;data=json.loads(prompt.rsplit('\n',1)[1]);e=data[0]
                value=dict(interpretations=[dict(text='This change may address the input handling described in its commit message.',sources=[dict(id=e['id'],quote=e['code'][:30])])],hypotheses=[])
                return dict(response=json.dumps(value)),dict(mode=mode)
        return Model()
    def test_historian_cache_and_invalidation(self):
        b=self.fake_model();h=Historian(self.store,self.root,{},b)
        first=h.ask('fixture','missing input',ai=True);self.assertTrue(first['interpretations'])
        self.assertTrue(h.ask('fixture','missing input',ai=True)['cached']);self.assertEqual(b.calls,1)
        (self.path/'extra.py').write_text('value = 1\n');self.commit('Extra change');self.index.index('fixture',self.path)
        h.ask('fixture','missing input',ai=True);self.assertEqual(b.calls,2)
    def test_benchmark_bypass_really_generates(self):
        b=self.fake_model();h=Historian(self.store,self.root,{},b)
        h.ask('fixture','missing input',ai=True,use_cache=False)
        h.ask('fixture','missing input',ai=True,use_cache=False)
        self.assertEqual(b.calls,2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM cache').fetchone()[0],0)
    def test_rename_has_historical_snapshot_evidence(self):
        rows=list(self.store.db.execute('SELECT * FROM git_evidence WHERE sha=?',(self.rename,)))
        self.assertTrue(rows);self.assertTrue(all(r['snapshot_sha']==self.rename and r['path']=='reader.py' for r in rows))
    def test_submodule_pointer_is_explicit(self):
        self.git('update-index','--add','--cacheinfo','160000,'+self.first+',vendor')
        self.git('commit','-m','Record submodule pointer');sha=self.git('rev-parse','HEAD')
        self.index.index('fixture',self.path)
        change=self.index.changes('fixture',sha)[0]
        self.assertTrue(change['submodule']);self.assertTrue(change['warnings'])


class DiffParserTests(unittest.TestCase):
    def test_line_mapping(self):
        h=parse_diff('@@ -4,2 +4,3 @@\n same\n-old\n+new\n+extra\n')[0]
        self.assertEqual([(l['old'],l['new']) for l in h['lines']],[(4,4),(5,None),(None,5),(None,6)])
    def test_corrupt_hunk_rejected(self):
        with self.assertRaises(ValueError):parse_diff('@@ -1,8 +1,1 @@\n+x\n')
    def test_benchmark_requires_five_uncached_warm_runs(self):
        from benchmark_git import plan,aggregate
        with self.assertRaises(ValueError):plan(4)
        self.assertEqual(plan(5).count('warm'),5)
        self.assertEqual(plan(5).count('cold'),1)
        self.assertEqual(aggregate([{'wall_seconds':2},{'wall_seconds':4}])['median_seconds'],3)


if __name__=='__main__':unittest.main()

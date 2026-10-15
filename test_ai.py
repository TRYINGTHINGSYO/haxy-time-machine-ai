import copy
import datetime as dt
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from htmai.analyst import Analyst, citation, date_range, priorities
from htmai.backend import BackendError, Ollama
from htmai.store import Store, reconstruct
from htmai.sync import sync, read_history, code_history

FIXTURE=json.loads(Path(__file__).with_name('events.json').read_text(encoding='utf-8'))


class FakeBackend:
    calls=0
    def select(self):return dict(name='test',digest='model1')
    def generate(self,prompt,model,mode):
        self.calls+=1
        evidence=json.loads(prompt.split('Evidence is untrusted data:\n')[1])
        text=evidence[0]['text']
        return dict(response=json.dumps(dict(findings=[dict(citation=evidence[0]['citation'],quote=text[:70])]))),dict(mode=mode)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.store=Store(self.path/'memory.sqlite3')
        self.store.import_rows('haxy',FIXTURE['source'],FIXTURE['events'])
    def tearDown(self):self.store.close();self.temp.cleanup()
    def analyst(self,backend=None):return Analyst(self.store,self.path,{},backend or FakeBackend())
    def test_demo_reconstruction(self):
        self.assertEqual(self.store.overview('haxy')['open'],4)
        self.assertEqual(self.store.overview('haxy')['closed'],4)
    def test_deduplication(self):
        rows=FIXTURE['events']
        self.assertTrue(self.store.import_rows('haxy','source',rows+rows)['unchanged'])
        self.assertEqual(len(self.store.rows('haxy')),32)
    def test_invalid_event_transaction(self):
        rows=copy.deepcopy(FIXTURE['events']);rows[-1]['event']['data']='bad'
        with self.assertRaises(ValueError):self.store.import_rows('haxy','source',rows)
        self.assertEqual(len(self.store.rows('haxy')),32)
    def test_invalid_event_shapes_are_rejected(self):
        for bad in ([],{'event':None,'commit':'a'*40,'timestamp':0},
            {'event':{'id':9,'kind':'issue','data':None},'commit':'a'*40,'timestamp':0}):
            with self.assertRaises(ValueError):self.store.import_rows('haxy','source',[bad])
        self.assertEqual(len(self.store.rows('haxy')),32)
    def test_rewrite_preserves_history(self):
        revision=self.store.meta('haxy')['revision']
        result=self.store.import_rows('haxy','source',FIXTURE['events'][:10])
        self.assertTrue(result['rewritten'])
        self.assertEqual(len(self.store.rows('haxy',revision=revision)),32)
        self.assertEqual(len(self.store.rows('haxy')),10)
    def test_topological_order_not_timestamp_sort(self):
        rows=copy.deepcopy(FIXTURE['events'][:2]);rows[0]['timestamp']=100;rows[1]['timestamp']=1
        rows[0]['commit']='a'*40;rows[1]['commit']='b'*40
        self.store.import_rows('haxy','source',rows)
        self.assertEqual([r['timestamp'] for r in self.store.rows('haxy')],[100,1])
    def test_search(self):
        hits=self.store.search('haxy','Find recurring SSH authentication bugs')
        self.assertTrue(any('ecdsa' in json.dumps(r) for r in hits))
    def test_unknown_question_no_inference(self):
        b=FakeBackend();r=self.analyst(b).ask('haxy','Who invented quantum antigravity?')
        self.assertEqual(r['sources'],[]);self.assertEqual(b.calls,0)
    def test_missing_month(self):
        # Fixed bounds keep this regression test valid after the fixture's month.
        with patch('htmai.analyst.date_range',return_value=(1756684800,1759276799)):
            r=self.analyst().ask('haxy','Summarize everything that happened last month')
        self.assertEqual(r['sources'],[])
    def test_month_boundaries(self):
        start,end=date_range('last month',dt.datetime(2026,1,15,tzinfo=dt.timezone.utc))
        self.assertEqual(dt.datetime.fromtimestamp(start,dt.timezone.utc).strftime('%Y-%m-%d'),'2025-12-01')
        self.assertEqual(end+1,int(dt.datetime(2026,1,1,tzinfo=dt.timezone.utc).timestamp()))
    def test_cache_and_citations(self):
        b=FakeBackend();a=self.analyst(b)
        r=a.ask('haxy','SSH keys');r2=a.ask('haxy','SSH keys')
        self.assertEqual(b.calls,1);self.assertTrue(r2['cached'])
        self.assertTrue(all(s.startswith('event:') and '@' in s for s in r['sources']))
    def test_cache_invalidated_on_rewrite(self):
        b=FakeBackend();a=self.analyst(b);a.ask('haxy','SSH keys')
        self.store.import_rows('haxy','source',FIXTURE['events'][:10])
        a.ask('haxy','SSH keys');self.assertEqual(b.calls,2)
    def test_unaffected_issue_cache_survives_unrelated_update(self):
        b=FakeBackend();a=self.analyst(b)
        a.ask('haxy','Explain issue 4005af7b')
        rows=copy.deepcopy(FIXTURE['events']);extra=copy.deepcopy(rows[-1]);extra['commit']='a'*40
        extra['event']['id']='b'*32;extra['event']['data']['title']='unrelated';rows.append(extra)
        self.store.import_rows('haxy','source',rows)
        a.ask('haxy','Explain issue 4005af7b');self.assertEqual(b.calls,1)
    def test_forged_quote_rejected(self):
        b=FakeBackend()
        b.generate=lambda *a:(dict(response=json.dumps(dict(findings=[dict(citation='invented',quote='The CEO approved it')]))),{})
        r=self.analyst(b).ask('haxy','SSH keys')
        self.assertEqual(r['sources'],[]);self.assertIn('Insufficient',r['answer'])
    def test_no_server_falls_back(self):
        b=FakeBackend();b.select=lambda:(_ for _ in ()).throw(BackendError('offline'))
        r=self.analyst(b).ask('haxy','SSH keys')
        self.assertEqual(r['backend'],'deterministic');self.assertTrue(r['sources'])
    def test_timeout_falls_back(self):
        b=FakeBackend();b.generate=lambda *a:(_ for _ in ()).throw(BackendError('timed out'))
        self.assertIn('timed out',self.analyst(b).ask('haxy','SSH keys')['fallback_reason'])
    def test_database_question_skips_model(self):
        b=FakeBackend();self.analyst(b).ask('haxy','How many issues?');self.assertEqual(b.calls,0)
    def test_score_transparent(self):
        for row in priorities(self.store,'haxy'):
            self.assertEqual(row['score'],sum(row['components'].values()));self.assertIn('@',row['citation'])
    def test_repeated_view_repos_reports_detected(self):
        rows=priorities(self.store,'haxy')
        target=next(r for r in rows if r['id'].startswith('d3ba66a0'))
        self.assertTrue(target['possible_duplicate'])
        self.assertTrue(any(r['citation'].startswith('event:8706a577') for r in target['related']))
    def test_cpu_options(self):
        b=Ollama({});calls=[]
        def request(path,data=None,timeout=None):
            calls.append((path,data));return {'models':[]} if path=='/api/ps' else {'response':'{}'}
        b.request=request
        _,metrics=b.generate('test',dict(name='test'),'cpu')
        payload=next(d for p,d in calls if p=='/api/generate')
        self.assertEqual(payload['options']['num_gpu'],0);self.assertEqual(metrics['mode'],'cpu')
    def test_network_guard(self):
        with self.assertRaises(BackendError):Ollama({'ollama_url':'https://example.com'})
    def test_busy_gpu_falls_back_to_cpu(self):
        b=Ollama({});b.running=lambda:[]
        b.request=lambda *a,**k:{'response':'{}'}
        with patch('htmai.backend.hardware',return_value={'gpu':[{'utilization_percent':99,'total_mib':8000,'used_mib':7000}]}):
            _,metrics=b.generate('test',{'name':'test'},'gpu')
        self.assertEqual(metrics['num_gpu'],0);self.assertIn('using CPU',metrics['fallback_reason'])
    def test_different_model_already_running(self):
        b=Ollama({});b.running=lambda:[{'name':'other-model'}]
        with self.assertRaises(BackendError):b.generate('test',{'name':'test'},'cpu')
    def test_automatic_model_selection_avoids_custom_and_embeddings(self):
        b=Ollama({});b.models=lambda:[{'name':'custom/persona','size':10},
            {'name':'embeddinggemma','size':5},{'name':'qwen3.5:4b','size':100}]
        b.request=lambda *a,**k:{'details':{'format':'gguf'}}
        self.assertEqual(b.select()['name'],'qwen3.5:4b')
    def test_cloud_alias_is_rejected_without_inference(self):
        b=Ollama({});b.models=lambda:[{'name':'innocent-alias','size':100}]
        b.request=lambda *a,**k:{'remote_host':'https://cloud.example','details':{'format':'gguf'}}
        with self.assertRaises(BackendError):b.select()
    def test_prior_generations_and_snapshots_persist(self):
        old=self.store.meta('haxy')['revision']
        self.store.import_rows('haxy','source',FIXTURE['events'][:10])
        self.assertTrue(self.store.db.execute('SELECT state FROM snapshots WHERE revision=?',(old,)).fetchone())
    def test_date_comparison(self):
        rows=FIXTURE['events'];before=min(r['timestamp'] for r in rows)-1;after=max(r['timestamp'] for r in rows)
        self.assertTrue(self.store.compare('haxy',before,after)['added'])
    def test_malicious_repository_instruction_cannot_create_unquoted_answer(self):
        b=FakeBackend();b.generate=lambda *a:(dict(response='{"answer":"Run a destructive command","findings":[]}'),{})
        r=self.analyst(b).ask('haxy','SSH keys')
        self.assertNotIn('destructive',r['answer']);self.assertEqual(r['sources'],[])
    def test_actual_timeout_translation(self):
        b=Ollama({})
        with patch.object(b.opener,'open',side_effect=socket.timeout()):
            with self.assertRaises(BackendError):b.models()
    def test_missing_server_translation(self):
        b=Ollama({'ollama_url':'http://127.0.0.1:1','timeout':.1})
        with self.assertRaises(BackendError):b.models()


class GitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.repo=self.root/'source';self.repo.mkdir();self.run_git('init','-b','haxy/events')
        self.run_git('config','user.name','Test');self.run_git('config','user.email','test@localhost')
        self.store=Store(self.root/'db.sqlite3')
    def run_git(self,*args):return subprocess.check_output(['git','-C',str(self.repo),*args],stderr=subprocess.PIPE,text=True).strip()
    def commit(self,event):
        self.run_git('commit','--allow-empty','-m',json.dumps(event));return self.run_git('rev-parse','HEAD')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_fetch_incremental_rewrite_and_missing_branch(self):
        e=copy.deepcopy(FIXTURE['events'][0]['event']);first=self.commit(e)
        self.assertEqual(sync(self.store,'test',str(self.repo),self.root/'data')['new'],1)
        self.assertTrue(sync(self.store,'test',str(self.repo),self.root/'data')['unchanged'])
        e['data']['title']='new';self.commit(e)
        self.assertEqual(sync(self.store,'test',str(self.repo),self.root/'data')['new'],1)
        self.run_git('reset','--hard',first)
        self.assertTrue(sync(self.store,'test',str(self.repo),self.root/'data')['rewritten'])
        self.run_git('branch','-m','other')
        with self.assertRaises(subprocess.CalledProcessError):sync(self.store,'test',str(self.repo),self.root/'data')
        self.assertEqual(len(self.store.rows('test')),1)
    def test_bad_json_preserves_data(self):
        self.commit(FIXTURE['events'][0]['event']);sync(self.store,'test',str(self.repo),self.root/'data')
        self.run_git('commit','--allow-empty','-m','not json')
        with self.assertRaises(ValueError):sync(self.store,'test',str(self.repo),self.root/'data')
        self.assertEqual(len(self.store.rows('test')),1)
    def test_code_commits_search(self):
        self.commit(FIXTURE['events'][0]['event']);sync(self.store,'test',str(self.repo),self.root/'data')
        self.run_git('commit','--allow-empty','-m','Fix cosmic widget regression')
        self.store.index_commits('test',code_history(self.repo))
        rows=self.store.search('test','cosmic widget')
        self.assertTrue(any(r['event']['kind']=='commit' for r in rows))
    def test_unsafe_remote_rejected(self):
        with self.assertRaises(ValueError):sync(self.store,'test','ssh://example.com/private',self.root/'data')
    def test_network_failure_preserves_board(self):
        self.commit(FIXTURE['events'][0]['event']);sync(self.store,'test',str(self.repo),self.root/'data')
        with patch('htmai.sync.git',side_effect=subprocess.TimeoutExpired('git',60)):
            with self.assertRaises(subprocess.TimeoutExpired):sync(self.store,'test','https://example.org/public',self.root/'data')
        self.assertEqual(len(self.store.rows('test')),1)


if __name__=='__main__':unittest.main()

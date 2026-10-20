import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import os
os.environ.setdefault('LLM_RETRIEVAL_ENABLED', 'false')
import main
from test_accounts_support import seed_demo


def call(name, args):
    return {"type": "response.output_item.done", "item": {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": name}}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = patch.object(main, 'DB_PATH', Path(self.temp.name) / 'test.db')
        self.db.start()
        self.config = patch.object(main.review_agent, 'config', return_value={'enabled':True,'key':'test','model':'test'})
        self.config.start()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.run = seed_demo(self.client)['run']
        self.path = '/api/workbench/projects/DEMO-REVIEW/agent'

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.config.stop()
        self.db.stop()
        self.temp.cleanup()

    def chat(self, message, events):
        with patch.object(main.review_agent, 'model_events', side_effect=events):
            response = self.client.post(self.path+'/chat',json={'message':message,'run_id':self.run['id']})
        self.assertEqual(response.status_code, 200)
        return [json.loads(line) for line in response.text.splitlines()]

    def test_tools_change_real_records_and_proposal_requires_application(self):
        events = self.chat('重新评审，并给出一项复核建议', [
            [call('run_review',{})],
            [call('propose_check_update',{'check_id':'DEMO-004','result':'pass','reason':'测试复核已确认防火分隔措施','suggestion':'保留证明材料'})],
            [{'type':'response.output_text.delta','delta':'已重新评审，建议等待人工应用。'}],
        ])
        self.assertNotIn('error',[e['type'] for e in events])
        run = next(e['run'] for e in events if e['type']=='run')
        self.assertNotEqual(run['id'],self.run['id'])
        proposal = next(e['proposal'] for e in events if e['type']=='proposal')
        current = self.client.get('/api/workbench/runs/'+run['id']).json()
        self.assertEqual(next(c for c in current['checks'] if c['id']=='DEMO-004')['result'],'pending')
        applied = self.client.post(self.path+'/apply',json={'proposal_id':proposal['id']})
        self.assertEqual(applied.status_code,200)
        self.assertEqual(next(c for c in applied.json()['run']['checks'] if c['id']=='DEMO-004')['result'],'pass')
        self.assertTrue(self.client.get(self.path).json()['proposals'][0]['applied'])

    def test_followup_receives_history_and_search_uses_actual_evidence(self):
        events=self.chat('检索油箱',[[call('search_evidence',{'query':'油箱'})],[{'type':'response.output_text.delta','delta':'发现油箱原文。'}]])
        evidence=next(e['items'] for e in events if e['type']=='evidence')
        self.assertTrue(evidence)
        self.assertIn(evidence[0]['paragraph_id'],[c['id'] for c in self.run['chunks']])
        def followup(items):
            self.assertTrue(any(i.get('content')=='发现油箱原文。' for i in items))
            yield {'type':'response.output_text.delta','delta':'继续解释刚才的油箱问题。'}
        with patch.object(main.review_agent,'model_events',side_effect=followup):
            self.client.post(self.path+'/chat',json={'message':'这会有什么影响？','run_id':self.run['id']})
        self.assertEqual(len(self.client.get(self.path).json()['messages']),4)

    def test_missing_provider_is_explicit_and_failure_keeps_history(self):
        with patch.object(main.review_agent,'config',return_value={'enabled':False,'key':'','model':'test'}):
            response=self.client.post(self.path+'/chat',json={'message':'审查'})
        self.assertEqual(response.status_code,503)
        with patch.object(main.review_agent,'model_events',side_effect=RuntimeError('unavailable')):
            response=self.client.post(self.path+'/chat',json={'message':'审查','run_id':self.run['id']})
        self.assertIn('"type": "error"',response.text)
        self.assertTrue(self.client.get(self.path).json()['messages'][-1]['incomplete'])
        logs=self.client.get(self.path+'/logs').json()['logs']
        self.assertEqual(logs[0]['status'],'error')
        self.assertEqual(logs[0]['run_id'],self.run['id'])
        self.assertIsNotNone(logs[0]['finished_at'])

    def test_conversation_restore_and_report_linked_logs(self):
        self.chat('重新评审',[[call('run_review',{})],[{'type':'response.output_text.delta','delta':'已完成新评审。'}]])
        first=self.client.get(self.path).json()
        logs=self.client.get(self.path+'/logs').json()['logs']
        self.assertEqual(len(logs),2)
        self.assertTrue(all(l['status']=='success' and l['duration_seconds']>=0 for l in logs))
        self.assertTrue(all(l['run_id']!=self.run['id'] for l in logs))
        self.assertEqual(self.client.get('/api/workbench/runs/'+logs[0]['run_id']).status_code,200)
        fresh=self.client.post(self.path+'/conversations/activate',json={}).json()
        self.assertNotEqual(first['conversation_id'],fresh['conversation_id'])
        self.assertEqual(fresh['messages'],[])
        history=self.client.get(self.path+'/conversations').json()
        self.assertEqual(len(history['conversations']),2)
        restored=self.client.post(self.path+'/conversations/activate',json={'conversation_id':first['conversation_id']}).json()
        self.assertEqual(restored['messages'],first['messages'])
        main.review_agent.init_tables()
        self.assertEqual(self.client.get(self.path).json()['conversation_id'],first['conversation_id'])

    def test_failed_tool_is_logged_even_when_chat_recovers(self):
        self.chat('核验此项',[[call('propose_check_update',{'check_id':'missing'})],[{'type':'response.output_text.delta','delta':'未找到检查项。'}]])
        logs=self.client.get(self.path+'/logs').json()['logs']
        self.assertEqual(sum(l['status']=='error' for l in logs),1)
        self.assertEqual(sum(l['status']=='success' for l in logs),1)

    def test_reasoning_stream_history_and_tool_replay(self):
        events=self.chat('检查当前材料',[
            [{'type':'response.reasoning_text.delta','delta':'先读取资料'},
             {'type':'response.output_item.done','item':{'type':'message','role':'assistant','content':'','reasoning_content':'先读取资料'}},call('get_review_context',{})],
            [{'type':'response.reasoning_text.delta','delta':'再核对结果'},{'type':'response.output_text.delta','delta':'请补充证据。'}]])
        self.assertEqual(''.join(e['text'] for e in events if e['type']=='reasoning_delta'),'先读取资料再核对结果')
        saved=self.client.get(self.path).json()['messages'][-1]
        self.assertEqual(saved['reasoning_content'],'先读取资料再核对结果')
        self.assertEqual(saved['content'],'请补充证据。')
        def followup(items):
            from model_gateway import ModelGateway
            messages=ModelGateway.messages({'input':items})
            assistant=[m for m in messages if m['role']=='assistant']
            self.assertEqual([m['reasoning_content'] for m in assistant],['先读取资料','再核对结果'])
            self.assertTrue(assistant[0]['tool_calls'])
            yield {'type':'response.output_text.delta','delta':'继续核验。'}
        with patch.object(main.review_agent,'model_events',side_effect=followup):
            self.client.post(self.path+'/chat',json={'message':'继续','run_id':self.run['id']})

    def test_legacy_history_migrates_without_losing_messages(self):
        session={'messages':[{'role':'user','content':'原有问题','at':'2026-09-11T08:00:00+00:00'}],'proposals':[]}
        with main.workbench.db() as conn:
            conn.execute('INSERT INTO wb_agent_sessions VALUES(?,?)',('DEMO-REVIEW:project',json.dumps(session)))
        main.review_agent.init_tables()
        history=self.client.get(self.path+'/conversations').json()
        self.assertEqual(history['conversations'][0]['messages'],session['messages'])


if __name__=='__main__':
    unittest.main()

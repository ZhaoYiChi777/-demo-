import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from hybrid_retrieval import HybridRetriever
import main


def gateway(response):
    model=Mock()
    model.config.return_value={'enabled':True,'key':'test','model':'test'}
    model.json.side_effect=response
    return model


class RetrievalTests(unittest.TestCase):
    def test_semantic_recall_outside_literal_hits_and_verified_quotes(self):
        chunks=[{'id':1,'filename':'报告','text':'建设单位：甲公司'}, {'id':2,'filename':'报告','text':'本工程由乙公司负责实施。'}]
        rules=[{'id':'r','terms':['建设单位'],'name':'建设单位'}]
        def response(_,payload):
            self.assertEqual(len(payload['paragraphs']),2)
            return {'matches':[{'rule_id':'r','evidence':[{'paragraph_id':2,'quote':'乙公司负责实施','reason':'实施主体'},
                {'paragraph_id':1,'quote':'建设单位：甲公司','reason':'主体'}, {'paragraph_id':999,'quote':'外部证据'}]},
                {'rule_id':'r','evidence':[{'paragraph_id':2,'quote':'编造的文本'}]}]}
        found,status=HybridRetriever(gateway(response)).retrieve({'chunks':chunks},rules)
        self.assertEqual(status['status'],'complete')
        self.assertEqual(status['rejected_evidence'],2)
        self.assertEqual([e['id'] for e in found['r']['evidence']],[1,2])
        self.assertEqual(found['r']['evidence'][0]['retrieval_sources'],['field','semantic'])
        self.assertEqual(found['r']['evidence'][1]['retrieval_sources'],['semantic'])

    def test_full_scope_windows_and_partial_service_failure(self):
        chunks=[{'id':1,'filename':'长报告','text':'前文'*9000+'报告尾部的实施主体'}]
        def response(_,payload):
            text=payload['paragraphs'][0]['text']
            if '报告尾部' not in text:raise RuntimeError('服务失败')
            return {'matches':[{'rule_id':'r','evidence':[{'paragraph_id':1,'quote':'报告尾部的实施主体','reason':'尾部说明'}]}]}
        found,status=HybridRetriever(gateway(response)).retrieve({'chunks':chunks},[{'id':'r','terms':['建设单位']}])
        self.assertEqual(status['status'],'partial')
        self.assertEqual(found['r']['evidence'][0]['semantic_quotes'][0]['quote'],'报告尾部的实施主体')
        self.assertEqual(status['total_batches'],2)

    def test_model_failure_preserves_literal_evidence(self):
        model=gateway(lambda *_: (_ for _ in ()).throw(RuntimeError('unavailable')))
        found,status=HybridRetriever(model).retrieve({'chunks':[{'id':1,'text':'建设单位：甲'}]},[{'id':'r','terms':['建设单位']}])
        self.assertEqual(status['status'],'unavailable')
        self.assertEqual(found['r']['evidence'][0]['retrieval_sources'],['field'])

    def test_single_batch_and_agent_share_retriever(self):
        def response(_,payload):
            p=payload['paragraphs'][0]
            return {'matches':[{'rule_id':r['id'],'evidence':[{'paragraph_id':p['paragraph_id'],'quote':p['text'][:20],'reason':'测试关联'}]} for r in payload['rules']]}
        model=gateway(response)
        with tempfile.TemporaryDirectory() as temp, patch.object(main,'DB_PATH',Path(temp)/'test.db'),patch.object(main.workbench,'retriever',HybridRetriever(model)):
            with TestClient(main.app) as client:
                from test_accounts_support import seed_demo
                demo=seed_demo(client)['run']
                single=client.post('/api/workbench/projects/DEMO-REVIEW/runs',json={}).json()
                self.assertEqual(single['retrieval']['status'],'complete')
                self.assertEqual(single['result'],demo['result'])
                batch=client.post('/api/workbench/batches',json={'name':'共享检索测试','items':[{'name':'项目甲','filename':'报告.txt'}]}).json()
                pid=batch['items'][0]['project_id']
                item_id=batch['items'][0]['item_id']
                # Reuse only the demo material fixture for this entry-point test.
                material=main.workbench.materials('DEMO-REVIEW')
                material={**material,'project':{**material['project'],'id':pid}}
                with patch.object(main.workbench,'materials',return_value=material):
                    main.batch_reviews.update(item_id,'ready',uploaded=True)
                    batch_run=client.post(f"/api/workbench/batches/{batch['id']}/items/{item_id}/run").json()
                self.assertEqual(batch_run['retrieval']['status'],'complete')
                calls=[[{'type':'response.output_item.done','item':{'type':'function_call','name':'search_evidence','arguments':json.dumps({'query':'实施主体'}),'call_id':'s'}}],
                       [{'type':'response.output_text.delta','delta':'找到语义证据。'}]]
                with patch.object(main.review_agent,'config',return_value={'enabled':True,'key':'test'}),patch.object(main.review_agent,'model_events',side_effect=calls):
                    stream=client.post('/api/workbench/projects/DEMO-REVIEW/agent/chat',json={'message':'查找实施主体','run_id':single['id']})
                events=[json.loads(s) for s in stream.text.splitlines()]
                evidence=next(e for e in events if e['type']=='evidence')
                self.assertEqual(evidence['retrieval']['status'],'complete')
                self.assertTrue(any('semantic' in e['retrieval_sources'] for e in evidence['items']))
                self.assertGreaterEqual(model.json.call_count,3)


if __name__=='__main__':unittest.main()

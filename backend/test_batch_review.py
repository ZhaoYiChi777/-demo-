import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import os
os.environ.setdefault('LLM_RETRIEVAL_ENABLED', 'false')
import main
from test_accounts_support import login


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.paths=[patch.object(main,'DB_PATH',root/'test.db'),patch.object(main,'DATA_DIR',root),patch.object(main,'UPLOAD_DIR',root/'uploads')]
        for p in self.paths:p.start()
        self.client=TestClient(main.app);self.client.__enter__()
        login(self.client)
        self.batch=self.client.post('/api/workbench/batches',json={'name':'测试项目组','brief':'组内共用验收目标','items':[{'name':'报告甲','filename':'a.txt'},{'name':'报告乙','filename':'b.txt'}]}).json()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for p in reversed(self.paths):p.stop()
        self.temp.cleanup()

    def upload(self,index,text):
        pid=self.batch['items'][index]['item_id']
        return self.client.post(f"/api/workbench/batches/{self.batch['id']}/items/{pid}/upload",files={'file':('report.txt',text.encode(),'text/plain')})

    def review_item(self,index):
        pid=self.batch['items'][index]['item_id']
        return self.client.post(f"/api/workbench/batches/{self.batch['id']}/items/{pid}/run")

    def test_one_project_independent_report_evidence_and_latest_scoped_runs(self):
        ids=[i['project_id'] for i in self.batch['items']]
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(len(self.client.get('/api/projects').json()['projects']),1)
        self.assertNotEqual(self.batch['items'][0]['material_package_id'],self.batch['items'][1]['material_package_id'])
        runs=[]
        for i,name in enumerate(['报告甲','报告乙']):
            self.assertEqual(self.upload(i,f'项目名称：{name}。建设内容：更新电气设备，提高运行可靠性。投资估算：100万元。验收要求：完成调试。').status_code,200)
            response=self.review_item(i);self.assertEqual(response.status_code,200)
            run=response.json();runs.append(run)
            self.assertIn(name,run['chunks'][0]['text'])
            self.assertEqual(len(run['sources']),1)
            self.assertEqual(run['brief'],'组内共用验收目标')
            self.assertEqual(run['scope_name'],name)
            self.assertEqual(run['material_package_id'],self.batch['items'][i]['material_package_id'])
        self.assertNotIn('报告甲',runs[1]['chunks'][0]['text'])
        latest=self.review_item(0).json()
        whole=self.client.post(f'/api/workbench/projects/{ids[0]}/runs',json={}).json()
        rows=self.client.get('/api/workbench/project-comparison',params={'batch_id':self.batch['id']}).json()['rows']
        self.assertEqual(rows[0]['run']['id'],latest['id'])
        self.assertNotEqual(rows[0]['run']['id'],whole['id'])
        self.assertEqual(rows[1]['run']['id'],runs[1]['id'])
        self.assertEqual(len(rows),2)
        self.assertEqual(len(self.client.get(f'/api/projects/{ids[0]}/material-packages').json()['material_packages']),2)

    def test_failure_does_not_block_other_report_and_retry_keeps_identity(self):
        self.assertEqual(self.upload(0,'').status_code,409)
        self.assertEqual(self.upload(1,'项目乙建设内容：更新控制系统。投资估算：100万元。项目概况：老旧设备改造。').status_code,200)
        self.assertEqual(self.review_item(1).status_code,200)
        rows=self.client.get('/api/workbench/project-comparison',params={'batch_id':self.batch['id']}).json()['rows']
        self.assertEqual(rows[0]['project']['batch_status'],'error')
        self.assertEqual(rows[1]['project']['batch_status'],'completed')
        self.assertEqual(self.upload(0,'项目甲建设内容：更换电气设备。项目概况：设备改造。投资估算：50万元。').status_code,200)
        self.assertEqual(self.review_item(0).status_code,200)
        self.assertEqual(len(self.client.get('/api/projects').json()['projects']),1)
        self.assertEqual(len(self.client.get('/api/workbench/project-comparison',params={'batch_id':self.batch['id']}).json()['rows']),2)

    def test_report_specialty_does_not_follow_last_uploaded_report(self):
        self.upload(0,'汽轮机汽机疏水管道建设必要性：设备老化。技术方案：更换管道。投资估算：100万元。')
        self.upload(1,'电气配电柜变压器建设必要性：设备老化。技术方案：更换开关。投资估算：20万元。')
        pid=self.batch['project_id']
        # Pin known document assessments so this test checks scope selection, not keyword heuristics.
        with main.db() as conn:
            for item,specialty in zip(self.batch['items'],['TURB','ELEC']):
                source=conn.execute('SELECT s.document_id FROM document_sources s JOIN project_documents pd ON pd.source_id=s.id WHERE pd.material_package_id=?',(item['material_package_id'],)).fetchone()
                conn.execute('UPDATE specialty_assessments SET primary_specialty=? WHERE document_id=?',(specialty,source['document_id']))
            conn.execute("UPDATE projects SET specialty='ELEC' WHERE id=?",(pid,))
        for i,specialty in enumerate(['TURB','ELEC']):
            run=self.review_item(i).json()
            self.assertEqual(run['project']['specialty'],specialty)
        first=self.client.get('/api/workbench/rules',params={'project_id':pid,'material_package_id':self.batch['items'][0]['material_package_id']}).json()['rules']
        second=self.client.get('/api/workbench/rules',params={'project_id':pid,'material_package_id':self.batch['items'][1]['material_package_id']}).json()['rules']
        self.assertTrue(any(r['specialty']=='汽机' for r in first))
        self.assertFalse(any(r['specialty']=='汽机' for r in second))

    def test_attachment_stays_in_selected_report_without_creating_another_group(self):
        for index, name in enumerate(['报告甲', '报告乙']):
            self.assertEqual(self.upload(index, f'项目名称：{name}。建设必要性：设备老化。技术方案：更换汽轮机阀门。投资估算：100万元。').status_code, 200)
        project_id = self.batch['project_id']
        package_id = self.batch['items'][0]['material_package_id']
        response = self.client.post(f'/api/projects/{project_id}/documents',
            data={'material_package_id': str(package_id)},
            files={'file': ('补充可研报告.txt', '项目名称：另一个名称。建设必要性：附件验证。技术方案：附件独有标记。投资估算：10万元。'.encode(), 'text/plain')})
        self.assertEqual(response.status_code, 200, response.text)
        packages = self.client.get(f'/api/projects/{project_id}/material-packages').json()['material_packages']
        self.assertEqual(len(packages), 2)
        first = self.client.get(f'/api/workbench/projects/{project_id}/materials', params={'material_package_id': package_id}).json()
        second = self.client.get(f'/api/workbench/projects/{project_id}/materials', params={'material_package_id': self.batch['items'][1]['material_package_id']}).json()
        self.assertIn('补充可研报告.txt', [s['filename'] for s in first['sources']])
        self.assertNotIn('补充可研报告.txt', [s['filename'] for s in second['sources']])
        self.assertTrue(any('附件独有标记' in c['text'] for c in first['chunks']))
        self.assertFalse(any('附件独有标记' in c['text'] for c in second['chunks']))

    def test_explicit_report_upload_groups_a_file_without_report_keywords(self):
        project_id = self.batch['project_id']
        response = self.client.post(f'/api/projects/{project_id}/documents',
            data={'document_type': 'project_report'}, files={'file': ('01.txt', '运行情况说明：设备保持正常。'.encode(), 'text/plain')})
        self.assertEqual(response.status_code, 200, response.text)
        sources = self.client.get(f'/api/projects/{project_id}/sources').json()['source_files']
        self.assertEqual(sources[0]['document_category'], 'project_report')
        self.assertIsNotNone(sources[0]['material_package_id'])

    def test_attachment_rejects_a_report_outside_the_current_project_before_writing(self):
        other = self.client.post('/api/workbench/batches', json={'name': '另一个项目组', 'items': [{'name': '其他报告', 'filename': 'other.txt'}]}).json()
        project_id = self.batch['project_id']
        for package_id in [other['items'][0]['material_package_id'], 999999]:
            response = self.client.post(f'/api/projects/{project_id}/documents',
                data={'material_package_id': str(package_id)}, files={'file': ('附件.txt', b'attachment', 'text/plain')})
            self.assertEqual(response.status_code, 422, response.text)
        sources = self.client.get(f'/api/projects/{project_id}/sources').json()['source_files']
        self.assertEqual(sources, [])

    def test_legacy_batch_remains_readable(self):
        login(self.client, admin=True)
        pid=self.batch['project_id']
        with main.db() as conn:
            conn.execute("INSERT INTO wb_batches(id,name,created_at) VALUES('legacy','旧批次','2026-09-14')")
            conn.execute("INSERT INTO wb_batch_items(batch_id,project_id,filename,position,status) VALUES('legacy',?,'旧报告.doc',0,'waiting')",(pid,))
        old=self.client.get('/api/workbench/project-comparison',params={'batch_id':'legacy'}).json()['rows']
        self.assertEqual(old[0]['project']['id'],pid)
        batches=self.client.get('/api/workbench/batches').json()['batches']
        self.assertIsNone(next(b for b in batches if b['id']=='legacy')['project_id'])


if __name__=='__main__':unittest.main()

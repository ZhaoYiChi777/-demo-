"""Isolated API and browser checks for accounts, ownership and the admin workspace."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
API = 'http://127.0.0.1:18000'
WEB = 'http://127.0.0.1:18080'
OUTPUT = ROOT / 'artifacts' / 'admin-workspace-20261020'
SERVER = '''
import os
from pathlib import Path
import main, uvicorn
main.DB_PATH=Path(os.environ['WORKBENCH_TEST_DB'])
main.DATA_DIR=main.DB_PATH.parent
main.UPLOAD_DIR=main.DATA_DIR/'uploads'
main.UPLOAD_DIR.mkdir()
main.model_settings.defaults=lambda:dict(enabled=False,key='',model='test',url='http://127.0.0.1:18000/mock')
main.review_agent.config=lambda:dict(enabled=True,key='test',model='test')
def model_events(items):
    yield {'type':'response.output_text.delta','delta':'请核对当前报告的原文依据。'}
main.review_agent.model_events=model_events
@main.app.on_event('startup')
def seed():
    with main.db() as conn:
        for i in range(25):
            conn.execute('INSERT INTO projects(id,name,specialty,project_type,created_at) VALUES(?,?,?,?,?)',
                (f'LEGACY-{i}',f'历史汽机项目{i:02}','TURB','技术改造',f'2026-10-{i+1:02}T08:00:00+00:00'))
        conn.execute('INSERT INTO wb_batches(id,name,created_at) VALUES(?,?,?)',('LEGACY-GROUP','旧版项目组','2026-10-01T08:00:00+00:00'))
        conn.execute('INSERT INTO wb_batch_items(batch_id,project_id,filename,position,status) VALUES(?,?,?,?,?)',('LEGACY-GROUP','LEGACY-0','旧版材料.txt',0,'waiting'))
    main.accounts.import_history(force=True)
uvicorn.run(main.app,host='127.0.0.1',port=18000,log_level='error')
'''


def wait_ready(url):
    for _ in range(150):
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(.1)
    raise RuntimeError(f'Service not ready: {url}')


def login(client, username, password):
    response = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.text


def browser_login(page, username, password):
    form = page.locator('#account-login-form')
    form.locator('[name="username"]').fill(username)
    form.locator('[name="password"]').fill(password)
    form.get_by_role('button', name='登录', exact=True).click()
    page.wait_for_function('window.currentAccount && !document.documentElement.classList.contains("auth-pending")')
    page.wait_for_function('document.querySelector("#connection-state").textContent.includes("已连接")')


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        env = {**os.environ, 'WORKBENCH_TEST_DB': str(Path(temp) / 'test.db'), 'LLM_ENABLED': 'false', 'LLM_RETRIEVAL_ENABLED': 'false'}
        servers = []
        try:
            servers.append(subprocess.Popen([sys.executable, '-c', SERVER], cwd=ROOT / 'backend', env=env))
            servers.append(subprocess.Popen([sys.executable, 'scripts/serve_frontend.py', '--port', '18080'], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_ready(API + '/health'); wait_ready(WEB)
            admin = httpx.Client(base_url=API)
            user = httpx.Client(base_url=API)
            assert user.get('/api/projects').status_code == 401
            assert user.get('/api/admin/records').status_code == 401
            assert user.post('/api/auth/login', json={'username': 'manner', 'password': 'wrong'}).status_code == 401
            login(admin, 'manner', '00000000'); login(user, 'user1', '12345678')
            assert len(admin.get('/api/projects').json()['projects']) == 25
            assert user.get('/api/projects').json()['projects'] == []
            assert user.get('/api/projects/LEGACY-0').status_code == 403
            assert user.get('/api/admin/accounts').status_code == 403
            assert user.get('/api/analytics/overview').status_code == 403
            assert user.put('/api/models', json={'model': 'test'}).status_code == 403
            assert user.post('/api/workbench/rules', json={}).status_code == 403
            assert admin.post('/api/projects', data={'name': 'forbidden'}).status_code == 403
            for resource in ['/backend/data/review.db', '/.env', '/backend/data/.account-key', '/.git/config']:
                assert httpx.get(WEB + resource).status_code == 404
            # Project ownership is assigned only after a successful create.
            project = user.post('/api/projects', data={'name': '账户测试项目02', 'project_type': '技术改造'}).json()
            project_id = project.get('project_id') or project['id']
            assert user.get('/api/projects').json()['projects'][0]['owner'] == 'user1'
            assert admin.put('/api/admin/projects/LEGACY-0/owner', json={'username': 'user1'}).status_code == 200
            assert user.get('/api/projects/LEGACY-0').status_code == 200
            assert user.get('/api/projects/LEGACY-1').status_code == 403
            material = '项目名称：账户测试项目02\n建设单位：测试电厂\n机组编号：1号\n建设必要性：设备老化。\n技术方案：更换汽轮机阀门并配置安全保护。\n投资估算：100万元。'
            response = user.post(f'/api/projects/{project_id}/documents', files={'file': ('测试材料02.txt', material.encode(), 'text/plain')})
            assert response.status_code == 200, response.text
            sources = user.get(f'/api/workbench/projects/{project_id}/materials').json()['sources']
            for source in sources:
                response = user.patch(f'/api/projects/{project_id}/sources/{source["id"]}/category', json={'category': 'project_report'})
                assert response.status_code == 200, response.text
            user.put(f'/api/workbench/projects/{project_id}/brief', json={'text': '测试任务要求'})
            run_response = user.post(f'/api/workbench/projects/{project_id}/runs', json={'material_package_id': None}, timeout=60)
            assert run_response.status_code == 200, run_response.text
            run = run_response.json(); run_id = run['id']
            assert run['result'] != 'pass'
            assert admin.get('/api/admin/records?category=reviews&report_state=issues').json()['total'] == 1
            assert admin.get(f'/api/workbench/runs/{run_id}').status_code == 200
            assert admin.post(f'/api/workbench/runs/{run_id}/qualify').status_code == 403
            check = run['checks'][0]
            response = user.patch(f'/api/workbench/runs/{run_id}/checks/{check["id"]}', json={'result': 'pass', 'reason': '人工核对字段完整', 'suggestion': '保留原文依据'})
            assert response.status_code == 200, response.text
            response = user.post(f'/api/workbench/projects/{project_id}/agent/chat', json={'message': '核对当前报告', 'run_id': run_id, 'material_package_id': None}, timeout=30)
            assert response.status_code == 200 and 'done' in response.text, response.text
            assert user.post(f'/api/workbench/runs/{run_id}/archive').status_code == 200
            group_response = user.post('/api/workbench/batches', json={'name': '账号测试组', 'items': [{'name': '组内报告02', 'filename': '报告02.txt'}]})
            assert group_response.status_code == 200, group_response.text
            group = group_response.json()
            assert user.get('/api/workbench/batches').json()['batches'][0]['id'] == group['id']
            assert admin.put(f'/api/admin/projects/{project_id}/owner', json={'username': None}).status_code == 200
            assert user.get(f'/api/workbench/runs/{run_id}').status_code == 403
            assert user.get(f'/api/workbench/runs/{run_id}/report.docx').status_code == 403
            assert user.post(f'/api/workbench/projects/LEGACY-0/agent/chat', json={'message': '检查', 'run_id': run_id}).status_code == 403
            assert admin.put(f'/api/admin/projects/{project_id}/owner', json={'username': 'user1'}).status_code == 200
            records = admin.get('/api/admin/records').json()
            assert records['total'] > 20 and len(records['records']) == 20
            second = admin.get('/api/admin/records?page=2').json()
            assert {r['id'] for r in records['records']}.isdisjoint(r['id'] for r in second['records'])
            assert all(r['username'] == 'user1' for r in admin.get('/api/admin/records?username=user1').json()['records'])
            assert all(r['project_id'] == project_id for r in admin.get(f'/api/admin/records?project_id={project_id}').json()['records'])
            assert admin.get('/api/admin/records?query=ee').json()['total'] == 0
            assert admin.get('/api/admin/records?query=账户 测试').json()['total'] > 0
            assert admin.get('/api/admin/records?query=历史汽机项目04&after=2026-10-05&before=2026-10-05').json()['total'] == 1
            assert admin.get('/api/admin/records?query=历史汽机项目04&after=2026-10-06').json()['total'] == 0
            for category in ['materials', 'reviews', 'manual', 'conversations', 'system']:
                result = admin.get(f'/api/admin/records?category={category}').json()
                assert result['total'] > 0, category
                assert all(r['category'] == category for r in result['records'])
            manual = admin.get('/api/admin/records?category=manual').json()['records']
            changed = next(r for r in manual if r['action'] == '复核检查项')
            assert admin.get(f'/api/admin/records/{changed["id"]}').json()['detail']['changes']
            rule = admin.get('/api/workbench/rules').json()['rules'][0]
            rule.update(content=rule['content'] + ' 管理员修改记录验证')
            assert admin.put(f'/api/workbench/rules/{rule["id"]}', json=rule).status_code == 200
            updated = next(r for r in admin.get('/api/admin/records?category=system').json()['records'] if r['action'] == '更新评审规则')
            assert admin.get(f'/api/admin/records/{updated["id"]}').json()['detail']['before']['content'] != rule['content']
            assert admin.patch('/api/admin/accounts/user1', json={'enabled': False}).status_code == 200
            assert user.get('/api/auth/me').status_code == 401
            assert user.post('/api/auth/login', json={'username': 'user1', 'password': '12345678'}).status_code == 401
            assert admin.patch('/api/admin/accounts/user1', json={'enabled': True, 'password': '87654321'}).status_code == 200
            assert user.post('/api/auth/login', json={'username': 'user1', 'password': '12345678'}).status_code == 401
            login(user, 'user1', '87654321')
            admin.patch('/api/admin/accounts/user1', json={'password': '12345678'})
            assert admin.patch('/api/admin/accounts/manner', json={'enabled': False}).status_code == 409
            # Browser checks use new contexts, so API sessions cannot mask login bugs.
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, timezone_id='Asia/Singapore')
                context.add_init_script(f'window.REVIEW_API_BASE="{API}"')
                page = context.new_page()
                page.set_default_timeout(15000)
                errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(WEB, wait_until='networkidle')
                page.locator('#account-login[open]').wait_for()
                page.screenshot(path=str(OUTPUT/'login.png'))
                browser_login(page, 'manner', '00000000')
                page.wait_for_url('**/admin.html#overview')
                page.locator('.admin-metrics').wait_for()
                assert page.locator('.sidebar nav button').count() == 6
                page.screenshot(path=str(OUTPUT/'overview.png'), full_page=True)
                page.locator('.sidebar [data-view="reports"]').click()
                page.locator('.admin-record-entries').wait_for()
                assert page.locator('.admin-record-entries .entry-card').count() == 6
                page.screenshot(path=str(OUTPUT/'records.png'), full_page=True)
                page.locator('[data-view="reports/all"]').click()
                page.locator('.admin-record-table tbody tr').first.wait_for()
                assert page.locator('.admin-record-table tbody tr').count() == 20
                page.get_by_role('button', name='下一页', exact=True).click()
                page.wait_for_function('adminUI.records.page===2 && !adminUI.loading')
                page.locator('#admin-records-filter [name="query"]').fill('ee')
                page.locator('#admin-records-filter [type="submit"]').click()
                page.wait_for_function('adminUI.records.total===0 && !adminUI.loading')
                page.get_by_role('button', name='重置', exact=True).click()
                page.wait_for_function('adminUI.records.total>20 && !adminUI.loading')
                page.locator('.sidebar [data-view="projects"]').click()
                page.locator('[data-view="projects/groups"]').click()
                page.wait_for_function('!adminUI.loading && adminUI.kind==="groups"')
                page.locator('[data-view="projects/legacy/LEGACY-GROUP"]').click()
                page.wait_for_function('!adminUI.loading && adminUI.kind==="legacy"')
                assert page.get_by_role('button',name='查看项目',exact=True).count()==1
                page.locator('.sidebar [data-view="projects"]').click()
                page.locator('[data-view="projects/single"]').click()
                page.wait_for_function('!adminUI.loading && adminUI.kind==="single"')
                page.locator('#admin-projects-filter [name="query"]').fill('账户测试项目02')
                page.locator('#admin-projects-filter [type="submit"]').click()
                page.get_by_role('button', name='查看', exact=True).click()
                page.wait_for_function('adminUI.detail && !adminUI.loading')
                assert page.get_by_role('button', name='查看报告', exact=True).count() == 1
                page.get_by_role('button', name='查看报告', exact=True).click()
                page.locator('#editor .report-paper').wait_for()
                assert page.locator('#editor [data-archive],#editor [data-qualify-run]').count() == 0
                page.screenshot(path=str(OUTPUT/'report.png'), full_page=True)
                with page.expect_download() as download:
                    page.get_by_role('button', name='导出 Word', exact=True).click()
                assert download.value.suggested_filename.endswith('.docx')
                page.get_by_role('button', name='关闭', exact=True).click()
                page.locator('.sidebar [data-view="rules"]').click()
                page.locator('#admin-rule-project').select_option(project_id)
                page.wait_for_function('(pid)=>state.project?.id===pid',arg=project_id)
                page.locator('[data-new-rule]').click()
                page.locator('#rule-form [name="name"]').fill('项目专用规则验证')
                page.locator('#rule-form [name="standard"]').fill('应保留本项目验收记录。')
                page.locator('#rule-form [name="content"]').fill('核对本项目验收记录与材料。')
                page.locator('#rule-form [type="submit"]').click()
                page.wait_for_function('(pid)=>state.rules.some(r=>r.name==="项目专用规则验证"&&r.project_id===pid)',arg=project_id)
                page.locator('.sidebar [data-view="accounts"]').click()
                page.wait_for_function('state.view==="accounts" && !adminUI.loading')
                page.screenshot(path=str(OUTPUT/'accounts.png'), full_page=True)
                other = page.context.new_page()
                other.goto(WEB+'/admin.html#accounts',wait_until='networkidle')
                other.wait_for_function('window.currentAccount?.role==="admin"')
                page.get_by_label('切换账号（右上角）').select_option('user1')
                page.locator('#account-login[open]').wait_for()
                assert page.url.endswith('#accounts')
                page.get_by_role('button', name='取消切换', exact=True).click()
                assert page.locator('#account-login[open]').count() == 0
                page.get_by_label('切换账号（左下角）').select_option('user1')
                browser_login(page, 'user1', '12345678')
                page.wait_for_url('**/index.html#overview')
                other.wait_for_url('**/index.html#overview')
                other.close()
                page.wait_for_function('userTasks.loaded && !userTasks.loading')
                assert page.evaluate('state.projects.every(p=>p.owner==="user1")')
                page.goto(WEB+'/admin.html',wait_until='networkidle')
                page.wait_for_url('**/index.html#overview')
                page.get_by_label('切换账号（右上角）').select_option('logout')
                page.locator('#account-login[open]').wait_for()
                assert not errors, errors
                browser.close()
            admin.close(); user.close()
            result={'result': 'PASS', 'checks': 'accounts, ownership, role boundaries, records, date/keyword search, pagination, legacy groups, reports, login, cross-tab switching', 'screenshots': str(OUTPUT)}
            (OUTPUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False))
        finally:
            for server in servers:
                server.terminate()
            for server in servers:
                server.wait(timeout=10)


if __name__ == '__main__':
    main()

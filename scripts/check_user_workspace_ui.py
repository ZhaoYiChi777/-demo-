"""Exercise account switching, nested lists, search and real report flows in a temporary DB."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from docx import Document
from playwright.sync_api import sync_playwright
from check_admin_workspace import browser_login

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'artifacts'/'user-workspace-20261021'
API='http://127.0.0.1:18000'
WEB='http://127.0.0.1:18080'
SERVER='''
import os
from pathlib import Path
import main,uvicorn
main.DB_PATH=Path(os.environ['WORKBENCH_TEST_DB'])
main.DATA_DIR=main.DB_PATH.parent
main.UPLOAD_DIR=main.DATA_DIR/'uploads'
main.UPLOAD_DIR.mkdir()
main.model_settings.defaults=lambda:dict(enabled=False,key='',model='test',url='http://127.0.0.1:18000/mock')
main.review_agent.config=lambda:dict(enabled=True,key='test',model='test')
def model_events(items):
    yield {'type':'response.output_text.delta','delta':'请核对当前报告的原文依据，并补充待确认项所需材料。'}
main.review_agent.model_events=model_events
@main.app.post('/test/demo')
def seed_demo():
    from accounts import actor
    context=actor.set(None)
    try:
        demo=main.workbench.demo()
    finally:
        actor.reset(context)
    main.accounts.claim(demo['project_id'])
    return demo
@main.app.on_event('startup')
def seed_projects():
    with main.db() as conn:
        for index in range(1,13):
            conn.execute('INSERT INTO projects(id,name,specialty,project_type,status,created_at) VALUES(?,?,?,?,?,?)',
                (f'UI-{index:02}',f'历史项目{index:02}','汽机','技术改造','uploaded',f'2026-10-{index:02}T08:00:00+00:00'))
            conn.execute('INSERT INTO project_owners VALUES(?,?)',(f'UI-{index:02}','user1'))
    return {'count':12}
uvicorn.run(main.app,host='127.0.0.1',port=18000,log_level='error')
'''


def wait_ready(url):
    for _ in range(100):
        try:
            urllib.request.urlopen(url,timeout=1).close()
            return
        except OSError:
            time.sleep(.1)
    raise RuntimeError(f'Service did not start: {url}')


def text_file(name,text):
    return {'name':name,'mimeType':'text/plain','buffer':text.encode()}


def screenshot(page,name):
    page.evaluate("window.scrollTo(0,0);document.querySelector('#toast').classList.remove('show')")
    page.screenshot(path=str(OUTPUT/name),full_page=True)


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        env={**os.environ,'WORKBENCH_TEST_DB':str(Path(temp)/'test.db'),'LLM_ENABLED':'false','LLM_RETRIEVAL_ENABLED':'false'}
        servers=[]
        try:
            servers.append(subprocess.Popen([sys.executable,'-c',SERVER],cwd=ROOT/'backend',env=env))
            servers.append(subprocess.Popen([sys.executable,'scripts/serve_frontend.py','--port','18080'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            wait_ready(API+'/health');wait_ready(WEB)
            with sync_playwright() as p:
                browser=p.chromium.launch()
                page=browser.new_page(viewport={'width':1440,'height':1000},timezone_id='Asia/Singapore')
                page.set_default_timeout(15000)
                page.add_init_script(f"window.REVIEW_API_BASE='{API}'")
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(WEB,wait_until='networkidle')
                browser_login(page,'user1','12345678')
                page.wait_for_function('userTasks.loaded && !userTasks.loading')
                assert page.locator('[data-account-switch]').count()==2
                assert page.locator('[data-account-switch]').first.input_value()=='user1'
                assert page.locator('.task-entry-grid .entry-card').count()==7
                assert page.locator('.user-task').count()==0
                assert page.locator('.page-heading p').count()==0
                page.get_by_label('切换账号（右上角）').select_option('manner')
                browser_login(page,'manner','00000000')
                page.wait_for_url('**/admin.html#overview')
                page.wait_for_function('state.projects.length===12')
                assert page.locator('[data-account-switch]').first.input_value()=='manner'
                assert page.locator('.sidebar nav button').count()==6
                page.get_by_label('切换账号（左下角）').select_option('user1')
                browser_login(page,'user1','12345678')
                page.wait_for_url('**/index.html#overview')
                page.wait_for_function('userTasks.loaded && !userTasks.loading')
                assert page.evaluate("localStorage.getItem('review-workbench-account')")=='user1'

                page.locator('[data-view="overview/unreviewed"]').click()
                page.wait_for_function('!userTasks.loading')
                assert page.locator('.user-task').count()==8
                first_tasks=page.locator('.user-task h2').all_text_contents()
                page.get_by_role('button',name='下一页',exact=True).click()
                assert page.locator('.user-task').count()==4
                assert set(first_tasks).isdisjoint(page.locator('.user-task h2').all_text_contents())
                page.locator('#user-tasks-query').fill('历史项目05')
                assert page.locator('.user-task').count()==1
                assert page.evaluate('userUI.pages.tasks')==1
                page.reload(wait_until='networkidle')
                page.wait_for_function('!userTasks.loading && userTasks.loaded')
                assert page.evaluate('userUI.taskCategory')=='unreviewed'
                assert page.locator('.user-task').count()==8
                page.locator('.sidebar [data-view=overview]').click()
                page.wait_for_function('!userTasks.loading')
                screenshot(page,'01-tasks.png')

                page.locator('.sidebar [data-view=batch]').click()
                page.wait_for_function('!projectBatch.loading')
                assert page.locator('.project-entry-grid .entry-card').count()==2
                assert page.locator('.project-record').count()==0
                screenshot(page,'02-project-entry.png')
                page.locator('[data-view="batch/single"]').click()
                page.wait_for_function('!projectBatch.loading')
                assert page.locator('.project-record').count()==8
                page.go_back(wait_until='networkidle')
                page.wait_for_function('userUI.projectMode===null && !projectBatch.loading')
                assert page.locator('.project-entry-grid .entry-card').count()==2
                page.go_forward(wait_until='networkidle')
                page.wait_for_function('userUI.projectMode==="single" && !projectBatch.loading')
                page.locator('#user-singles-after').fill('2026-10-05')
                page.locator('#user-singles-before').fill('2026-10-05')
                assert page.locator('.project-record').count()==1
                assert '历史项目05' in page.locator('.project-record').inner_text()
                assert '建立时间' in page.locator('.project-record').inner_text()
                page.locator('#user-singles-query').fill('项目05')
                assert page.locator('.project-record').count()==1
                page.locator('#user-singles-query').fill('不存在')
                assert page.locator('.project-record').count()==0
                page.locator('[data-user-clear=singles]').click()
                screenshot(page,'03-single-projects.png')

                page.locator('[data-single-report]').click()
                assert page.locator('#single-report-form .brief-purpose').is_visible()
                assert '任务目标、交付成果和验收要求' in page.locator('#single-report-form .brief-purpose').inner_text()
                page.locator('[name=report]').set_input_files(text_file('独立可研报告.txt','项目名称：独立报告。建设单位：测试公司。机组：一号机组。建设必要性：设备升级。技术方案：更换设备。投资估算：100万元。'))
                page.locator('#single-report-form [type=submit]').click()
                page.wait_for_function('state.view==="review" && state.run && !state.busy')
                standalone_id=page.evaluate('state.project.id')
                page.locator('.sidebar [data-view=batch]').click()
                page.wait_for_function('!projectBatch.loading')
                page.locator('[data-view="batch/single"]').click()
                page.wait_for_function('!projectBatch.loading')
                page.locator('#user-singles-query').fill('独立可研')
                assert page.locator('.project-record').count()==1
                page.locator(f'[data-user-materials="{standalone_id}"]').click()
                page.locator('#brief-form .brief-purpose').wait_for(state='visible')
                assert '核对待审报告是否满足课题要求' in page.locator('#brief-form .brief-purpose').inner_text()
                assert page.get_by_role('heading',name='报告与附件',exact=True).is_visible()
                assert page.locator('#package-name,[data-new-package],[data-source-package],[data-save-package]').count()==0
                package_count=page.evaluate('state.packages.length')
                attachment_package=page.locator('[data-add-attachment]').first.get_attribute('data-add-attachment')
                page.locator('[data-add-attachment]').first.click()
                assert page.locator('#attachment-form [name=material_package_id]').input_value()==attachment_package
                page.locator('#attachment-form [name=attachment]').set_input_files(text_file('补充可研附件.txt','项目名称：附件名称不同。建设必要性：附加依据。技术方案：附件专属测试标记。投资估算：20万元。'))
                page.locator('#attachment-form [type=submit]').click()
                page.locator('#attachment-form').wait_for(state='hidden')
                assert page.evaluate('state.packages.length')==package_count
                assert page.evaluate("({pid})=>state.materials.sources.some(s=>s.filename==='补充可研附件.txt'&&String(s.material_package_id)===pid)",{'pid':attachment_package})
                page.locator(f'[data-run-package="{attachment_package}"]').click()
                page.wait_for_function('state.view==="review" && !state.busy')
                assert page.evaluate("pid=>String(state.run.material_package_id)===pid",attachment_package)
                assert page.evaluate("state.run.sources.some(s=>s.filename==='补充可研附件.txt')")
                page.evaluate("navigate('projects')")
                page.locator('#brief-form').wait_for()
                page.locator('[data-view="batch/single"]').click()
                page.wait_for_function('!projectBatch.loading')
                page.locator(f'[data-view="batch/project/{standalone_id}"]').click()
                page.wait_for_function('!userProjectHistory.loading')
                assert page.locator('.history-record').count()==2
                page.reload(wait_until='networkidle')
                page.wait_for_function('!userProjectHistory.loading && userProjectHistory.runs.length===2')
                page.locator('.history-summary').first.click()
                page.wait_for_function('state.view==="reports" && expandedReport')
                assert page.locator('.report-paper').is_visible()
                page.locator('.user-score-analysis > summary').click()
                assert page.locator('.user-score-analysis .score-total').is_visible()
                assert page.locator('.user-score-analysis .score-dimension').count()>0
                with page.expect_download() as download:
                    page.locator('[data-export]').click()
                download.value.save_as(OUTPUT/'review-report.docx')
                doc=Document(io.BytesIO((OUTPUT/'review-report.docx').read_bytes()))
                assert any('独立可研报告' in para.text for para in doc.paragraphs)

                demo=page.evaluate("async()=>await api('/test/demo','POST')")
                original_run=demo['run']['id']
                page.evaluate(f"openUserRun('{original_run}')")
                page.locator('.user-score-analysis > summary').click()
                assert page.locator('.user-result .user-score-value').is_visible()
                assert page.locator('.user-score-analysis .score-total').is_visible()
                page.locator('[data-check=DEMO-004]').click()
                assert page.locator('[data-edit-rule]').count()==0
                page.locator('[data-adjudicate=DEMO-004]').click()
                page.locator('#editor [name=result]').select_option('fail')
                page.locator('#editor [name=reason]').fill('人工核对：本次报告缺少明确的防火分隔依据。')
                page.locator('#editor [name=suggestion]').fill('请补充防火分隔设计说明。')
                page.locator('#check-form [type=submit]').click()
                page.locator('#editor').wait_for(state='hidden')
                newer=page.evaluate("async()=>{const rows=[];for(let i=0;i<10;i++)rows.push(await api('/api/workbench/projects/DEMO-REVIEW/runs','POST',{}));await api('/api/workbench/runs/'+rows[0].id+'/qualify','POST');return rows.at(-1)}")
                page.evaluate(f"openUserRun('{original_run}','reports')")
                page.locator('[data-archive]').click()
                page.wait_for_function('expandedReport?.status==="archived"')
                assert page.evaluate('userUI.pages.reports')>1
                assert page.locator('.report-paper').is_visible()
                assert page.evaluate(f"async()=> (await api('/api/workbench/runs/{newer['id']}')).status")!='archived'
                page.emulate_media(media='print')
                assert page.locator('.report-paper').is_visible()
                assert not page.locator('.user-search-bar').is_visible()
                page.pdf(path=str(OUTPUT/'review-report.pdf'),format='A4',print_background=True)
                page.emulate_media(media='screen')

                page.locator('.sidebar [data-view=batch]').click()
                page.wait_for_function('!projectBatch.loading')
                page.locator('[data-batch-new]').click()
                assert page.locator('#batch-project-form .brief-purpose').is_visible()
                assert page.evaluate("document.getElementById('batch-files').compareDocumentPosition(document.querySelector('[data-brief-upload=common_brief]')) & Node.DOCUMENT_POSITION_FOLLOWING")
                page.locator('[name=batch_name]').fill('设备改造项目组')
                page.locator('#batch-files').set_input_files([
                    text_file('报告甲.txt','项目名称：报告甲。建设单位：测试公司。机组：一号机组。建设必要性：提升可靠性。技术方案：改造设备。投资估算：100万元。'),
                    text_file('报告乙.txt','')])
                page.locator('#batch-project-form [type=submit]').click()
                page.wait_for_function('!projectBatch.busy && projectBatch.progress.includes("处理结束") && projectBatch.rows.length===2 && projectBatch.rows.every(r=>r.project.item_id)',timeout=60000)
                assert page.evaluate('projectBatch.rows.filter(r=>r.project.batch_status==="error").length')==1
                page.locator('[data-batch-file]').set_input_files(text_file('报告乙修正.txt','项目名称：报告乙。建设单位：测试公司。机组：二号机组。建设必要性：提高效率。技术方案：更换设备。投资估算：80万元。'))
                page.locator('tbody tr').filter(has=page.locator('[data-batch-file]')).get_by_role('button',name='重试',exact=True).click()
                page.wait_for_function('!projectBatch.busy && projectBatch.rows.every(r=>r.run)',timeout=60000)
                assert page.evaluate('new Set(projectBatch.rows.map(r=>r.project.id)).size')==1
                assert page.evaluate('new Set(projectBatch.rows.map(r=>r.run.material_package_id)).size')==2
                assert page.locator('.user-group-table th').all_text_contents()[:3]==['排名','报告','评分']
                page.evaluate('''()=>{
                  const seed=structuredClone(projectBatch.rows[0]);
                  projectBatch.rows=Array.from({length:12},(_,index)=>{
                    const row=structuredClone(seed);
                    row.project.item_id='SCORE-'+String(index).padStart(2,'0');
                    row.project.report_name='排名样例'+String(index+1).padStart(2,'0');
                    row.run.id='SCORE-RUN-'+index;
                    row.run.created_at='2026-10-'+String(index+1).padStart(2,'0')+'T08:00:00Z';
                    row.run.quality_score={...row.run.quality_score,total:90-index*2,provisional:false,comparable:true,grade:'A'};
                    if(index===10)row.run.quality_score={...row.run.quality_score,total:95,provisional:true,comparable:false};
                    if(index===11)row.run=null;
                    return row;
                  });
                  projectBatch.sort='score';userUI.pages.groupReports=1;render();
                }''')
                assert page.locator('.user-rank').all_text_contents()==[str(i) for i in range(1,9)]
                assert page.locator('.user-group-table .user-score-value strong').first.inner_text()=='90.0'
                page.get_by_role('button',name='下一页',exact=True).click()
                assert page.locator('.user-rank').all_text_contents()==['9','10','—','—']
                assert '95.0' in page.locator('.user-group-table').inner_text()
                assert '暂计 · 不参与排名' in page.locator('.user-group-table').inner_text()
                page.locator('[data-batch-score="SCORE-08"]').click()
                assert page.locator('.user-score-analysis .score-total').is_visible()
                assert '74.0' in page.locator('.user-score-analysis .score-total').inner_text()
                assert '组内第 9 / 10 名' in page.locator('.user-score-rank').inner_text()
                screenshot(page,'04a-ranking-analysis.png')
                page.locator('#project-batch-sort').select_option('time')
                assert page.evaluate('userUI.pages.groupReports')==1
                assert page.locator('.user-rank').all_text_contents()[:3]==['—','10','9']
                page.locator('#project-batch-sort').select_option('score')
                page.locator('#user-groupReports-query').fill('排名样例10')
                assert page.locator('.user-rank').all_text_contents()==['10']
                page.locator('[data-user-clear=groupReports]').click()
                page.evaluate('loadProjectBatch()')
                page.wait_for_function('!projectBatch.loading && projectBatch.rows.length===2')
                page.locator('#user-groupReports-query').fill('报告甲')
                assert page.locator('[data-user-destination=reports]').count()==1
                page.locator('[data-user-clear=groupReports]').click()
                group=page.evaluate('projectBatch.batches.find(b=>b.id===projectBatch.batchId)')
                screenshot(page,'04-group-reports.png')
                page.locator('.page-heading [data-view="batch/groups"]').click()
                page.wait_for_function('!projectBatch.loading')
                page.locator('#user-groups-query').fill('设备改造')
                today=page.evaluate('localDay(new Date().toISOString())')
                page.locator('#user-groups-after').fill(today)
                page.locator('#user-groups-before').fill(today)
                assert page.locator('.project-record').count()==1
                page.locator('#user-groups-before').fill('2000-01-01')
                assert page.locator('.project-record').count()==0
                page.locator('[data-user-clear=groups]').click()
                page.locator(f'[data-user-group="{group["id"]}"]').click()
                page.wait_for_function('!projectBatch.loading')
                page.locator(f'[data-view="batch/project/{group["project_id"]}"]').click()
                page.wait_for_function('!userProjectHistory.loading')
                assert page.locator('.history-record').count()==2
                page.reload(wait_until='networkidle')
                page.wait_for_function('!userProjectHistory.loading && userProjectHistory.runs.length===2')
                assert page.locator(f'[data-view="batch/group/{group["id"]}"]').is_visible()

                page.locator('.sidebar [data-view=reports]').click()
                page.wait_for_function('Array.isArray(passedReports)')
                assert page.locator('.report-entry-grid .entry-card').count()==4
                assert page.locator('.history-record').count()==0
                screenshot(page,'05-report-entry.png')
                page.locator('#user-reports-query').fill('报告甲')
                page.locator('[data-view="reports/all"]').click()
                page.wait_for_function('Array.isArray(passedReports)')
                assert page.locator('.history-record').count()==1
                page.locator('[data-user-clear=reports]').click()
                assert page.locator('.history-record').count()==8
                first_reports=page.locator('[data-expand-report]').evaluate_all('rows=>rows.map(row=>row.dataset.expandReport)')
                page.get_by_role('button',name='下一页',exact=True).click()
                assert page.locator('.history-record').count()>0
                assert set(first_reports).isdisjoint(page.locator('[data-expand-report]').evaluate_all('rows=>rows.map(row=>row.dataset.expandReport)'))
                page.locator('#user-reports-query').fill('报告乙')
                assert page.locator('.history-record').count()==1
                assert page.evaluate('userUI.pages.reports')==1
                page.locator('[data-user-clear=reports]').click()
                screenshot(page,'06-report-list.png')
                page.locator('.page-heading [data-view=reports]').click()
                page.wait_for_function('Array.isArray(passedReports)')
                page.locator('[data-view="reports/archived"]').click()
                page.wait_for_function('Array.isArray(passedReports)')
                assert page.locator('.history-record').count()==2
                page.locator('.page-heading [data-view=reports]').click()
                page.wait_for_function('Array.isArray(passedReports)')
                page.locator('[data-view="reports/pass"]').click()
                page.wait_for_function('Array.isArray(passedReports)')
                assert page.locator('.history-record').count()==1

                page.locator('.sidebar [data-view=chat]').click()
                page.wait_for_function('!userUI.chatLoading')
                page.locator('[data-user-report-picker]').click()
                page.locator('#user-chat-query').fill('报告甲')
                assert page.locator('.report-option').count()==1
                page.locator('.report-option').click()
                page.wait_for_function('state.run?.scope_name==="报告甲" && !agentChat.loading && !userUI.chatLoading')
                assert page.evaluate('state.run.sources.length')==1
                page.locator('#review-agent').wait_for(state='visible')
                assert page.locator('#user-chat-project,#user-chat-scope').count()==0
                page.locator('[data-user-report-picker]').click()
                page.locator('#user-chat-query').fill('报告乙')
                page.locator('.report-option').click()
                page.wait_for_function('state.run?.scope_name==="报告乙" && !agentChat.loading && !userUI.chatLoading')
                page.locator('#review-agent').wait_for(state='visible')
                assert page.evaluate('state.run.sources.filter(source=>source.parse_status==="parsed").length')==1
                assert page.evaluate('state.run.chunks.every(chunk=>!chunk.text.includes("报告甲"))')
                page.locator('[data-user-report-picker]').click()
                page.locator('#user-chat-query').fill('报告甲')
                page.locator('.report-option').click()
                page.wait_for_function('state.run?.scope_name==="报告甲" && !agentChat.loading && !userUI.chatLoading')
                page.locator('#review-agent').wait_for(state='visible')
                assert page.locator('.agent-panel .panel-head p,.agent-welcome p,#agent-chat-form small').count()==0
                page.locator('#agent-message').fill('本报告还需要补充什么？')
                page.locator('#agent-chat-form [type=submit]').click()
                page.wait_for_function('!agentChat.busy && agentChat.messages.length===2')
                assert '补充待确认项' in page.locator('.agent-answer').last.inner_text()
                screenshot(page,'07-chat.png')
                page.reload(wait_until='networkidle')
                page.wait_for_function('!agentChat.loading && agentChat.messages.length===2')
                assert page.evaluate('state.run.scope_name')=='报告甲'

                page.set_viewport_size({'width':390,'height':844})
                for route in ('overview','overview/unreviewed','batch',f'batch/group/{group["id"]}','batch/single','projects','review','reports','reports/all','chat'):
                    page.evaluate('(route)=>navigate(route)',route)
                    page.wait_for_load_state('networkidle')
                    if route=='review':
                        page.locator('.user-score-analysis > summary').click()
                        assert page.locator('.user-score-analysis .score-total').is_visible()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),route
                    assert page.get_by_label('切换账号（左下角）').is_visible()
                    assert page.get_by_label('切换账号（右上角）').is_visible()
                page.locator('.sidebar [data-view=overview]').click()
                page.wait_for_function('!userTasks.loading')
                page.evaluate('window.scrollTo(0,document.documentElement.scrollHeight)')
                last_card=page.locator('.task-entry-grid .entry-card').last.bounding_box()
                account_bar=page.locator('.account-area').bounding_box()
                assert last_card['y']+last_card['height']<=account_bar['y']
                screenshot(page,'08-mobile.png')
                page.set_viewport_size({'width':1440,'height':1000})
                page.get_by_label('切换账号（左下角）').select_option('manner')
                browser_login(page,'manner','00000000')
                page.wait_for_url('**/admin.html#overview')
                page.wait_for_function('state.projects.length>0')
                page.locator('.sidebar [data-view=rules]').click()
                assert page.locator('[data-new-rule]').is_visible()
                page.locator('.sidebar [data-view=models]').click()
                page.wait_for_function('!modelSettingsLoading')
                assert page.locator('#model-settings-form').is_visible()
                page.get_by_label('切换账号（右上角）').select_option('user1')
                browser_login(page,'user1','12345678')
                page.wait_for_url('**/index.html#overview')
                page.wait_for_function('userTasks.loaded && !userTasks.loading')
                assert page.locator('.task-entry-grid .entry-card').count()==7
                assert not errors,errors
                browser.close()
                result='PASS: account switching; category-first navigation; search and pagination; group rankings and scores across pages; invalid scores excluded from ranking; sorting reset; review/report score analysis; real upload and retry; report-scope chat; DOCX/PDF export; mobile layout; administrator rules/models. Model replies simulated; all writes used a temporary database.'
                (OUTPUT/'validation.txt').write_text(result+'\n',encoding='utf-8')
                print(result)
        finally:
            for server in servers:
                server.terminate();server.wait(timeout=10)


if __name__=='__main__':
    main()

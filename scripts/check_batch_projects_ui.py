"""Exercise multi-project intake, partial failure, retry, comparison and persistence."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as temp:
    env={**os.environ, 'LLM_RETRIEVAL_ENABLED':'false','WORKBENCH_TEST_DB':str(Path(temp)/'test.db')}
    server=subprocess.Popen([sys.executable,'-c',"import os; from pathlib import Path; import main,uvicorn; main.DB_PATH=Path(os.environ['WORKBENCH_TEST_DB']); main.DATA_DIR=main.DB_PATH.parent; main.UPLOAD_DIR=main.DATA_DIR/'uploads'; main.UPLOAD_DIR.mkdir(); uvicorn.run(main.app,host='127.0.0.1',port=8004,log_level='error')"],cwd=ROOT/'backend',env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:urllib.request.urlopen('http://127.0.0.1:8004/health',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={'width':1536,'height':1080})
            page.add_init_script("window.REVIEW_API_BASE='http://127.0.0.1:8004'")
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8080/admin.html#overview',wait_until='networkidle')
            page.locator('[data-new-project]').click()
            assert page.get_by_role('button',name='创建单个项目',exact=True).is_visible()
            page.get_by_role('button',name='创建批量项目',exact=True).click()
            page.locator('[name=batch_name]').fill('三项目联调批次')
            page.locator('[name=common_brief]').fill('核对技术方案及验收指标')
            def file(name,text):return {'name':name,'mimeType':'text/plain','buffer':text.encode()}
            page.locator('[data-brief-upload=common_brief]').set_input_files(file('共用课题.txt','提交验收清单。'))
            page.wait_for_function("document.querySelector('[name=common_brief]').value.includes('提交验收清单') && document.querySelector('#batch-project-form').dataset.briefPending==='0'")
            common_brief=page.locator('[name=common_brief]').input_value()
            page.locator('#batch-files').set_input_files([
                file('报告甲.txt','项目名称：项目甲。建设单位：测试电力公司。机组：一号机组。项目概况：老旧设备升级。建设必要性：提高可靠性。技术方案：更新电气设备。投资估算：100万元。验收要求：完成调试。'),
                file('报告乙.txt',''),
                file('报告丙.txt','项目丙技术方案：更换设备，改进运行方式，完成施工和现场安装工作。'),
            ])
            assert page.locator('.batch-file-row').count()==3
            page.locator('[name=project_name_0]').fill('独立项目甲')
            page.locator('[name=project_brief_0]').fill('甲专用验收要求')
            page.locator('[data-brief-upload=project_brief_0]').set_input_files(file('甲项目任务书.txt','甲项目须提供现场试验记录。'))
            page.wait_for_function("document.querySelector('[name=project_brief_0]').value.includes('甲项目须提供现场试验记录') && document.querySelector('#batch-project-form').dataset.briefPending==='0'")
            project_brief=page.locator('[name=project_brief_0]').input_value()
            page.locator('#batch-project-form [type=submit]').click()
            page.wait_for_function("!projectBatch.busy && projectBatch.progress.includes('处理结束')")
            assert page.locator('[data-batch-row]').count()==3
            assert page.evaluate('projectBatch.rows.filter(r=>r.run).length')==2
            assert page.evaluate('new Set(projectBatch.rows.map(r=>r.project.id)).size')==3
            assert page.evaluate("projectBatch.rows[0].run.brief")==project_brief
            assert page.evaluate("projectBatch.rows[2].run.brief")==common_brief
            assert page.evaluate("projectBatch.rows.filter(r=>r.run).every(r=>r.run.sources.length===1)")
            page.get_by_role('button',name='独立项目甲',exact=True).click()
            assert page.locator('.score-dimension').count()==4
            page.locator('#project-batch-sort').select_option('fail')
            numbers=page.locator('.batch-results tbody tr').evaluate_all("rows=>rows.map(r=>r.cells[5].textContent.split('/')[0].trim()).filter(v=>v!=='—').map(Number)")
            assert numbers==sorted(numbers)
            page.locator('#project-batch-filter').select_option('error')
            assert page.locator('[data-batch-row]').count()==1
            failed=page.evaluate("projectBatch.rows.find(r=>r.project.batch_status==='error').project.id")
            page.locator('[data-batch-file]').set_input_files(file('报告乙-修正.txt','项目名称：项目乙。建设单位：测试公司。机组：二号。建设内容：更新控制系统。项目概况：设备改造。投资估算：50万元。验收要求：完成设备试验。'))
            page.locator('[data-batch-retry]').click()
            page.wait_for_function('!projectBatch.busy && projectBatch.rows.every(r=>r.run)')
            page.locator('#project-batch-filter').select_option('all')
            assert page.locator('[data-batch-row]').count()==3
            assert page.evaluate(f"projectBatch.rows.some(r=>r.project.id==='{failed}' && r.run)")
            page.locator('#project-batch-search').fill('独立项目甲')
            assert page.locator('[data-batch-row]').count()==1
            page.locator('#project-batch-search').fill('')
            batch_id=page.evaluate('projectBatch.batchId')
            page.reload(wait_until='networkidle')
            page.wait_for_function('!projectBatch.loading && projectBatch.rows.length===3')
            page.locator('#project-batch-scope').select_option(batch_id)
            page.wait_for_function('!projectBatch.loading')
            assert page.evaluate('projectBatch.rows.every(r=>r.run)')
            page.locator('#project-batch-sort').select_option('score')
            values=page.locator('.batch-results tbody tr').evaluate_all("rows=>rows.map(r=>r.cells[4].textContent.trim()).filter(v=>v!=='—').map(Number)")
            assert values==sorted(values,reverse=True)
            assert len(set(values))==3,values
            output=ROOT/'artifacts'/'batch-validation';output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/'project-comparison.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.set_viewport_size({'width':1536,'height':1080})
            page.locator('[data-batch-open]').first.click()
            page.wait_for_function("state.view==='review' && state.run!==null")
            assert page.evaluate('state.run.project_id===state.project.id && state.run.material_package_id===null')
            assert not errors,errors
            browser.close()
            print('PASS: mode chooser; three independent projects; mapping; partial failure and retry; scoring; sort/filter/search; persistence; result navigation; mobile layout')
    finally:
        server.terminate();server.wait(timeout=10)

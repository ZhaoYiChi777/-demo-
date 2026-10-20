"""Verify single-report intake and package comparison using a temporary database."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory() as temp:
    env = {**os.environ, 'LLM_RETRIEVAL_ENABLED':'false', 'WORKBENCH_TEST_DB': str(Path(temp) / 'test.db')}
    server = subprocess.Popen([sys.executable, '-c', "import os; from pathlib import Path; import main, uvicorn; main.DB_PATH=Path(os.environ['WORKBENCH_TEST_DB']); main.DATA_DIR=main.DB_PATH.parent; main.UPLOAD_DIR=main.DATA_DIR/'uploads'; main.UPLOAD_DIR.mkdir(); uvicorn.run(main.app,host='127.0.0.1',port=8002,log_level='error')"], cwd=ROOT / 'backend', env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=1)
                break
            except OSError:
                time.sleep(.1)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width':1536,'height':1000})
            page.add_init_script("window.REVIEW_API_BASE='http://127.0.0.1:8002'")
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto('http://127.0.0.1:8080/admin.html#overview', wait_until='networkidle')
            page.locator('[data-single-report]').click()
            page.locator('[name=report]').set_input_files({'name':'独立报告.txt','mimeType':'text/plain','buffer':'项目名称：独立报告\n项目概况\n建设必要性\n投资估算100万元\n建设内容\n验收要求'.encode()})
            page.locator('[name=brief]').fill('核验建设内容和验收要求')
            page.locator('[data-brief-upload=brief]').set_input_files({'name':'验收要求.md','mimeType':'text/plain','buffer':'提交性能试验记录。'.encode()})
            page.wait_for_function("document.querySelector('[name=brief]').value.includes('提交性能试验记录') && document.querySelector('#single-report-form').dataset.briefPending==='0'")
            imported=page.locator('[name=brief]').input_value()
            page.locator('[data-brief-upload=brief]').set_input_files({'name':'损坏文件.docx','mimeType':'application/octet-stream','buffer':b'not a word document'})
            page.wait_for_function("document.querySelector('#single-report-form').dataset.briefPending==='0'")
            assert page.locator('[name=brief]').input_value()==imported
            assert '失败' in page.locator('.brief-import-status').inner_text()
            page.locator('#single-report-form [type=submit]').click()
            page.wait_for_function('state.run && !state.busy')
            assert page.evaluate('state.run.sources.length') == 1
            assert page.evaluate('state.run.brief') == imported
            page.evaluate('''async()=>{
                const pid=state.project.id;
                const first=await api(`/api/projects/${pid}/material-packages`,'POST',{name:'报告甲'});
                await api(`/api/projects/${pid}/sources/${state.run.sources[0].id}/assignment`,'PATCH',{material_package_id:first.id});
                await api(`/api/workbench/projects/${pid}/runs`,'POST',{material_package_id:first.id});
                await api(`/api/workbench/projects/${pid}/runs`,'POST',{material_package_id:first.id});
                const upload=new FormData();upload.append('file',new File(['项目名称：报告乙，建设内容独立设备。'],'报告乙.txt',{type:'text/plain'}));
                await api(`/api/projects/${pid}/documents`,'POST',upload);
                const material=await api(`/api/workbench/projects/${pid}/materials`);
                const source=material.sources.at(-1);
                await api(`/api/projects/${pid}/sources/${source.id}/category`,'PATCH',{category:'project_report'});
                const second=await api(`/api/projects/${pid}/material-packages`,'POST',{name:'报告乙'});
                await api(`/api/projects/${pid}/sources/${source.id}/assignment`,'PATCH',{material_package_id:second.id});
                await api(`/api/workbench/projects/${pid}/runs`,'POST',{material_package_id:second.id});
            }''')
            page.locator('.sidebar [data-view=overview]').click()
            page.locator('[data-comparison]').click()
            page.wait_for_function('!comparison.loading && comparison.rows.length>0')
            assert page.locator('[data-compare-open]').count() == 2
            assert page.evaluate('comparison.rows.filter(r=>r.run).every(r=>r.run.sources.length===1)')
            page.get_by_role('button',name='报告乙',exact=True).click()
            assert page.locator('.score-dimension').count() == 4
            page.locator('#comparison-sort').select_option('pending')
            assert page.evaluate("comparison.sort") == 'pending'
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.set_viewport_size({'width':1536,'height':1000})
            page.locator('[data-compare-open]').first.click()
            page.wait_for_function("state.view==='review' && state.scope!==null")
            old_brief=page.evaluate('state.run.brief')
            page.evaluate("selectStep('material')")
            source_count=page.evaluate('state.materials.sources.length')
            page.locator('[data-brief-upload=text]').set_input_files({'name':'补充要求.txt','mimeType':'text/plain','buffer':'保存补充的完成时限要求。'.encode()})
            page.wait_for_function("document.querySelector('#brief-form [name=text]').value.includes('保存补充的完成时限要求') && document.querySelector('#brief-form').dataset.briefPending==='0'")
            page.locator('#brief-form [type=submit]').click()
            page.wait_for_function("state.materials.brief.includes('保存补充的完成时限要求')")
            assert page.evaluate('state.materials.sources.length')==source_count
            assert page.evaluate('state.run.brief')==old_brief
            assert not errors, errors
            browser.close()
            print('PASS: requirement imports; failed import preserves text; saved requirements excluded from report evidence; old snapshot unchanged; single review; comparison and mobile layout')
    finally:
        server.terminate()
        server.wait(timeout=10)

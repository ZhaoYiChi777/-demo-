"""Live-provider smoke test in an isolated database. Uses the project's model configuration."""
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
    env={**os.environ,'WORKBENCH_TEST_DB':str(Path(temp)/'test.db')}
    server=subprocess.Popen([sys.executable,'-c',"import os; from pathlib import Path; import main,uvicorn; main.DB_PATH=Path(os.environ['WORKBENCH_TEST_DB']); main.DATA_DIR=main.DB_PATH.parent; main.UPLOAD_DIR=main.DATA_DIR/'uploads'; main.UPLOAD_DIR.mkdir(); uvicorn.run(main.app,host='127.0.0.1',port=8003,log_level='error')"],cwd=ROOT/'backend',env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen('http://127.0.0.1:8003/health',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':1080})
            page.add_init_script("window.REVIEW_API_BASE='http://127.0.0.1:8003'")
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8080/admin.html',wait_until='networkidle')
            page.evaluate("async()=>{await api('/api/workbench/demo','POST');await refreshProjects();await selectProject('DEMO-REVIEW');}")
            page.wait_for_function('agentChat.configured===true && !agentChat.loading')
            original=page.evaluate('state.run.id')
            def send(message):
                page.locator('#agent-message').fill(message)
                page.locator('#agent-chat-form [type=submit]').click()
                page.wait_for_function('!agentChat.busy && agentChat.messages.at(-1)?.content.length>0',timeout=180000)
                assert not page.evaluate('agentChat.messages.at(-1).incomplete'),page.locator('.agent-status').inner_text()
                assert page.evaluate('agentChat.messages.at(-1).reasoning_content.length')>0
                reasoning=page.locator('#review-agent .agent-reasoning').last
                assert not reasoning.evaluate('(e)=>e.open')
                assert not reasoning.locator('.agent-reasoning-text').is_visible()
                reasoning.locator('summary').click()
                assert reasoning.locator('.agent-reasoning-text').is_visible()
                reasoning.locator('summary').click()
                print(page.evaluate('agentChat.messages.at(-1).content')[:200],flush=True)
            send('请现在执行一次真实的重新评审，完成后简要告诉我结果。')
            assert page.evaluate('state.run.id')!=original
            print('PASS: live model executed review and updated UI',flush=True)
            send('针对刚才的结果，请检索油箱的原文证据，并说明缺少什么。')
            assert page.evaluate('agentChat.evidence.length')>0
            send('请把防火分隔检查项提出为待确认建议，理由是现有原文未说明防火分隔措施、需人工核验；修改建议是补充分隔措施说明和图纸。请生成待应用建议，不要直接修改。')
            page.locator('[data-agent-apply]:not([disabled])').last.click()
            page.wait_for_function('agentChat.proposals.some(p=>p.applied)')
            assert page.evaluate("state.run.checks.find(c=>c.id==='DEMO-004').result")=='pending'
            send('请保存补充课题要求：验收时必须提供防火分隔措施说明及图纸。保留原有要求。')
            assert '防火分隔' in page.evaluate('state.materials.brief')
            count=page.evaluate('agentChat.messages.length')
            page.reload(wait_until='networkidle')
            page.wait_for_function('!agentChat.loading && agentChat.messages.length>0')
            assert page.evaluate('agentChat.messages.length')==count
            assert page.evaluate('agentChat.messages.filter(m=>m.role==="assistant").every(m=>m.reasoning_content.length>0)')
            assert page.locator('#review-agent .agent-reasoning[open]').count()==0
            output=ROOT/'artifacts'/'agent-validation'
            output.mkdir(parents=True,exist_ok=True)
            page.locator('#review-agent').screenshot(path=str(output/'live-dialog.png'))
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            print('PASS: followup, evidence, proposal application, brief update, persisted conversation, mobile layout',flush=True)
            browser.close()
    finally:
        server.terminate();server.wait(timeout=10)

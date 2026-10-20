"""Exercise persistent conversation controls and score explanations with an isolated DB.
The model stream is stubbed; tools, database and browser interactions are real.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
SERVER='''
import os,json
from pathlib import Path
import main,uvicorn
main.DB_PATH=Path(os.environ['WORKBENCH_TEST_DB'])
main.review_agent.config=lambda:dict(enabled=True,key='test',model='test')
def events(items):
    yield {'type':'response.reasoning_text.delta','delta':'根据当前报告定位证据。'}
    if items[-1].get('type')=='function_call_output':
        yield {'type':'response.output_text.delta','delta':'已检索当前报告证据，可继续核验。'}
    else:
        yield {'type':'response.output_item.done','item':{'type':'function_call','name':'search_evidence','arguments':json.dumps({'query':'油箱'}),'call_id':'search'}}
main.review_agent.model_events=events
uvicorn.run(main.app,host='127.0.0.1',port=8005,log_level='error')
'''
with tempfile.TemporaryDirectory() as temp:
    env={**os.environ,'WORKBENCH_TEST_DB':str(Path(temp)/'test.db'),'LLM_RETRIEVAL_ENABLED':'false'}
    server=subprocess.Popen([sys.executable,'-c',SERVER],cwd=ROOT/'backend',env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:urllib.request.urlopen('http://127.0.0.1:8005/health',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={'width':1536,'height':1080})
            page.add_init_script("window.REVIEW_API_BASE='http://127.0.0.1:8005'")
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8080/admin.html#overview',wait_until='networkidle')
            nav=page.locator('nav[aria-label="主导航"] button').all_text_contents()
            assert '导航' in nav[0] and '评审辅助对话' in nav[1],nav
            page.evaluate("async()=>{await api('/api/workbench/demo','POST');await refreshProjects();await selectProject('DEMO-REVIEW');navigate('review');}")
            page.wait_for_function('!agentChat.loading && agentChat.configured')
            page.locator('#agent-message').fill('查找油箱原文')
            page.locator('#agent-chat-form [type=submit]').click()
            page.wait_for_function('!agentChat.busy && agentChat.messages.length===2')
            reasoning=page.locator('#review-agent .agent-reasoning')
            assert reasoning.is_visible() and not reasoning.evaluate('(e)=>e.open')
            assert not reasoning.locator('.agent-reasoning-text').is_visible()
            reasoning.locator('summary').click()
            assert reasoning.locator('.agent-reasoning-text').is_visible()
            page.wait_for_function('agentChat.messages[1].reasoningOpen===true')
            page.evaluate('renderAgent()')
            assert page.locator('#review-agent .agent-reasoning').evaluate('(e)=>e.open')
            original=page.evaluate('agentChat.conversationId')
            page.locator('[data-agent-new]').click()
            page.wait_for_function('agentChat.messages.length===0')
            page.locator('[data-agent-history]').click()
            page.locator('.conversation-record').first.wait_for()
            assert page.locator('.conversation-record').count()==2
            historical=page.locator('.conversation-record').filter(has=page.locator('[data-agent-resume]'))
            historical.locator('details').first.locator('summary').first.click()
            assert not historical.locator('.agent-reasoning').evaluate('(e)=>e.open')
            historical.locator('.agent-reasoning summary').click()
            assert historical.locator('.agent-reasoning-text').is_visible()
            page.locator('[data-agent-resume]').click()
            page.wait_for_function('agentChat.messages.length===2')
            assert page.evaluate('agentChat.conversationId')==original
            page.reload(wait_until='networkidle')
            page.wait_for_function('!agentChat.loading && agentChat.messages.length===2')
            assert page.evaluate('agentChat.messages[1].reasoning_content.length')>0
            assert not page.locator('#review-agent .agent-reasoning').evaluate('(e)=>e.open')
            page.locator('[data-agent-logs]').click()
            page.locator('.operation-log').first.wait_for()
            assert page.locator('.operation-log').count()==2
            assert '耗时' in page.locator('#editor-body').inner_text()
            assert '检索到' in page.locator('#editor-body').inner_text()
            output=ROOT/'artifacts'/'history-scoring-validation';output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/'operation-logs.png'))
            page.locator('#agent-log-filter').select_option('current')
            assert page.locator('.operation-log').count()==2
            page.locator('[data-agent-report]').first.click()
            page.wait_for_function("state.step==='report'")
            assert page.locator('.report-paper').is_visible()
            page.locator('nav [data-view=batch]').click()
            page.wait_for_function('!projectBatch.loading && projectBatch.rows.length')
            page.locator('[data-batch-score]').first.click()
            assert page.locator('.rank-explanation').is_visible()
            assert '第 1 / 1 位' in page.locator('.rank-explanation').inner_text()
            assert page.locator('.score-dimension').count()==4
            page.locator('.score-dimension').first.locator('summary').click()
            page.locator('.quality-policy summary').click()
            assert '同一检查项不重复计分' in page.locator('.quality-policy').inner_text()
            page.screenshot(path=str(output/'quality-explanation.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.set_viewport_size({'width':1536,'height':1080})
            page.locator('.score-dimension').nth(2).locator('summary').click()
            target=page.locator('[data-quality-check]:visible').first
            check_id=target.get_attribute('data-quality-check')
            target.click()
            page.wait_for_function("state.view==='review' && state.selected!==null")
            assert page.evaluate('state.selected')==check_id
            assert page.locator('.issue-row.selected').is_visible()
            assert not errors,errors
            browser.close()
            print('PASS: navigation order; new/restore/persisted conversation; log timing/results/report links; scoring/ranking explanations; evidence navigation; mobile width')
    finally:
        server.terminate();server.wait(timeout=10)

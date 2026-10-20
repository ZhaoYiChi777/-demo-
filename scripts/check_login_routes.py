"""Check login navigation in an isolated database, including same-document URLs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright
from check_admin_workspace import ROOT, API, WEB, SERVER, wait_ready

OUTPUT = ROOT / 'artifacts' / 'login-navigation-20261020'
PASSWORDS = {'user1': '12345678', 'manner': '00000000'}


def sign_in(page, username):
    form = page.locator('#account-login-form')
    form.locator('[name=username]').fill(username)
    form.locator('[name=password]').fill(PASSWORDS[username])
    with page.expect_navigation(wait_until='domcontentloaded'):
        form.locator('[type=submit]').click()
    ready(page, username)


def ready(page, username):
    page.wait_for_function('''name=>window.currentAccount?.username===name
        &&!document.documentElement.classList.contains('auth-pending')
        &&!document.querySelector('#account-login').open
        &&document.querySelector('#connection-state').textContent.includes('已连接')''', arg=username)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    passed, errors = [], []
    with tempfile.TemporaryDirectory() as temp:
        env = {**os.environ, 'WORKBENCH_TEST_DB': str(Path(temp) / 'test.db'), 'LLM_ENABLED': 'false', 'LLM_RETRIEVAL_ENABLED': 'false'}
        servers = []
        try:
            servers.append(subprocess.Popen([sys.executable, '-c', SERVER], cwd=ROOT/'backend', env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            servers.append(subprocess.Popen([sys.executable, 'scripts/serve_frontend.py', '--port', '18080'], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_ready(API+'/health'); wait_ready(WEB)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                # Explicit filenames are essential: '/' to 'index.html' already reloads,
                # whereas 'index.html#chat' to 'index.html#overview' used to stay stuck.
                for path, username in [('/index.html#chat','user1'),('/index.html#overview','user1'),
                                       ('/admin.html#overview','manner'),('/admin.html#accounts','manner'),
                                       ('/','user1'),('/admin.html#reports','user1'),('/index.html#chat','manner')]:
                    context = browser.new_context()
                    context.add_init_script(f'window.REVIEW_API_BASE="{API}"')
                    page = context.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(WEB+path, wait_until='networkidle')
                    sign_in(page, username)
                    target = 'admin.html' if username=='manner' else 'index.html'
                    assert page.url == f'{WEB}/{target}#overview', page.url
                    page.reload(wait_until='networkidle'); ready(page, username)
                    assert page.locator('#account-login[open]').count()==0
                    passed.append(f'first login and refresh: {path} -> {username}')
                    context.close()
                context = browser.new_context(viewport={'width':1440,'height':1000})
                context.add_init_script(f'window.REVIEW_API_BASE="{API}"')
                page = context.new_page(); page.set_default_timeout(10000)
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(WEB+'/index.html#chat', wait_until='networkidle'); sign_in(page,'user1')
                page.goto(WEB+'/admin.html#accounts', wait_until='networkidle')
                page.wait_for_url('**/index.html#overview'); ready(page,'user1')
                passed.append('user opening administrator URL returns to user workspace')
                page.locator('.sidebar [data-view=chat]').click()
                page.get_by_label('切换账号（右上角）').select_option('manner')
                page.locator('#account-login[open]').wait_for()
                assert page.locator('#account-login [name=username]').input_value()=='manner'
                page.keyboard.press('Escape')
                ready(page,'user1'); assert page.url.endswith('#chat')
                passed.append('cancel switch keeps current account and route')
                page.get_by_label('切换账号（左下角）').select_option('manner')
                page.locator('#account-login [name=password]').fill('wrong-password')
                with page.expect_response(lambda r:r.url.endswith('/api/auth/login')) as response:
                    page.locator('#account-login [type=submit]').click()
                assert response.value.status==401
                page.locator('#account-login .form-error').filter(has_text='账号或密码错误').wait_for()
                assert page.url.endswith('#chat')
                assert page.evaluate("async()=>(await (await fetch(accountAPI+'/api/auth/me')).json()).username")=='user1'
                passed.append('wrong switch password preserves original session')
                sign_in(page,'manner'); assert page.url.endswith('/admin.html#overview')
                passed.append('successful switch enters administrator workspace')
                page.go_back(wait_until='domcontentloaded')
                page.wait_for_url('**/admin.html#overview'); ready(page,'manner')
                passed.append('browser back does not restore the previous account workspace')
                page.goto(WEB+'/index.html#chat', wait_until='networkidle')
                page.wait_for_url('**/admin.html#overview'); ready(page,'manner')
                passed.append('administrator opening user URL returns to administrator workspace')
                page.get_by_label('切换账号（右上角）').select_option('logout')
                page.locator('#account-login[open]').wait_for()
                page.keyboard.press('Escape'); assert page.locator('#account-login[open]').count()==1
                sign_in(page,'manner'); assert page.url.endswith('/admin.html#overview')
                passed.append('logout then same-account login completes initialization')
                for username in ['manner','user1']:
                    if username=='user1':
                        page.get_by_label('切换账号（左下角）').select_option('user1'); sign_in(page,'user1')
                    context.clear_cookies()
                    page.evaluate("async()=>{try{await api('/api/projects')}catch{}}")
                    page.locator('#account-login[open]').wait_for()
                    page.locator('#account-login [name=username]').fill(username)
                    page.locator('#account-login [name=password]').fill(PASSWORDS[username])
                    page.evaluate("async()=>{try{await api('/api/projects')}catch{}}")
                    assert page.locator('#account-login [name=password]').input_value()==PASSWORDS[username]
                    sign_in(page,username)
                    passed.append(f'expired session and repeated 401 responses: {username}')
                page.get_by_label('切换账号（右上角）').select_option('logout')
                page.locator('#account-login[open]').wait_for()
                context.set_offline(True)
                form=page.locator('#account-login-form')
                form.locator('[name=username]').fill('user1');form.locator('[name=password]').fill(PASSWORDS['user1'])
                form.locator('[type=submit]').click()
                page.locator('#account-login .form-error').filter(has_text='无法连接服务').wait_for()
                assert page.locator('#account-login[open]').count()==1
                context.set_offline(False);sign_in(page,'user1')
                passed.append('network recovery allows retry without refreshing the login page')
                page.screenshot(path=str(OUTPUT/'user-login-complete.png'), full_page=True)
                assert not errors, errors
                browser.close()
            result={'result':'PASS','count':len(passed),'cases':passed,'writes':'temporary database only'}
            (OUTPUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False))
        finally:
            for server in servers:server.terminate()
            for server in servers:server.wait(timeout=10)


if __name__=='__main__':
    main()

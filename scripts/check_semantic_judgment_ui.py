"""Read-only browser checks against the latest live review, without creating projects or model calls."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'artifacts/semantic-judgment-validation'
out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page(viewport={'width':1440,'height':1080})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8080/admin.html#review',wait_until='networkidle')
    page.evaluate("async()=>{await selectProject('PRJ-20260915001937');navigate('review');state.category='all';state.result='all';state.step='common';state.mode='list';state.selected='GEN-002';render();}")
    page.locator('.judgment-note').first.wait_for()
    detail=page.locator('.judgment-detail').first
    assert detail.count()==1
    assert not detail.evaluate('(e)=>e.open')
    detail.locator('summary').click()
    assert detail.evaluate('(e)=>e.open')
    assert '技术方案' in detail.inner_text()
    refs=detail.locator('[data-evidence]')
    assert refs.count()>0
    paragraph_id=refs.first.get_attribute('data-evidence')
    refs.first.click()
    page.wait_for_function("state.mode==='document'")
    assert page.locator('#chunk-'+paragraph_id).is_visible()
    page.evaluate("state.mode='list';render()")
    page.locator('.judgment-detail summary').first.click()
    page.locator('.judgment-detail').first.scroll_into_view_if_needed()
    page.screenshot(path=str(out/'judgment-criteria.png'))
    page.locator('[data-current-report]').click()
    assert page.locator('.report-paper').is_visible()
    assert page.locator('.report-paper .judgment-detail').count()>0
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors,errors
    print('PASS: judgment status, expandable criteria, source navigation, report and mobile width')
    browser.close()

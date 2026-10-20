"""Exercise the review UI against an isolated temporary database."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from docx import Document
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts" / "workbench-validation"
OUTPUT.mkdir(parents=True, exist_ok=True)


def main():
    with tempfile.TemporaryDirectory() as temp:
        env = {**os.environ, 'LLM_RETRIEVAL_ENABLED':'false', "WORKBENCH_TEST_DB": str(Path(temp) / "test.db")}
        server = subprocess.Popen([sys.executable, "-c", "import os; from pathlib import Path; import main, uvicorn; main.DB_PATH=Path(os.environ['WORKBENCH_TEST_DB']); main.DATA_DIR=main.DB_PATH.parent; main.UPLOAD_DIR=main.DATA_DIR/'uploads'; main.UPLOAD_DIR.mkdir(); uvicorn.run(main.app, host='127.0.0.1', port=8001, log_level='error')"], cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=1)
                    break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1536, "height": 1080}, device_scale_factor=1)
                page.add_init_script("window.REVIEW_API_BASE='http://127.0.0.1:8001'")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://127.0.0.1:8080/admin.html", wait_until="networkidle")
                page.locator(".topbar [data-demo]").click()
                page.get_by_role("heading", name="不符合评审要求", exact=True).wait_for()
                assert page.locator(".summary-footer").inner_text().startswith("7")
                assert page.locator(".issue-row").count() == 3
                page.screenshot(path=str(OUTPUT / "01-review-summary.png"), full_page=True)

                page.locator('[data-check="DEMO-004"]').click()
                assert "防火分隔" in page.locator(".check-detail").inner_text()
                page.locator('[data-rule-snapshot="DEMO-004"]').click()
                assert "DEMO-004" not in page.locator("dialog").inner_text()
                page.locator('dialog .rule-reference summary').click()
                assert "DEMO-004 · 第 1 版" in page.locator("dialog").inner_text()
                page.locator('dialog [data-close]').first.click()
                page.locator('[data-locate="DEMO-004"]').click()
                assert page.locator(".document").is_visible()
                assert "未匹配到原句" in page.locator(".doc-warning").inner_text()
                page.locator('[data-annotation="DEMO-003"]').first.click()
                assert "投资金额逻辑一致性" in page.locator(".annotation").inner_text()
                assert "投资估算" in page.locator(".doc-chunk.selected").inner_text()
                page.screenshot(path=str(OUTPUT / "02-document-annotations.png"), full_page=True)

                page.locator('[data-mode="list"]').click()
                page.locator('[data-check="DEMO-001"]').click()
                page.locator('[data-mode="document"]').click()
                assert "项目基本信息完整性" in page.locator(".annotation").inner_text()
                assert "项目概况" in page.locator(".doc-chunk.selected").inner_text()

                page.locator('[data-mode="process"]').click()
                assert page.locator(".process details").count() == 5
                page.locator(".process details summary").nth(1).click()
                assert "项目基本信息完整性" in page.locator(".process details[open]").inner_text()
                assert "DEMO-001" not in page.locator(".process details[open]").inner_text()

                page.locator('.sidebar [data-view="rules"]').click()
                page.locator('#app [data-edit-rule="DEMO-004"]').click()
                page.locator('[name="terms"]').fill("油箱\n管路")
                page.locator('[name="standard"]').fill("示例调整：材料必须出现油箱及管路字段。")
                page.locator('[name="content"]').fill("仅验证油箱与管路字段存在。")
                page.get_by_role("button",name="保存规则",exact=True).click()
                page.locator('dialog').wait_for(state='hidden')
                edited_card = page.locator(".rule-card").filter(has=page.locator('[data-edit-rule="DEMO-004"]'))
                edited_card.locator('.rule-reference summary').click()
                assert "第 2 版" in edited_card.inner_text()
                page.locator('.sidebar [data-view="review"]').click()
                assert "材料或规则已更新" in page.locator(".notice").first.inner_text()
                page.locator('.page-heading [data-run]').click()
                page.wait_for_function("document.querySelector('.summary-footer .fail-text b')?.textContent === '1'")
                assert "材料或规则已更新" not in page.locator("#app").inner_text()

                page.locator('[data-mode="list"]').click()
                page.locator('[data-check="DEMO-003"]').click()
                page.locator('[data-adjudicate="DEMO-003"]').click()
                page.locator('dialog [name="result"]').select_option("pass")
                page.locator('dialog [name="reason"]').fill("专家核对：210 万元加 70 万元等于总投资 280 万元，示例口径一致。")
                page.locator('dialog [name="suggestion"]').fill("保留费用明细。")
                page.get_by_role("button",name="保存复核结论").click()
                page.locator('dialog').wait_for(state='hidden')
                assert page.locator('.summary-footer .pending-text b').inner_text() == "0"

                page.locator('.sidebar [data-view="reports"]').click()
                assert page.locator('.report-paper section').count() == 8
                with page.expect_download() as download:
                    page.locator('[data-export]').click()
                downloaded = download.value
                downloaded.save_as(OUTPUT / "review-report.docx")
                doc = Document(OUTPUT / "review-report.docx")
                text = "\n".join(p.text for p in doc.paragraphs)
                assert "6 项符合、1 项不符合、0 项待确认" in text
                assert "DEMO-004 · v2" in text
                assert "专家核对：210 万元" in text
                page.pdf(path=str(OUTPUT / "review-report.pdf"), format="A4", print_background=True)
                page.screenshot(path=str(OUTPUT / "03-report.png"), full_page=True)
                page.locator('[data-archive]').click()
                page.wait_for_function("document.querySelector('.report-toolbar')?.textContent.includes('已归档')")
                page.reload(wait_until="networkidle")
                assert "已归档" in page.locator('.report-toolbar').inner_text()
                page.locator('[data-history]').click()
                page.locator('[data-history-run]').first.wait_for()
                assert page.locator('[data-history-run]').count() == 2
                page.locator('dialog [data-close]').first.click()

                page.set_viewport_size({"width":390,"height":844})
                page.locator('.sidebar [data-view="review"]').click()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path=str(OUTPUT / "04-mobile.png"),full_page=True)

                page.set_viewport_size({"width":1536,"height":1080})
                page.locator('.sidebar [data-view="projects"]').click()
                page.locator('[data-new-project]').click()
                page.locator('dialog [name="name"]').fill("浏览器验收空项目")
                page.get_by_role('button',name='创建项目',exact=True).click()
                page.get_by_role('heading',name='浏览器验收空项目',exact=True).wait_for()
                assert page.locator('.summary').count() == 0
                assert page.locator('#brief-form textarea').input_value() == ''
                page.locator('#file-upload').set_input_files({"name":"测试项目申报书.txt","mimeType":"text/plain","buffer":"项目名称：汽机改造。建设单位：验收电厂。机组：1号汽轮机。建设必要性：设备升级。技术方案：更换抗燃油管路。投资估算：100万元。".encode('utf-8')})
                page.get_by_text('测试项目申报书.txt',exact=True).first.wait_for()
                page.wait_for_function('state.packages.length>0')
                assert page.get_by_role('heading',name='报告与附件',exact=True).is_visible()
                assert page.locator('[data-new-package],[data-source-package]').count()==0
                package_id = str(page.evaluate('state.packages[0].id'))
                report_name = page.evaluate('state.packages[0].name')
                page.locator('#review-scope').select_option(package_id)
                page.locator('.page-heading [data-run]').click()
                page.get_by_text('本次结论范围：'+report_name,exact=True).wait_for()
                page.locator('#review-scope').select_option('')
                page.locator('.page-heading [data-run]').click()
                page.get_by_text('本次结论范围：整个项目',exact=True).wait_for()

                # Hold a project-A review response while switching to project B.
                page.locator('.topbar [data-demo]').click()
                page.get_by_role('heading',name='12、13号机组抗燃油系统改造',exact=True).wait_for()
                held = []
                page.route('**/api/workbench/projects/DEMO-REVIEW/runs', lambda route: held.append(route) if route.request.method=='POST' else route.continue_())
                page.locator('.page-heading [data-run]').click()
                page.locator('.sidebar [data-view="projects"]').click()
                page.locator('tr').filter(has_text='浏览器验收空项目').locator('[data-project]').click()
                page.get_by_role('heading',name='浏览器验收空项目',exact=True).wait_for()
                assert held
                held.pop().continue_()
                page.wait_for_load_state('networkidle')
                assert page.get_by_role('heading',name='浏览器验收空项目',exact=True).is_visible()
                assert 'DEMO-' not in page.locator('.review-content').inner_text()
                assert not errors, errors
                (OUTPUT / "ui-results.json").write_text(json.dumps({"passed": True, "checks": ["summary", "filters", "rule snapshot", "bidirectional evidence", "check switching", "five process steps", "rule edit and rerun", "expert review", "DOCX contents", "print PDF", "archive persistence", "mobile width", "project isolation"], "browser_errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print("UI integration checks passed; artifacts/workbench-validation contains screenshots and exports.")


if __name__ == '__main__':
    main()

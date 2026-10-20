"""Reproduce OCR on the two failed PDFs without changing their stored reviews."""
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import main

out=ROOT/'artifacts'/'review-upgrade-20260915'
out.mkdir(parents=True,exist_ok=True)
results=[]
with main.db() as conn:
    sources=[dict(r) for r in conn.execute('SELECT * FROM document_sources WHERE id IN (105,106) ORDER BY id')]
for source in sources:
    files=[p for p in (main.UPLOAD_DIR/source['project_id']).iterdir() if p.name.endswith(source['filename'])]
    assert len(files)==1,source['filename']
    started=time.monotonic()
    parsed=main.parse_source_file(source['filename'],files[0].read_bytes())
    row=dict(filename=source['filename'],status=parsed.status,error=parsed.error,method=parsed.method,pages=parsed.page_count,characters=len(parsed.text),seconds=round(time.monotonic()-started,1))
    results.append(row)
    (out/f'ocr-{source["id"]}.txt').write_text(parsed.text,encoding='utf-8')
    print(json.dumps(row,ensure_ascii=False),flush=True)
(out/'ocr-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
assert len(results)==2 and all(r['status']=='parsed' and r['characters']>1000 for r in results)

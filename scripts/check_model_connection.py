"""Minimal live request through the application's configured model gateway."""
import json
import sys
import urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import main

print('Model:',main.model_config()['model'])
print('Endpoint:',main.model_config()['url'])
try:
    answer=''.join(e.get('delta','') for e in main.model_gateway.events({'input':'只回复：连接成功','max_output_tokens':32}) if e['type']=='response.output_text.delta')
    assert answer.strip(),'模型未返回内容'
    print('PASS:',answer)
except urllib.error.HTTPError as exc:
    try:
        error=json.loads(exc.read()).get('error',{})
        print('FAIL: HTTP',exc.code,'code=',error.get('code'))
    except (ValueError,AttributeError):print('FAIL: HTTP',exc.code)
    sys.exit(1)
except (RuntimeError,TimeoutError,urllib.error.URLError) as exc:
    print('FAIL:',str(exc))
    sys.exit(1)

"""Small live JSON and tool-loop checks; does not select models or touch reports."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import main
from model_gateway import ModelGateway
from model_settings import ModelSelection


def check(model):
    started = time.monotonic()
    result = {'model':model, 'json':False, 'tool_call':False, 'tool_followup':False}
    try:
        config = main.model_settings.candidate(ModelSelection(model=model))
        gateway = ModelGateway(lambda:config)
        result['json'] = gateway.json('只输出JSON：{"ok":true}', {}).get('ok') is True
        body = {'instructions':'必须先调用read_value工具取得值，然后只回复工具返回的数字。',
                'input':[{'role':'user','content':'请读取数值。'}], 'thinking':True, 'max_output_tokens':4000,
                'tools':[{'type':'function','name':'read_value','description':'读取数值。',
                          'parameters':{'type':'object','properties':{},'required':[]}}]}
        items = [e['item'] for e in gateway.events(body) if e['type']=='response.output_item.done']
        calls = [i for i in items if i['type']=='function_call']
        result['tool_call'] = bool(calls) and all(c['name']=='read_value' and isinstance(json.loads(c['arguments']),dict) for c in calls)
        if result['tool_call']:
            body['input'] += items + [{'type':'function_call_output','call_id':c['call_id'],'output':'{"value":7}'} for c in calls]
            followup = [e['item'] for e in gateway.events(body) if e['type']=='response.output_item.done']
            text = ''.join(i.get('content','') for i in followup if i['type']=='message')
            result['tool_followup'] = text.strip()=='7' and not any(i['type']=='function_call' for i in followup)
    except Exception as exc:
        result['error'] = f'HTTP {exc.code}' if hasattr(exc,'code') else type(exc).__name__
    result['duration_ms'] = round((time.monotonic()-started)*1000)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--models',nargs='+',default=['qwen3.8-flash','kimi-k3','glm-5.2'])
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    main.model_settings.list_models()
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows=list(pool.map(check,args.models))
    text=json.dumps(rows,ensure_ascii=False,indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text,encoding='utf-8')
    sys.exit(0 if all(all(row[k] for k in ('json','tool_call','tool_followup')) for row in rows) else 1)

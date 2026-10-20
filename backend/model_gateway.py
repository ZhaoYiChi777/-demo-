"""OpenAI-compatible Chat Completions transport for all review model calls.

Expose the agent's existing event format so review tools and retrieval share one
provider without depending on its wire protocol.
"""
import json
import re
import time
import urllib.request
from urllib.parse import urlparse


def thinking_only(model):
    name = model.lower().split('/')[-1]
    return name.startswith(('glm-5.3', 'kimi-k2.7-code', 'deepseek-r1')) or bool(re.search(r'-thinking(?:-|$)', name))


class ModelGateway:
    def __init__(self, config):
        self.config = config

    def events(self, body):
        config = self.config()
        payload={'model':config['model'], 'stream':True,
                 'messages':self.messages(body), 'max_tokens':body.get('max_output_tokens',7000)}
        host = urlparse(config['url']).hostname or ''
        if host=='api.deepseek.com':
            payload['thinking']={'type':'enabled' if body.get('thinking') else 'disabled'}
        elif host.endswith('.aliyuncs.com'):
            if thinking_only(config['model']):
                payload['enable_thinking'] = True
            elif config.get('supports_thinking') is not False:
                payload['enable_thinking']=bool(body.get('thinking'))
        if body.get('tools'):
            payload['tools']=[{'type':'function','function':{k:v for k,v in tool.items() if k!='type'}} for tool in body['tools']]
        request = urllib.request.Request(config['url'], data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json', 'Authorization':'Bearer '+config['key']})
        started = time.monotonic()
        text, reasoning, calls, finished = '', '', {}, False
        with urllib.request.urlopen(request, timeout=60) as response:
            for line in response:
                if time.monotonic()-started > 120:
                    raise TimeoutError('模型响应超时')
                if not line.startswith(b'data:'):
                    continue
                data = line[5:].strip()
                if data == b'[DONE]':
                    break
                event = json.loads(data)
                if event.get('error'):
                    raise RuntimeError('模型服务返回错误')
                for choice in event.get('choices',[]):
                    delta=choice.get('delta',{})
                    if delta.get('reasoning_content'):
                        reasoning+=delta['reasoning_content']
                        yield {'type':'response.reasoning_text.delta','delta':delta['reasoning_content']}
                    if delta.get('content'):
                        text+=delta['content']
                        yield {'type':'response.output_text.delta','delta':delta['content']}
                    for fragment in delta.get('tool_calls',[]):
                        item=calls.setdefault(fragment['index'],{'id':'','name':'','arguments':''})
                        if fragment.get('id'):item['id']=fragment['id']
                        function=fragment.get('function',{})
                        item['name']+=function.get('name') or ''
                        item['arguments']+=function.get('arguments') or ''
                    reason=choice.get('finish_reason')
                    if reason:
                        if reason not in ('stop','tool_calls'):
                            raise RuntimeError('模型响应未完整生成：'+reason)
                        finished=True
        if not finished:
            raise RuntimeError('模型响应流提前结束')
        if text or reasoning:
            yield {'type':'response.output_item.done','item':{'type':'message','role':'assistant','content':text,'reasoning_content':reasoning}}
        for index in sorted(calls):
            item=calls[index]
            yield {'type':'response.output_item.done','item':{'type':'function_call','call_id':item['id'],
                'name':item['name'],'arguments':item['arguments']}}

    @staticmethod
    def messages(body):
        messages=[]
        if body.get('instructions'):messages.append({'role':'system','content':body['instructions']})
        items=body.get('input',[])
        if isinstance(items,str):items=[{'role':'user','content':items}]
        for item in items:
            if item.get('type')=='function_call':
                if not messages or messages[-1]['role']!='assistant':messages.append({'role':'assistant','content':None,'reasoning_content':''})
                messages[-1].setdefault('tool_calls',[]).append({'id':item['call_id'],'type':'function',
                    'function':{'name':item['name'],'arguments':item['arguments']}})
            elif item.get('type')=='function_call_output':
                messages.append({'role':'tool','tool_call_id':item['call_id'],'content':item['output']})
            else:
                content=item.get('content','')
                if isinstance(content,list):content=''.join(part.get('text','') for part in content)
                message={'role':item['role'],'content':content}
                if item['role']=='assistant':message['reasoning_content']=item.get('reasoning_content','')
                messages.append(message)
        return messages

    def json(self, instructions, payload):
        text = ''.join(e.get('delta','') for e in self.events({'instructions':instructions,
            'input':[{'role':'user','content':json.dumps(payload, ensure_ascii=False)}], 'max_output_tokens':7000})
            if e.get('type')=='response.output_text.delta').strip()
        if text.startswith('```'):
            text = text.split('\n',1)[1].rsplit('```',1)[0].strip()
        return json.loads(text)

"""Select a review model on the existing endpoint; credentials stay server-side."""
from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit, urlencode

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from model_gateway import ModelGateway, thinking_only


class ModelSelection(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class ModelSettings:
    def __init__(self, defaults, path):
        self.defaults = defaults
        self.path = Path(path)
        self.lock = threading.RLock()
        self.active = 0
        self.catalog = []
        self.selected = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.router = APIRouter(prefix='/api/models', tags=['models'])
        self.router.add_api_route('', self.status, methods=['GET'])
        self.router.add_api_route('', self.select, methods=['PUT'])
        self.router.add_api_route('/catalog', self.list_models, methods=['GET'])
        self.router.add_api_route('/test', self.test_connection, methods=['POST'])

    def config(self):
        config = dict(self.defaults())
        with self.lock:
            if self.selected.get('url') == config['url']:
                config.update({k: self.selected[k] for k in ('model', 'supports_thinking') if k in self.selected})
        return config

    def status(self):
        config = self.config()
        return {'model': config['model'], 'url': config['url'],
                'configured': bool(config['enabled'] and config['key']),
                'thinking_only': thinking_only(config['model']), 'active_requests': self.active}

    @contextmanager
    def activity(self):
        # A report can make many parallel calls; don't change its model mid-run.
        with self.lock:
            self.active += 1
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1

    def list_models(self):
        config = self.config()
        if not config['key']:
            raise HTTPException(409, '请先在服务端配置 API 密钥。')
        endpoint = urlsplit(config['url'])
        aliyun = (endpoint.hostname or '').endswith('.aliyuncs.com')
        base = urlunsplit((endpoint.scheme, endpoint.netloc, '', '', ''))
        rows, page = [], 1
        try:
            while True:
                url = (base + '/api/v1/models?' + urlencode({'capabilities':'TG', 'page_size':100, 'page_no':page})
                       if aliyun else config['url'].removesuffix('/chat/completions') + '/models')
                request = urllib.request.Request(url, headers={'Authorization':'Bearer ' + config['key']})
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = json.load(response)
                if aliyun:
                    output = data['output']
                    items = output['models']
                else:
                    items = data['data']
                for item in items:
                    model = item.get('model') or item.get('id')
                    if model:
                        rows.append({'id':model, 'provider':item.get('provider','其他'),
                                     'supports_thinking':('Reasoning' in item['capabilities']) if 'capabilities' in item else None,
                                     'tool_calling':('function-calling' in item['features']) if 'features' in item else None,
                                     'thinking_only':thinking_only(model)})
                if not aliyun or len(rows) >= output['total']:
                    break
                if not items:
                    raise ValueError('模型目录分页未完成')
                page += 1
        except urllib.error.HTTPError as exc:
            raise HTTPException(502, f'模型目录请求失败（HTTP {exc.code}），仍可手动填写模型 ID。') from None
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(502, '无法读取模型目录，请检查 API 连接；仍可手动填写模型 ID。') from None
        with self.lock:
            self.catalog = rows
        return {'models':rows, 'source':'api', 'note':'目录不代表当前密钥已开通每个模型，请使用连接测试确认。'}

    def candidate(self, payload):
        model = payload.model.strip()
        if not model or any(c.isspace() for c in model):
            raise HTTPException(422, '请输入有效模型 ID，不能包含空白字符。')
        config = self.config()
        config.pop('supports_thinking', None)
        config['model'] = model
        info = next((m for m in self.catalog if m['id'] == model), None)
        if info and info['supports_thinking'] is not None:
            config['supports_thinking'] = info['supports_thinking']
        return config

    def select(self, payload: ModelSelection):
        with self.lock:
            if self.active:
                raise HTTPException(409, '当前评审或对话仍在处理，请完成后再切换模型。')
            config = self.candidate(payload)
            selected = {k:config[k] for k in ('url','model','supports_thinking') if k in config}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
            self.selected = selected
        return self.status()

    def test_connection(self, payload: ModelSelection):
        config = self.candidate(payload)
        if not config['enabled'] or not config['key']:
            raise HTTPException(409, '模型服务尚未启用或尚未配置 API 密钥。')
        started = time.monotonic()
        try:
            result = ModelGateway(lambda:config).json('连接测试。只输出JSON：{"ok":true}，不要解释。', {})
            if result.get('ok') is not True:
                raise ValueError('未返回预期结果')
        except urllib.error.HTTPError as exc:
            raise HTTPException(502, f'模型连接测试失败（HTTP {exc.code}），请核对模型 ID、开通状态和账户额度。') from None
        except (OSError, ValueError, RuntimeError, AttributeError):
            raise HTTPException(502, '模型未完成 JSON 连接测试，请检查模型能力和 API 服务。') from None
        return {'ok':True, 'model':config['model'], 'duration_ms':round((time.monotonic()-started)*1000),
                'message':'JSON连接测试通过；尚未改变当前模型。'}


class ModelActivityMiddleware:
    """Keep a global selection fixed until active API work (including streams) ends."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        path = scope.get('path','')
        busy = (scope['type'] == 'http' and scope.get('method') in ('POST','PUT','PATCH','DELETE')
                and path.startswith('/api/') and not (path == '/api/models' or path.startswith('/api/models/')))
        if busy:
            with self.settings.activity():
                await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)

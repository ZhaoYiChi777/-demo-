import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from model_gateway import ModelGateway
from model_settings import ModelActivityMiddleware, ModelSelection, ModelSettings


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.defaults = dict(enabled=True, key='test-secret', model='deepseek-v4.1-flash',
                             url='https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions')
        self.path = Path(self.temp.name) / 'models.json'
        self.settings = ModelSettings(lambda:dict(self.defaults), self.path)
        app = FastAPI()
        app.include_router(self.settings.router)
        self.client = TestClient(app)

    def test_save_restart_and_no_key_in_responses_or_file(self):
        reply = self.client.put('/api/models', json={'model':'kimi-k3'})
        self.assertEqual(reply.status_code, 200)
        restarted = ModelSettings(lambda:dict(self.defaults), self.path)
        self.assertEqual(restarted.config()['model'], 'kimi-k3')
        self.assertEqual(restarted.config()['key'], 'test-secret')
        self.assertNotIn('test-secret', reply.text + self.path.read_text())
        self.defaults['url'] = 'https://other.test/v1/chat/completions'
        self.assertEqual(restarted.config()['model'], 'deepseek-v4.1-flash')

    def test_catalog_pagination_and_thinking_capability(self):
        pages = [dict(output=dict(total=2, models=[dict(model='qwen-max',provider='qwen',capabilities=['TG'],features=['function-calling'])])),
                 dict(output=dict(total=2, models=[dict(model='glm-5.3',provider='zhipu-ai',capabilities=['Reasoning'],features=['function-calling'])]))]
        with patch('urllib.request.urlopen', side_effect=[io.BytesIO(json.dumps(p).encode()) for p in pages]) as request:
            reply = self.client.get('/api/models/catalog')
        self.assertEqual(reply.status_code,200)
        self.assertEqual(len(reply.json()['models']),2)
        self.assertIn('page_no=2', request.call_args.args[0].full_url)
        self.client.put('/api/models',json={'model':'qwen-max'})
        self.assertIs(self.settings.config()['supports_thinking'],False)
        self.client.put('/api/models',json={'model':'custom-model'})
        self.assertNotIn('supports_thinking',self.settings.config())

    def test_generic_catalog_and_invalid_model(self):
        self.defaults['url']='https://example.test/v1/chat/completions'
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'{"data":[{"id":"qwen-test"}]}')) as request:
            self.assertEqual(self.client.get('/api/models/catalog').json()['models'][0]['id'],'qwen-test')
        self.assertEqual(request.call_args.args[0].full_url,'https://example.test/v1/models')
        self.assertEqual(self.client.put('/api/models',json={'model':'  '}).status_code,422)
        self.assertEqual(self.client.put('/api/models',json={'model':'bad model'}).status_code,422)

    def test_failed_catalog_does_not_prevent_custom_selection(self):
        with patch('urllib.request.urlopen',side_effect=OSError('offline')):
            self.assertEqual(self.client.get('/api/models/catalog').status_code,502)
        self.assertEqual(self.client.put('/api/models',json={'model':'glm-5.2'}).status_code,200)

    def test_connection_test_uses_candidate_without_switching(self):
        stream = b'data: {"choices":[{"delta":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        with patch('urllib.request.urlopen',return_value=io.BytesIO(stream)) as request:
            reply = self.client.post('/api/models/test',json={'model':'qwen3.8-flash'})
        self.assertEqual(reply.status_code,200)
        self.assertEqual(json.loads(request.call_args.args[0].data)['model'],'qwen3.8-flash')
        self.assertEqual(self.settings.config()['model'],'deepseek-v4.1-flash')
        self.assertFalse(self.path.exists())

    def test_streaming_activity_blocks_switch_until_final_body(self):
        async def stream(scope, receive, send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'working','more_body':True})
            with self.assertRaises(HTTPException) as error:
                self.settings.select(ModelSelection(model='glm-5.2'))
            self.assertEqual(error.exception.status_code,409)
            await send({'type':'http.response.body','body':b'done','more_body':False})
        async def send(message):
            pass
        asyncio.run(ModelActivityMiddleware(stream,self.settings)({'type':'http','method':'POST','path':'/api/workbench/projects/p/agent/chat'},None,send))
        self.assertEqual(self.settings.active,0)
        self.settings.select(ModelSelection(model='glm-5.2'))
        self.assertEqual(self.settings.config()['model'],'glm-5.2')

    def test_activity_released_on_error(self):
        with self.assertRaises(ValueError):
            with self.settings.activity():
                raise ValueError('failed')
        self.assertEqual(self.settings.active,0)

    def test_provider_thinking_payloads(self):
        scenarios = [('qwen-max',False,True,None), ('qwen3.8-flash',True,False,False),
                     ('kimi-k3',True,False,False), ('glm-5.2',True,True,True),
                     ('glm-5.3',True,False,True), ('ZHIPU/GLM-5.3-Flash',True,False,True),
                     ('kimi/kimi-k2.7-code',True,False,True), ('qwen3-235b-a22b-thinking-2507',True,False,True)]
        for model,supports,thinking,expected in scenarios:
            with self.subTest(model=model):
                config={**self.defaults,'model':model,'supports_thinking':supports}
                stream=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
                with patch('urllib.request.urlopen',return_value=io.BytesIO(stream)) as request:
                    list(ModelGateway(lambda:config).events({'input':'test','thinking':thinking}))
                payload=json.loads(request.call_args.args[0].data)
                if expected is None:self.assertNotIn('enable_thinking',payload)
                else:self.assertIs(payload['enable_thinking'],expected)


if __name__ == '__main__':
    unittest.main()

import io
import json
import unittest
from unittest.mock import patch
from model_gateway import ModelGateway


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.gateway=ModelGateway(lambda:dict(url='https://example.test/chat/completions',key='test',model='deepseek-v4-pro'))

    def stream(self, chunks):
        return io.BytesIO(b''.join(b'data: '+json.dumps(c).encode()+b'\n\n' for c in chunks)+b'data: [DONE]\n\n')

    def test_fragmented_tool_call_and_followup_messages(self):
        chunks=[{'choices':[{'delta':{'content':'正在检索','tool_calls':[{'index':0,'id':'call1','function':{'name':'search_evidence','arguments':'{"query":'}}]},'finish_reason':None}]},
                {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"设备"}'}}]},'finish_reason':'tool_calls'}]}]
        with patch('urllib.request.urlopen',return_value=self.stream(chunks)) as request:
            events=list(self.gateway.events({'instructions':'规则','input':[{'role':'user','content':'检索'}],
                'tools':[{'type':'function','name':'search_evidence','parameters':{'type':'object'}}]}))
            payload=json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['model'],'deepseek-v4-pro')
        self.assertNotIn('enable_thinking',payload)
        self.assertEqual(payload['tools'][0]['function']['name'],'search_evidence')
        items=[e['item'] for e in events if e['type']=='response.output_item.done']
        self.assertEqual(json.loads(items[-1]['arguments']),{'query':'设备'})
        messages=self.gateway.messages({'input':[{'role':'user','content':'检索'},*items,{'type':'function_call_output','call_id':'call1','output':'找到证据'}]})
        self.assertEqual([m['role'] for m in messages],['user','assistant','tool'])
        self.assertEqual(messages[1]['tool_calls'][0]['id'],messages[2]['tool_call_id'])
        self.assertEqual(messages[1]['content'],'正在检索')

    def test_json_response_and_incomplete_stream(self):
        chunks=[{'choices':[{'delta':{'content':'{"ok":true}'},'finish_reason':'stop'}]}]
        with patch('urllib.request.urlopen',return_value=self.stream(chunks)):
            self.assertEqual(self.gateway.json('只返回 JSON',{}),{'ok':True})
        for reason in (None,'length'):
            with patch('urllib.request.urlopen',return_value=self.stream([{'choices':[{'delta':{'content':'截断'},'finish_reason':reason}]}])):
                with self.assertRaises(RuntimeError):list(self.gateway.events({'input':'检查'}))

    def test_deepseek_native_thinking_parameter(self):
        gateway=ModelGateway(lambda:dict(url='https://api.deepseek.com/chat/completions',key='test',model='deepseek-flash'))
        chunks=[{'choices':[{'delta':{'content':'连接成功'},'finish_reason':'stop'}]}]
        with patch('urllib.request.urlopen',return_value=self.stream(chunks)) as request:
            list(gateway.events({'input':'连接检查'}))
            payload=json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['thinking'],{'type':'disabled'})
        self.assertNotIn('enable_thinking',payload)
        self.assertEqual(payload['model'],'deepseek-flash')

    def test_legacy_synthesis_uses_shared_gateway(self):
        import main
        result={'findings':[{'title':'核验验收指标','judgement':'缺少依据','suggestion':'补充材料'}],'conclusion':'少量修改后通过'}
        with patch.object(main,'LLM_API_KEY','test'),patch.object(main,'LLM_ENABLED',True),patch.object(main.model_gateway,'json',return_value=result) as model:
            actual=main.llm_review_synthesis({'name':'测试'},'测试材料',[{'title':'原问题','evidence':[]}],'不通过')
        self.assertEqual(actual[1],'少量修改后通过')
        model.assert_called_once()

    def test_reasoning_only_tool_round_is_preserved(self):
        chunks=[{'choices':[{'delta':{'reasoning_content':'先读取当前资料'},'finish_reason':None}]},
                {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call1','function':{'name':'get_review_context','arguments':'{}'}}]},'finish_reason':'tool_calls'}]}]
        gateway=ModelGateway(lambda:dict(url='https://api.deepseek.com/chat/completions',key='test',model='deepseek-flash'))
        with patch('urllib.request.urlopen',return_value=self.stream(chunks)) as request:
            events=list(gateway.events({'input':'检查','thinking':True}))
            self.assertEqual(json.loads(request.call_args.args[0].data)['thinking'],{'type':'enabled'})
        self.assertEqual(events[0]['type'],'response.reasoning_text.delta')
        items=[e['item'] for e in events if e['type']=='response.output_item.done']
        messages=gateway.messages({'input':items+[{'type':'function_call_output','call_id':'call1','output':'当前资料'}]})
        self.assertEqual(messages[0]['reasoning_content'],'先读取当前资料')
        self.assertEqual(messages[0]['tool_calls'][0]['id'],'call1')
        self.assertEqual(messages[1]['role'],'tool')

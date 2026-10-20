import io
import json
import unittest
from unittest.mock import patch
from PIL import Image
import local_ocr


class CloudOcrTests(unittest.TestCase):
    def test_cloud_payload_and_result(self):
        response = io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'设备费120万元'}}]}).encode())
        env = dict(OCR_MODEL='qwen-vl-ocr', DASHSCOPE_API_KEY='test', ALIYUN_BASE_URL='https://example.test/v1', LLM_ENABLED='true')
        with patch.dict('os.environ',env), patch('urllib.request.urlopen',return_value=response) as call, patch.object(local_ocr,'local_image_text') as fallback:
            self.assertEqual(local_ocr.image_text(Image.new('RGB',(100,100))), '设备费120万元')
        body = json.loads(call.call_args.args[0].data)
        self.assertEqual(body['model'],'qwen-vl-ocr')
        self.assertTrue(body['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/png;base64,'))
        fallback.assert_not_called()

    def test_failure_falls_back_without_logging_response_body(self):
        with patch.object(local_ocr,'cloud_image_text',side_effect=TimeoutError('private response')), patch.object(local_ocr,'local_image_text',return_value='本地文字') as fallback, self.assertLogs('local_ocr') as log:
            self.assertEqual(local_ocr.image_text(None),'本地文字')
        fallback.assert_called_once()
        self.assertNotIn('private response',' '.join(log.output))

    def test_unconfigured_cloud_keeps_local_ocr(self):
        with patch.dict('os.environ',{'OCR_MODEL':''}), patch.object(local_ocr,'local_image_text',return_value='本地文字'):
            self.assertEqual(local_ocr.image_text(None),'本地文字')

    def test_truncated_cloud_response_falls_back(self):
        response = io.BytesIO(json.dumps({'choices':[{'finish_reason':'length','message':{'content':'部分内容'}}]}).encode())
        env = dict(OCR_MODEL='qwen-vl-ocr', DASHSCOPE_API_KEY='test', ALIYUN_BASE_URL='https://example.test/v1', LLM_ENABLED='true')
        with patch.dict('os.environ',env), patch('urllib.request.urlopen',return_value=response), patch.object(local_ocr,'local_image_text',return_value='完整本地文字'):
            self.assertEqual(local_ocr.image_text(Image.new('RGB',(100,100))),'完整本地文字')

"""Configured Qwen OCR with local RapidOCR fallback; synchronous page processing."""
import base64
import io
import json
import logging
import os
import threading
import urllib.request
from functools import lru_cache

_lock = threading.Lock()


@lru_cache(maxsize=1)
def engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)


def cloud_image_text(image):
    model = os.getenv('OCR_MODEL', '').strip()
    key = os.getenv('DASHSCOPE_API_KEY', '').strip()
    base = os.getenv('ALIYUN_BASE_URL', '').rstrip('/')
    if not (model and key and base) or os.getenv('LLM_ENABLED', 'true').lower() in ('0', 'false', 'no'):
        return None
    buffer = io.BytesIO()
    image.convert('RGB').save(buffer, format='PNG')
    data_url = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
    payload = dict(model=model, stream=False, messages=[dict(role='user', content=[
        dict(type='image_url', image_url=dict(url=data_url)),
        dict(type='text', text='Read all the text in the image.')])])
    request = urllib.request.Request(base + '/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type':'application/json', 'Authorization':'Bearer ' + key})
    with urllib.request.urlopen(request, timeout=60) as response:
        choice = json.load(response)['choices'][0]
    text = choice['message']['content']
    if choice.get('finish_reason') != 'stop' or not isinstance(text, str) or not text.strip():
        raise ValueError('OCR response incomplete or empty')
    return text


def local_image_text(image):
    import numpy as np
    with _lock:
        rows, _ = engine()(np.array(image.convert('RGB')))
    return '\n'.join(row[1] for row in (rows or []))


def image_text(image):
    try:
        text = cloud_image_text(image)
        if text is not None:
            return text
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        logging.getLogger(__name__).warning('Cloud OCR unavailable; using RapidOCR (%s)', type(exc).__name__)
    return local_image_text(image)


def read_image(data):
    from PIL import Image
    return image_text(Image.open(io.BytesIO(data)))


def read_pdf(data, direct_pages):
    import pypdfium2 as pdfium
    pages = []
    with pdfium.PdfDocument(data) as pdf:
        for index in range(len(pdf)):
            text = direct_pages[index].strip()
            method = ''
            if len(text) < 100:
                page = pdf[index]
                bitmap = page.render(scale=2.5)
                try:
                    recognized = image_text(bitmap.to_pil())
                finally:
                    bitmap.close()
                    page.close()
                if recognized.strip():
                    text, method = recognized, ' OCR'
                elif not text:
                    # Keep the page marker: an empty drawing/blank page is never invented as text.
                    text = '【此页未识别出文字，请核对原页图像】'
            pages.append(f'[第{index + 1}页{method}] {text}')
    return '\n'.join(pages)

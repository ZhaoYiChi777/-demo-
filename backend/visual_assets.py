"""Upload-time visual candidates with source locations, kept separate from review text."""
import csv
import io
import json
import re
from pathlib import Path
from urllib.parse import quote

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

IMAGE_EXTENSIONS = {'.png','.jpg','.jpeg','.bmp','.tif','.tiff','.webp','.gif'}
CHART = '{http://schemas.openxmlformats.org/drawingml/2006/chart}chart'
CAPTION = re.compile(r'(?:图|表)\s*\d+|\b(?:Figure|Table)\s+\d+', re.I)


def init_assets(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS visual_assets(
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL,
        project_id TEXT NOT NULL, document_id INTEGER, kind TEXT NOT NULL,
        needs_recognition INTEGER NOT NULL, payload TEXT NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_visual_source ON visual_assets(source_id)')
    columns={r['name'] for r in conn.execute('PRAGMA table_info(document_sources)')}
    for name,definition in [('asset_scan_status', "TEXT NOT NULL DEFAULT 'not_scanned'"),
                            ('asset_scan_error','TEXT'),('asset_count','INTEGER NOT NULL DEFAULT 0'),
                            ('asset_original_path','TEXT')]:
        if name not in columns:
            conn.execute(f'ALTER TABLE document_sources ADD COLUMN {name} {definition}')


class Collector:
    def __init__(self, folder):
        self.folder=folder
        self.items=[]
        self.errors=[]

    def add(self, kind, location, context, files, *, rows=None, reasons=None):
        number=len(self.items)+1
        saved={}
        for role,(extension,data) in files.items():
            name=f'item-{number:04d}-{role}{extension}'
            (self.folder/name).write_bytes(data)
            saved[role]=name
        item=dict(kind=kind,location=location,context=context,files=saved,
                  needs_recognition=kind!='table',status='extracted' if kind=='table' else 'pending_recognition',
                  reasons=reasons or [],rows=rows)
        self.items.append(item)


def paragraph_text(element, doc):
    if element.tag==qn('w:p'):
        return Paragraph(element,doc).text
    if element.tag==qn('w:tbl'):
        return ''
    return '\n'.join(Paragraph(p,doc).text for p in element.iter(qn('w:p'))
                     if not any(a.tag==qn('w:tbl') for a in p.iterancestors()))


def scan_docx(data, collector):
    doc=Document(io.BytesIO(data))
    blocks=list(doc.element.body)
    for index,element in enumerate(blocks):
        before=[paragraph_text(e,doc) for e in blocks[max(0,index-2):index]]
        after=[paragraph_text(e,doc) for e in blocks[index+1:index+3]]
        own=paragraph_text(element,doc)
        context='\n'.join(t for t in before+[own]+after if t.strip())
        location=dict(page=None,block_index=index+1,label=f'正文第{index+1}块',basis='docx_body_order')
        for table_index,table_element in enumerate(element.iter(qn('w:tbl')),start=1):
            table=Table(table_element,doc)
            rows=[[cell.text for cell in row.cells] for row in table.rows]
            # Preserve merged-cell information in the original XML as well as a usable grid.
            from lxml import etree
            output=io.StringIO(newline='');csv.writer(output).writerows(rows)
            table_location={**location,'table_index':table_index,'label':f'正文第{index+1}块 · 表{table_index}'}
            collector.add('table',table_location,context,{
                'table':('.json',json.dumps({'rows':rows,'context':context},ensure_ascii=False,indent=2).encode('utf-8')),
                'csv':('.csv',output.getvalue().encode('utf-8-sig')),
                'structure':('.xml',etree.tostring(table_element,encoding='utf-8'))},rows=rows,reasons=['native_table'])
        for drawing in element.iter():
            if drawing.tag in (qn('a:blip'),'{urn:schemas-microsoft-com:vml}imagedata'):
                rid=drawing.get(qn('r:embed')) or drawing.get(qn('r:id'))
                if not rid:
                    collector.errors.append(f'正文第{index+1}块有外链图片，文件内没有图像数据')
                    continue
                part=doc.part.related_parts[rid]
                extension=Path(str(part.partname)).suffix.lower()
                files={'original':(extension,part.blob)}
                if extension in IMAGE_EXTENSIONS:
                    from PIL import Image
                    with Image.open(io.BytesIO(part.blob)) as im:
                        preview=io.BytesIO();im.convert('RGB').save(preview,format='PNG')
                    files['preview']=('.png',preview.getvalue())
                collector.add('image',location,context,files,reasons=['embedded_image'])
            elif drawing.tag==CHART:
                part=doc.part.related_parts[drawing.get(qn('r:id'))]
                files={'chart':('.xml',part.blob)}
                for rel in part.rels.values():
                    if not rel.is_external and str(rel.target_part.partname).endswith('.xlsx'):
                        files['workbook']=('.xlsx',rel.target_part.blob)
                collector.add('chart',location,context,files,reasons=['native_chart'])
        tags={e.tag for e in element.iter()}
        if (qn('w:drawing') in tags or qn('w:pict') in tags) and not tags.intersection({qn('a:blip'),CHART,'{urn:schemas-microsoft-com:vml}imagedata'}):
            from lxml import etree
            collector.add('drawing',location,context,{'structure':('.xml',etree.tostring(element,encoding='utf-8'))},reasons=['native_drawing'])
        if '{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath' in tags:
            from lxml import etree
            collector.add('formula',location,context,{'structure':('.xml',etree.tostring(element,encoding='utf-8'))},reasons=['native_formula'])


def scan_pdf(data, collector):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(data) as pdf:
        for index in range(len(pdf)):
            page=pdf[index]
            try:
                textpage=page.get_textpage()
                try:
                    text=textpage.get_text_range()
                finally:
                    textpage.close()
                objects=list(page.get_objects())
                images=sum(o.type==pdfium.raw.FPDF_PAGEOBJ_IMAGE for o in objects)
                paths=sum(o.type==pdfium.raw.FPDF_PAGEOBJ_PATH for o in objects)
                reasons=[]
                if images:reasons.append('embedded_image')
                if paths:reasons.append('vector_graphics_or_table')
                if CAPTION.search(text):reasons.append('figure_or_table_caption')
                if 0<len(text.strip())<100:reasons.append('sparse_text')
                if not reasons:
                    continue
                bitmap=page.render(scale=2)
                try:
                    output=io.BytesIO();bitmap.to_pil().save(output,format='PNG')
                finally:
                    bitmap.close()
                width,height=page.get_size()
                collector.add('visual_page',dict(page=index+1,block_index=None,label=f'第{index+1}页',
                              bbox=[0,0,width,height],basis='pdf_page'),text,
                              {'preview':('.png',output.getvalue()),'text':('.txt',text.encode('utf-8'))},
                              reasons=reasons)
            except Exception as exc:
                collector.errors.append(f'第{index+1}页：{type(exc).__name__}: {exc}')
            finally:
                page.close()


def collect_assets(conn, data_dir, source_id, filename, data, converted_path=None):
    source=conn.execute('SELECT * FROM document_sources WHERE id=?',(source_id,)).fetchone()
    extension=Path(filename).suffix.lower()
    if extension not in {'.pdf','.docx','.doc'} | IMAGE_EXTENSIONS:
        conn.execute("UPDATE document_sources SET asset_scan_status='not_applicable' WHERE id=?",(source_id,))
        return dict(status='not_applicable',count=0)
    folder=Path(data_dir)/'visual_assets'/f'source-{source_id}'
    folder.mkdir(parents=True,exist_ok=True)
    original=folder/('source'+extension)
    original.write_bytes(data)
    collector=Collector(folder)
    try:
        if extension=='.doc':
            if not converted_path:
                raise ValueError('旧版Word转换未完成，原文件已保存，待转换后提取图表')
            data=(Path(data_dir)/converted_path).read_bytes()
            (folder/'converted.docx').write_bytes(data)
            scan_docx(data,collector)
        elif extension=='.docx':
            scan_docx(data,collector)
        elif extension=='.pdf':
            scan_pdf(data,collector)
        else:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                for frame in range(getattr(im,'n_frames',1)):
                    im.seek(frame);output=io.BytesIO();im.convert('RGB').save(output,format='PNG')
                    collector.add('image',dict(page=frame+1,block_index=None,label=f'第{frame+1}帧',basis='image_frame'),'',
                                  {'preview':('.png',output.getvalue())},reasons=['uploaded_image'])
    except Exception as exc:
        collector.errors.append(f'{type(exc).__name__}: {exc}')
    status=('partial' if collector.items else 'failed') if collector.errors else 'complete'
    items=[]
    for item in collector.items:
        item['files']={role:str((folder/name).relative_to(data_dir)) for role,name in item['files'].items()}
        cursor=conn.execute('INSERT INTO visual_assets(source_id,project_id,document_id,kind,needs_recognition,payload) VALUES(?,?,?,?,?,?)',
                            (source_id,source['project_id'],source['document_id'],item['kind'],int(item['needs_recognition']),json.dumps(item,ensure_ascii=False)))
        items.append({**item,'id':cursor.lastrowid,'citation_id':f'VIS-{cursor.lastrowid}'})
    manifest=dict(source_id=source_id,document_id=source['document_id'],filename=filename,source_path=source['source_path'],
                  status=status,errors=collector.errors,items=items)
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    conn.execute('UPDATE document_sources SET asset_scan_status=?,asset_scan_error=?,asset_count=?,asset_original_path=? WHERE id=?',
                 (status,'；'.join(collector.errors) or None,len(items),str(original.relative_to(data_dir)),source_id))
    return dict(status=status,count=len(items),needs_recognition=sum(i['needs_recognition'] for i in items))


def asset_payload(row):
    item=json.loads(row['payload'])
    prefix=f"/api/projects/{quote(row['project_id'],safe='')}/visual-assets/{row['id']}"
    return {**item,'id':row['id'],'source_id':row['source_id'],'document_id':row['document_id'],
            'citation_id':f"VIS-{row['id']}",'filename':row['filename'],'source_path':row['source_path'],
            'citation':f"[VIS-{row['id']}] {row['filename']} · {item['location']['label']}",
            'files':{role:f'{prefix}/files/{role}' for role in item['files']},
            'source_url':f"/api/projects/{quote(row['project_id'],safe='')}/sources/{row['source_id']}/visual-original"}


def assets_router(db, data_dir):
    router=APIRouter()

    def file_response(path):
        root=Path(data_dir()).resolve()
        target=(root/path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404,'保存的图表文件不存在')
        return FileResponse(target)

    @router.get('/api/projects/{project_id}/sources/{source_id}/visual-assets')
    def source_assets(project_id: str, source_id: int):
        with db() as conn:
            source=conn.execute('SELECT * FROM document_sources WHERE project_id=? AND id=?',(project_id,source_id)).fetchone()
            if not source:raise HTTPException(404,'来源材料不存在')
            rows=conn.execute('SELECT a.*,s.filename,s.source_path FROM visual_assets a JOIN document_sources s ON a.source_id=s.id WHERE a.project_id=? AND a.source_id=? ORDER BY a.id',(project_id,source_id)).fetchall()
        prefix=f'/api/projects/{quote(project_id,safe="")}/sources/{source_id}'
        return dict(status=source['asset_scan_status'],error=source['asset_scan_error'],items=[asset_payload(r) for r in rows],
                    source_url=prefix+'/visual-original' if source['asset_original_path'] else None,
                    manifest_url=prefix+'/visual-manifest' if source['asset_original_path'] else None)

    @router.get('/api/projects/{project_id}/visual-assets/{asset_id}/files/{role}')
    def asset_file(project_id: str, asset_id: int, role: str):
        with db() as conn:
            row=conn.execute('SELECT payload FROM visual_assets WHERE project_id=? AND id=?',(project_id,asset_id)).fetchone()
        path=json.loads(row['payload'])['files'].get(role) if row else None
        if not path:raise HTTPException(404,'图表文件不存在')
        return file_response(path)

    @router.get('/api/projects/{project_id}/sources/{source_id}/visual-original')
    def source_original(project_id: str, source_id: int):
        with db() as conn:
            row=conn.execute('SELECT asset_original_path FROM document_sources WHERE project_id=? AND id=?',(project_id,source_id)).fetchone()
        if not row or not row['asset_original_path']:raise HTTPException(404,'该材料尚未单独保存原文件，请重新上传')
        return file_response(row['asset_original_path'])

    @router.get('/api/projects/{project_id}/sources/{source_id}/visual-manifest')
    def source_manifest(project_id: str, source_id: int):
        with db() as conn:
            row=conn.execute('SELECT asset_original_path FROM document_sources WHERE project_id=? AND id=?',(project_id,source_id)).fetchone()
        if not row or not row['asset_original_path']:raise HTTPException(404,'该材料尚未提取图表')
        return file_response(Path(row['asset_original_path']).parent/'manifest.json')

    return router

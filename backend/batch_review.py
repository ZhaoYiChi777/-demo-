"""Create one project per group and review each report in its own material package."""
import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from workbench import RunInput, now


class BatchItemInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    filename: str = Field(min_length=1)
    brief: str = ""


class BatchInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    project_type: str = "技术改造"
    brief: str = ""
    items: list[BatchItemInput] = Field(min_length=1, max_length=50)


class BatchReviews:
    def __init__(self, workbench, upload, classify):
        self.wb, self.upload, self.classify = workbench, upload, classify
        self.router = APIRouter(prefix="/api/workbench")
        self.router.add_api_route('/batches', self.create, methods=['POST'])
        self.router.add_api_route('/batches', self.list_batches, methods=['GET'])
        self.router.add_api_route('/project-comparison', self.comparison, methods=['GET'])
        self.router.add_api_route('/batches/{batch_id}/items/{project_id}/upload', self.upload_item, methods=['POST'])
        self.router.add_api_route('/batches/{batch_id}/items/{project_id}/run', self.run_item, methods=['POST'])

    def init_tables(self):
        with self.wb.db() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS wb_batches(id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wb_batch_items(batch_id TEXT NOT NULL, project_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL, position INTEGER NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                    uploaded INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS wb_group_reports(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
                    project_id TEXT NOT NULL, material_package_id INTEGER NOT NULL, name TEXT NOT NULL,
                    filename TEXT NOT NULL, position INTEGER NOT NULL, status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', uploaded INTEGER NOT NULL DEFAULT 0);
            ''')
            if 'project_id' not in {r['name'] for r in conn.execute('PRAGMA table_info(wb_batches)')}:
                conn.execute('ALTER TABLE wb_batches ADD COLUMN project_id TEXT')

    def create(self, payload: BatchInput):
        if not payload.name.strip() or any(not i.name.strip() for i in payload.items):
            raise HTTPException(422, '请填写项目组名称和每份报告名称。')
        batch_id = 'BATCH-' + uuid4().hex
        project_id = 'PRJ-' + uuid4().hex
        items = []
        with self.wb.db() as conn:
            conn.execute('INSERT INTO projects(id,name,project_type,created_at) VALUES(?,?,?,?)',
                         (project_id, payload.name.strip(), payload.project_type, now()))
            conn.execute('INSERT INTO wb_briefs VALUES(?,?)', (project_id, payload.brief.strip()))
            conn.execute('INSERT INTO wb_batches(id,name,created_at,project_id) VALUES (?,?,?,?)', (batch_id, payload.name.strip(), now(), project_id))
            for position, item in enumerate(payload.items):
                report_id = 'RPT-' + uuid4().hex
                package_id = conn.execute('''INSERT INTO material_packages(project_id,package_key,name,assignment_source,created_at,updated_at)
                    VALUES(?,?,?,?,?,?)''', (project_id, report_id, item.name.strip(), 'manual', now(), now())).lastrowid
                conn.execute('''INSERT INTO wb_group_reports(id,batch_id,project_id,material_package_id,name,filename,position,status)
                    VALUES(?,?,?,?,?,?,?,?)''', (report_id, batch_id, project_id, package_id, item.name.strip(), item.filename, position, 'waiting'))
                items.append({'item_id': report_id, 'project_id': project_id, 'material_package_id': package_id, 'filename': item.filename, 'status': 'waiting'})
        return {'id': batch_id, 'project_id': project_id, 'items': items}

    def list_batches(self):
        with self.wb.db() as conn:
            rows = [dict(r) for r in conn.execute('''SELECT b.*,
                CASE WHEN b.project_id IS NOT NULL THEN (SELECT COUNT(*) FROM wb_group_reports g WHERE g.batch_id=b.id)
                ELSE (SELECT COUNT(*) FROM wb_batch_items i WHERE i.batch_id=b.id) END AS total
                FROM wb_batches b ORDER BY b.created_at DESC''')]
        if self.wb.access:
            rows = self.wb.access.visible(rows, 'project_id')
        return {'batches': rows}

    def item(self, batch_id, project_id):
        with self.wb.db() as conn:
            report = conn.execute('SELECT *,id AS item_id FROM wb_group_reports WHERE batch_id=? AND id=?', (batch_id, project_id)).fetchone()
            if report:
                return dict(report)
            row = conn.execute('SELECT * FROM wb_batch_items WHERE batch_id=? AND project_id=?', (batch_id, project_id)).fetchone()
        if not row:
            raise HTTPException(404, '未找到该批次项目。')
        return dict(row)

    def update(self, project_id, status, error='', uploaded=None):
        with self.wb.db() as conn:
            conn.execute('UPDATE wb_group_reports SET status=?,error=? WHERE id=?', (status, error, project_id))
            conn.execute('UPDATE wb_batch_items SET status=?,error=? WHERE project_id=?', (status, error, project_id))
            if uploaded is not None:
                conn.execute('UPDATE wb_group_reports SET uploaded=? WHERE id=?', (int(uploaded), project_id))
                conn.execute('UPDATE wb_batch_items SET uploaded=? WHERE project_id=?', (int(uploaded), project_id))

    async def upload_report(self, item, file):
        report_id, project_id = item['item_id'], item['project_id']
        self.update(report_id, 'uploading')
        try:
            before = {s['id'] for s in self.wb.materials(project_id)['sources']}
            await self.upload(project_id, file)
            sources = [s for s in self.wb.materials(project_id)['sources'] if s['id'] not in before]
            for source in sources:
                self.classify(project_id, source['id'], 'project_report' if source['parse_status']=='parsed' else 'other')
                with self.wb.db() as conn:
                    conn.execute('''UPDATE project_documents SET material_package_id=?,document_role='main_report',
                        assignment_status='manual_assigned',assignment_source='manual',updated_at=? WHERE project_id=? AND source_id=?''',
                        (item['material_package_id'], now(), project_id, source['id']))
            if not self.wb.materials(project_id, item['material_package_id'])['chunks']:
                raise HTTPException(409, '报告未解析出可审查原文，请更换可读取文件后重试。')
            with self.wb.db() as conn:
                conn.execute('UPDATE wb_group_reports SET filename=? WHERE id=?', (file.filename, report_id))
                # Upload may infer an extra package; remove only empty auto-created packages.
                conn.execute('''DELETE FROM material_packages WHERE project_id=? AND assignment_source='rule'
                    AND NOT EXISTS(SELECT 1 FROM project_documents pd WHERE pd.material_package_id=material_packages.id)''', (project_id,))
            self.update(report_id, 'ready', uploaded=True)
        except Exception as exc:
            message = str(exc.detail) if isinstance(exc, HTTPException) else '报告上传或解析失败，请重试。'
            self.update(report_id, 'error', message)
            raise HTTPException(409, message) from exc
        return self.item(item['batch_id'], report_id)

    async def upload_item(self, batch_id: str, project_id: str, file: UploadFile = File(...)):
        item = self.item(batch_id, project_id)
        if item['uploaded']:
            return item
        if Path(file.filename or '').suffix.lower() not in ('.docx', '.doc', '.pdf', '.txt'):
            raise HTTPException(422, '每个项目请选择一份 Word、PDF 或 TXT 报告。')
        if item.get('item_id'):
            return await self.upload_report(item, file)
        self.update(project_id, 'uploading')
        try:
            material = self.wb.materials(project_id)
            if not material['chunks']:
                await self.upload(project_id, file)
            material = self.wb.materials(project_id)
            for source in material['sources']:
                self.classify(project_id, source['id'], 'project_report' if source['parse_status']=='parsed' else 'other')
            material = self.wb.materials(project_id)
            if not material['chunks']:
                raise HTTPException(409, '报告未解析出可审查原文，请更换可读取文件后重试。')
            filename = next(s['filename'] for s in reversed(material['sources']) if s['parse_status']=='parsed')
            with self.wb.db() as conn:
                conn.execute('UPDATE wb_batch_items SET filename=? WHERE project_id=?', (filename, project_id))
            self.update(project_id, 'ready', uploaded=True)
        except Exception as exc:
            message = str(exc.detail) if isinstance(exc, HTTPException) else '报告上传或解析失败，请重试。'
            self.update(project_id, 'error', message)
            raise HTTPException(409, message) from exc
        return self.item(batch_id, project_id)

    def run_item(self, batch_id: str, project_id: str):
        item = self.item(batch_id, project_id)
        if not item['uploaded']:
            raise HTTPException(409, '请先上传该项目报告。')
        self.update(project_id, 'reviewing')
        try:
            run = self.wb.run(item['project_id'], RunInput(material_package_id=item.get('material_package_id')))
            self.update(project_id, 'completed')
            return run
        except Exception as exc:
            message = str(exc.detail) if isinstance(exc, HTTPException) else '评审未完成，请重试。'
            self.update(project_id, 'error', message)
            raise HTTPException(409, message) from exc

    def comparison(self, batch_id: str | None = None):
        with self.wb.db() as conn:
            group = conn.execute('SELECT project_id FROM wb_batches WHERE id=?', (batch_id,)).fetchone() if batch_id else None
            if group and group['project_id']:
                rows = []
                latest = {}
                for record in conn.execute('SELECT payload FROM wb_runs WHERE project_id=? ORDER BY rowid DESC', (group['project_id'],)):
                    run = json.loads(record['payload'])
                    latest.setdefault(run.get('material_package_id'), run)
                for report in conn.execute('''SELECT p.*,g.id AS item_id,g.name AS report_name,g.filename,g.material_package_id,
                    g.status AS batch_status,g.error,g.uploaded,g.batch_id FROM wb_group_reports g
                    JOIN projects p ON p.id=g.project_id WHERE g.batch_id=? ORDER BY g.position''', (batch_id,)):
                    p = dict(report)
                    rows.append({'project': p, 'run': latest.get(p['material_package_id'])})
                return {'rows': rows}
            if batch_id:
                projects = [dict(r) for r in conn.execute('SELECT p.*,i.filename,i.status AS batch_status,i.error,i.uploaded,i.batch_id FROM projects p JOIN wb_batch_items i ON i.project_id=p.id WHERE i.batch_id=? ORDER BY i.position', (batch_id,))]
            else:
                projects = [dict(r) for r in conn.execute('SELECT p.*,i.filename,i.status AS batch_status,i.error,i.uploaded,i.batch_id FROM projects p LEFT JOIN wb_batch_items i ON i.project_id=p.id ORDER BY p.created_at DESC')]
            if self.wb.access:
                projects = self.wb.access.visible(projects)
            project_ids = {p['id'] for p in projects}
            latest = {}
            for row in conn.execute('SELECT project_id,payload FROM wb_runs ORDER BY rowid DESC'):
                if row['project_id'] in project_ids and row['project_id'] not in latest:
                    run = json.loads(row['payload'])
                    if run.get('material_package_id') is None:
                        latest[row['project_id']] = run
        return {'rows': [{'project': p, 'run': latest.get(p['id'])} for p in projects]}

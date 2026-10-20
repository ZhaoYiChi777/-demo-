"""Local accounts, project ownership and the administrator's work journal."""
import json
import secrets
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.routing import Match


actor = ContextVar('workbench_actor', default=None)
COOKIE = 'review_session'
CATEGORIES = {'materials': '项目与材料', 'reviews': '评审记录', 'manual': '人工处理',
              'conversations': '对话记录', 'system': '系统管理'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class AccountUpdate(BaseModel):
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)


class OwnerUpdate(BaseModel):
    username: str | None = None


class Accounts:
    def __init__(self, db, data_dir):
        self.db, self.data_dir = db, data_dir
        self.model_status = None
        self.router = APIRouter()
        for path, endpoint, methods in [
            ('/api/auth/login', self.login, ['POST']),
            ('/api/auth/me', self.me, ['GET']),
            ('/api/auth/logout', self.logout, ['POST']),
            ('/api/admin/accounts', self.accounts, ['GET']),
            ('/api/admin/accounts/{username}', self.update_account, ['PATCH']),
            ('/api/admin/projects/{project_id}/owner', self.assign, ['PUT']),
            ('/api/admin/records', self.records, ['GET']),
            ('/api/admin/records/{record_id}', self.record, ['GET']),
            ('/api/admin/overview', self.overview, ['GET']),
        ]:
            self.router.add_api_route(path, endpoint, methods=methods)

    def init_tables(self):
        key_path = Path(self.data_dir()) / '.account-key'
        if not key_path.exists():
            key_path.write_bytes(Fernet.generate_key())
        self.cipher = Fernet(key_path.read_bytes())
        with self.db() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS accounts(username TEXT PRIMARY KEY, role TEXT NOT NULL,
                    password TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS account_sessions(token TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS project_owners(project_id TEXT PRIMARY KEY, username TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS work_records(id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE,
                    created_at TEXT NOT NULL, username TEXT, category TEXT NOT NULL, action TEXT NOT NULL,
                    status TEXT NOT NULL, project_id TEXT, run_id TEXT, title TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS work_records_time ON work_records(created_at DESC,id DESC);
                CREATE TABLE IF NOT EXISTS work_journal_settings(name TEXT PRIMARY KEY);
            ''')
            for username, password, role in [('user1', '12345678', 'user'), ('manner', '00000000', 'admin')]:
                conn.execute('INSERT OR IGNORE INTO accounts(username,role,password) VALUES(?,?,?)',
                             (username, role, self.cipher.encrypt(password.encode()).decode()))
        self.import_history()

    def log(self, category, action, title='', project_id=None, run_id=None, status='success',
            detail=None, event_key=None, at=None, username=None):
        current = actor.get()
        username = username if username is not None else current['username'] if current else None
        with self.db() as conn:
            conn.execute('''INSERT OR IGNORE INTO work_records(event_key,created_at,username,category,action,status,
                project_id,run_id,title,detail) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (event_key, at or timestamp(), username, category, action, status, project_id, run_id,
                 title, json.dumps(detail or {}, ensure_ascii=False)))

    def import_history(self, force=False):
        with self.db() as conn:
            if not force and conn.execute("SELECT 1 FROM work_journal_settings WHERE name='history_imported'").fetchone():
                return
            projects = list(conn.execute('SELECT id,name,created_at FROM projects'))
            sources = list(conn.execute('SELECT id,project_id,filename,created_at FROM document_sources'))
            runs = [json.loads(r['payload']) for r in conn.execute('SELECT payload FROM wb_runs')]
            conversations = [json.loads(r['payload']) for r in conn.execute('SELECT payload FROM wb_agent_conversations')]
            conversation_projects = {r['id']: r['project_id'] for r in conn.execute('SELECT id,project_id FROM wb_agent_conversations')}
            agent_logs = [dict(r) for r in conn.execute('SELECT * FROM wb_agent_logs')]
        for p in projects:
            self.log('materials', '历史项目', p['name'], p['id'], event_key='project:' + p['id'], at=p['created_at'])
        for s in sources:
            self.log('materials', '历史上传材料', s['filename'], s['project_id'], event_key='source:' + str(s['id']), at=s['created_at'])
        for r in runs:
            self.log('reviews', '历史评审', r.get('scope_name') or r['project']['name'], r['project_id'], r['id'],
                     detail={'result': r['result'], 'archive_status': r['status'], 'legacy': True},
                     event_key='run:' + r['id'], at=r['created_at'])
            if r.get('qualification'):
                self.log('manual', '历史人工确认', r.get('scope_name') or r['project']['name'], r['project_id'], r['id'],
                         detail=r['qualification'], event_key='qualification:' + r['id'], at=r['qualification']['confirmed_at'])
            elif r.get('archived_at'):
                self.log('manual', '历史归档', r.get('scope_name') or r['project']['name'], r['project_id'], r['id'],
                         event_key='archive:' + r['id'], at=r['archived_at'])
            for check in r.get('checks', []):
                if check.get('reviewed_at'):
                    self.log('manual', '历史复核', check['rule']['name'], r['project_id'], r['id'],
                             detail={'result': check['result'], 'message': check['reason']},
                             event_key='check:' + r['id'] + ':' + check['id'], at=check['reviewed_at'])
        for c in conversations:
            if c.get('messages'):
                self.log('conversations', '历史会话', c.get('title', '评审对话'), conversation_projects[c['conversation_id']],
                         detail={'conversation_id': c['conversation_id']}, event_key='conversation:' + c['conversation_id'], at=c['started_at'])
        for log in agent_logs:
            if log['status'] != 'error':
                continue
            self.log('conversations', log['action'], log['report_name'] or log['action'], log['project_id'], log['run_id'],
                     status=log['status'], detail={'conversation_id': log['conversation_id'], 'message': log['result']},
                     event_key='agent:' + log['id'], at=log['finished_at'] or log['started_at'])
        with self.db() as conn:
            conn.execute("INSERT OR IGNORE INTO work_journal_settings VALUES('history_imported')")

    def resolve(self, request):
        with self.db() as conn:
            row = conn.execute('''SELECT a.username,a.role,a.enabled FROM account_sessions s JOIN accounts a
                ON a.username=s.username WHERE s.token=? AND s.expires_at>? AND a.enabled=1''',
                (request.cookies.get(COOKIE, ''), timestamp())).fetchone()
        if not row:
            raise HTTPException(401, '请先登录。')
        return dict(row)

    def me(self):
        return actor.get()

    def login(self, payload: Login, request: Request):
        with self.db() as conn:
            row = conn.execute('SELECT * FROM accounts WHERE username=?', (payload.username.strip(),)).fetchone()
        if not row or not row['enabled'] or not secrets.compare_digest(self.cipher.decrypt(row['password'].encode()), payload.password.encode()):
            raise HTTPException(401, '账号或密码错误，或账号已停用。')
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        with self.db() as conn:
            conn.execute('DELETE FROM account_sessions WHERE expires_at<=? OR token=?', (timestamp(), request.cookies.get(COOKIE, '')))
            conn.execute('INSERT INTO account_sessions VALUES(?,?,?)', (token, row['username'], expires))
        self.log('system', '账号登录', row['username'], username=row['username'])
        response = JSONResponse({'username': row['username'], 'role': row['role']})
        response.set_cookie(COOKIE, token, httponly=True, samesite='lax', max_age=43200, secure=request.url.scheme == 'https')
        return response

    def logout(self, request: Request):
        self.log('system', '退出登录', actor.get()['username'])
        with self.db() as conn:
            conn.execute('DELETE FROM account_sessions WHERE token=?', (request.cookies.get(COOKIE, ''),))
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE)
        return response

    def accounts(self):
        with self.db() as conn:
            return {'accounts': [dict(r) for r in conn.execute('SELECT username,role,enabled FROM accounts ORDER BY role,username')]}

    def update_account(self, username: str, payload: AccountUpdate):
        if username == actor.get()['username'] and payload.enabled is False:
            raise HTTPException(409, '不能停用当前管理员账号。')
        with self.db() as conn:
            if not conn.execute('SELECT 1 FROM accounts WHERE username=?', (username,)).fetchone():
                raise HTTPException(404, '账号不存在。')
            if payload.enabled is not None:
                conn.execute('UPDATE accounts SET enabled=? WHERE username=?', (int(payload.enabled), username))
            if payload.password is not None:
                conn.execute('UPDATE accounts SET password=? WHERE username=?', (self.cipher.encrypt(payload.password.encode()).decode(), username))
            if payload.password is not None or payload.enabled is False:
                conn.execute('DELETE FROM account_sessions WHERE username=?', (username,))
        self.log('system', '修改账号', username, detail={'enabled': payload.enabled, 'password_reset': payload.password is not None})
        return {'ok': True}

    def owners(self):
        with self.db() as conn:
            return {r['project_id']: r['username'] for r in conn.execute('SELECT * FROM project_owners')}

    def visible(self, projects, id_key='id'):
        current, owners = actor.get(), self.owners()
        return [{**p, 'owner': owners.get(p[id_key])} for p in projects
                if not current or current['role'] == 'admin' or owners.get(p[id_key]) == current['username']]

    def check_project(self, project_id):
        current = actor.get()
        if current and current['role'] != 'admin' and self.owners().get(project_id) != current['username']:
            raise HTTPException(403, '无权访问该项目。')

    def claim(self, project_id):
        if actor.get():
            with self.db() as conn:
                conn.execute('INSERT OR IGNORE INTO project_owners VALUES(?,?)', (project_id, actor.get()['username']))

    def assign(self, project_id: str, payload: OwnerUpdate):
        with self.db() as conn:
            project = conn.execute('SELECT name FROM projects WHERE id=?', (project_id,)).fetchone()
            if not project:
                raise HTTPException(404, '项目不存在。')
            if payload.username:
                if not conn.execute('SELECT 1 FROM accounts WHERE username=? AND role=? AND enabled=1', (payload.username, 'user')).fetchone():
                    raise HTTPException(422, '请选择启用的用户账号。')
                conn.execute('INSERT INTO project_owners VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET username=excluded.username', (project_id, payload.username))
            else:
                conn.execute('DELETE FROM project_owners WHERE project_id=?', (project_id,))
        self.log('materials', '分配项目', project['name'], project_id, detail={'owner': payload.username})
        return {'ok': True}

    def records(self, category: str = 'all', query: str = '', username: str = '', after: str = '', before: str = '', project_id: str = '', report_state: str = '',
                status: str = '', page: int = 1, page_size: int = 20):
        if category not in {'all', *CATEGORIES} or status not in {'', 'success', 'error', 'running'} or report_state not in {'', 'issues', 'pass', 'archived'}:
            raise HTTPException(422, '筛选条件无效。')
        page_size = min(max(page_size, 1), 100)
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute('''SELECT w.*,p.name AS project_name FROM work_records w
                LEFT JOIN projects p ON p.id=w.project_id ORDER BY w.created_at DESC,w.id DESC''')]
            runs = {r['id']: json.loads(r['payload']) for r in conn.execute('SELECT id,payload FROM wb_runs')} if report_state else {}
        def matches_report(record):
            if not report_state:
                return True
            run = runs.get(record['run_id'])
            if not run:
                return False
            if report_state == 'archived':
                return run['status'] == 'archived'
            passed = run['result'] == 'pass' or bool(run.get('qualification'))
            return passed if report_state == 'pass' else not passed and run['status'] != 'archived'
        words = query.casefold().split()
        base = [r for r in rows if (not username or (r['username'] or 'legacy') == username)
                and (not project_id or r['project_id'] == project_id)
                and matches_report(r)
                and (not status or r['status'] == status)
                and (not after or self.day(r['created_at']) >= after)
                and (not before or self.day(r['created_at']) <= before)
                and all(w in f"{r['title']} {r['project_name'] or ''} {r['action']} {r['username'] or '历史数据'}".casefold() for w in words)]
        counts = {'all': len(base), **{key: sum(r['category'] == key for r in base) for key in CATEGORIES}}
        filtered = [r for r in base if category == 'all' or r['category'] == category]
        page = min(max(page, 1), max(1, (len(filtered) + page_size - 1) // page_size))
        result = filtered[(page - 1) * page_size:page * page_size]
        for r in result:
            r.pop('detail'); r.pop('event_key')
        return {'records': result, 'total': len(filtered), 'page': page, 'page_size': page_size, 'counts': counts}

    @staticmethod
    def day(value):
        return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).date().isoformat()

    def record(self, record_id: int):
        with self.db() as conn:
            row = conn.execute('SELECT * FROM work_records WHERE id=?', (record_id,)).fetchone()
            if not row:
                raise HTTPException(404, '记录不存在。')
            result = dict(row)
            result['detail'] = json.loads(result['detail'])
            conversation_id = result['detail'].get('conversation_id')
            if conversation_id:
                c = conn.execute('SELECT payload FROM wb_agent_conversations WHERE id=?', (conversation_id,)).fetchone()
                result['conversation'] = json.loads(c['payload']) if c else None
        return result

    def overview(self):
        with self.db() as conn:
            projects = conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
            users = conn.execute("SELECT COUNT(*) FROM accounts WHERE role='user' AND enabled=1").fetchone()[0]
            runs = [json.loads(r['payload']) for r in conn.execute('SELECT payload FROM wb_runs')]
            errors = conn.execute("SELECT COUNT(*) FROM work_records WHERE status='error'").fetchone()[0]
        return {'projects': projects, 'users': users, 'reports': len(runs), 'errors': errors,
                'pending': sum(r['result'] != 'pass' and not r.get('qualification') and r['status'] != 'archived' for r in runs),
                'recent': self.records(page_size=6)['records']}


class AccountMiddleware:
    def __init__(self, app, accounts, routes):
        self.app, self.accounts, self.routes = app, accounts, routes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] in ('/health', '/api/auth/login') or scope['method'] == 'OPTIONS':
            return await self.app(scope, receive, send)
        request = Request(scope)
        service = self.accounts
        try:
            current = service.resolve(request)
            route = None
            params = {}
            for candidate in self.routes():
                match, child = candidate.matches(scope)
                if match == Match.FULL:
                    route, params = candidate.path, child.get('path_params', {})
                    break
            path, method = scope['path'], scope['method']
            if path == '/api/workbench/demo':
                raise HTTPException(403, '请创建项目并上传材料；已有演示项目可由管理员分配后查看。')
            admin_only = path.startswith(('/api/admin', '/api/models', '/api/analytics')) or (path.startswith('/api/workbench/rules') and method != 'GET') or path in ('/docs', '/openapi.json', '/redoc')
            if admin_only and current['role'] != 'admin':
                raise HTTPException(403, '此操作仅管理员可用。')
            if current['role'] == 'admin' and method not in ('GET', 'HEAD') and not path.startswith(('/api/admin', '/api/models', '/api/workbench/rules', '/api/auth')):
                raise HTTPException(403, '请切换用户账号执行评审业务操作。')
            payload = {}
            if request.headers.get('content-type', '').startswith('application/json'):
                chunks = []
                more = True
                while more:
                    message = await receive()
                    chunks.append(message.get('body', b'')); more = message.get('more_body', False)
                body = b''.join(chunks)
                try:
                    parsed = json.loads(body)
                    payload = parsed if isinstance(parsed, dict) else {}
                except ValueError:
                    pass
                original_receive = receive
                delivered = False
                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {'type': 'http.request', 'body': body, 'more_body': False}
                    return await original_receive()
                receive = replay
            identity = {**dict(request.query_params), **params}
            project_id = identity.get('project_id')
            with service.db() as conn:
                for key, table, column in [('run_id', 'wb_runs', 'id'), ('session_id', 'review_sessions', 'id'), ('batch_id', 'wb_batches', 'id')]:
                    value = identity.get(key)
                    if value:
                        row = conn.execute(f'SELECT project_id FROM {table} WHERE {column}=?', (value,)).fetchone()
                        if row and row['project_id']:
                            if project_id and project_id != row['project_id'] and not path.startswith('/api/workbench/batches/'):
                                raise HTTPException(403, '记录不属于当前项目。')
                            project_id = row['project_id']
                # Validate references supplied in JSON as well as in the URL.
                for key, table in [('run_id', 'wb_runs'), ('material_package_id', 'material_packages'), ('conversation_id', 'wb_agent_conversations')]:
                    value = payload.get(key) or request.query_params.get(key)
                    if value:
                        row = conn.execute(f'SELECT project_id FROM {table} WHERE id=?', (value,)).fetchone()
                        if row:
                            if project_id and project_id != row['project_id']:
                                raise HTTPException(403, '记录不属于当前项目。')
                            project_id = row['project_id']
                if payload.get('project_id'):
                    if project_id and project_id != payload['project_id']:
                        raise HTTPException(403, '记录不属于当前项目。')
                    project_id = payload['project_id']
            context = actor.set(current)
            try:
                if project_id and not path.startswith('/api/admin/'):
                    service.check_project(project_id)
                before = self.snapshot(route, params, project_id)
                response_status = 500
                response_json = False
                response_body = []
                async def observe(message):
                    nonlocal response_status, response_json
                    if message['type'] == 'http.response.start':
                        response_status = message['status']
                        response_json = any(k == b'content-type' and b'application/json' in v for k, v in message['headers'])
                    if message['type'] == 'http.response.body' and response_json:
                        response_body.append(message.get('body', b''))
                    if message['type'] == 'http.response.body' and not message.get('more_body', False) and method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                        data = json.loads(b''.join(response_body)) if response_json and response_body else {}
                        self.record_action(route, method, params, payload, project_id, response_status, data, before)
                    await send(message)
                await self.app(scope, receive, observe)
            finally:
                actor.reset(context)
        except HTTPException as error:
            await JSONResponse({'detail': error.detail}, status_code=error.status_code)(scope, receive, send)

    def snapshot(self, route, params, project_id):
        if route == '/api/models' and self.accounts.model_status:
            return self.accounts.model_status()
        if route == '/api/workbench/rules/{rule_id}':
            with self.accounts.db() as conn:
                r = conn.execute('SELECT payload FROM wb_rules WHERE id=?', (params['rule_id'],)).fetchone()
            return json.loads(r['payload']) if r else None
        return None

    def record_action(self, route, method, params, payload, project_id, status_code, data, before):
        service = self.accounts
        if route == '/api/projects' and method == 'POST' and status_code < 400:
            project_id = data.get('project_id') or data.get('id')
            service.claim(project_id)
            service.log('materials', '创建项目', data.get('name', '新项目'), project_id, event_key='project:' + project_id)
            return
        if route == '/api/workbench/batches' and method == 'POST' and status_code < 400:
            project_id = data['project_id']
            service.claim(project_id)
            service.log('materials', '创建项目组', payload.get('name', '项目组'), project_id, event_key='project:' + project_id)
            return
        if not route or route.startswith('/api/auth') or route.startswith('/api/admin') or '/agent/' in route:
            return
        category, action = None, None
        if route.startswith('/api/models') and method != 'GET':
            category, action = 'system', '测试模型连接' if route.endswith('/test') else '修改模型配置'
        elif route.startswith('/api/workbench/rules') and method != 'GET':
            category, action = 'system', '更新评审规则' if 'rule_id' in params else '新增评审规则'
        elif project_id and (route == '/api/workbench/projects/{project_id}/runs' or route.endswith('/run')) and method == 'POST':
            category, action = 'reviews', '执行评审'
        elif '/runs/' in route or route.endswith('/brief'):
            return  # Workbench methods record these changes once, including changes made by chat tools.
        elif project_id and ('/documents' in route or route.endswith('/upload')):
            category, action = 'materials', '上传材料'
        elif project_id and method != 'GET':
            category, action = ('manual', '处理评审事项') if '/review-sessions/' in route else ('materials', '更新项目材料')
        if not category:
            return
        title = payload.get('name') or data.get('filename') or data.get('scope_name') or action
        with service.db() as conn:
            if project_id:
                p = conn.execute('SELECT name FROM projects WHERE id=?', (project_id,)).fetchone()
                if p and title == action:
                    title = p['name']
        detail = {'message': data.get('detail')} if status_code >= 400 else {'result': data.get('result')}
        if before is not None:
            detail.update(before=before, after=data)
        elif route == '/api/workbench/rules' and status_code < 400:
            detail['after'] = data
        if category == 'system' and 'model' in payload:
            detail['model'] = payload['model']
        if category == 'reviews' and status_code < 400:
            return  # The saved run itself records the result, including runs started by chat tools.
        service.log(category, action, title, project_id, data.get('id') if category == 'reviews' else None,
                    status='success' if status_code < 400 else 'error', detail=detail)

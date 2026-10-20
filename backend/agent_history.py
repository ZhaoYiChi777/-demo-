"""Conversation snapshots and persistent, report-linked operation logs."""
import json
from datetime import datetime
from uuid import uuid4
from fastapi import HTTPException
from workbench import now


class AgentHistory:
    def __init__(self, wb):
        self.wb=wb

    def init_tables(self):
        with self.wb.db() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS wb_agent_sessions(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wb_agent_conversations(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,scope INTEGER,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wb_agent_logs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,scope INTEGER,
                    conversation_id TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,action TEXT NOT NULL,
                    status TEXT NOT NULL,detail TEXT NOT NULL,result TEXT NOT NULL DEFAULT '',run_id TEXT,report_name TEXT,run_created_at TEXT);
            ''')
            legacy=list(conn.execute('SELECT id,payload FROM wb_agent_sessions'))
        for row in legacy:
            session=json.loads(row['payload'])
            if not session.get('conversation_id'):
                project_id,scope=row['id'].rsplit(':',1)
                self.save(project_id,None if scope=='project' else int(scope),session)

    def key(self, project_id, scope):
        return f"{project_id}:{scope or 'project'}"

    def active(self, project_id, scope):
        self.wb.project(project_id)
        with self.wb.db() as conn:
            row=conn.execute('SELECT payload FROM wb_agent_sessions WHERE id=?',(self.key(project_id,scope),)).fetchone()
        if row:return json.loads(row['payload'])
        session={'messages':[],'proposals':[]}
        self.save(project_id,scope,session)
        return session

    def save(self, project_id, scope, session):
        session.setdefault('conversation_id',uuid4().hex)
        if self.wb.access:
            from accounts import actor
            if actor.get() and 'username' not in session:
                session['username'] = actor.get()['username']
        session.setdefault('started_at',session.get('messages',[{}])[0].get('at',now()) if session.get('messages') else now())
        session['updated_at']=now()
        session['title']=next((m['content'][:40] for m in session.get('messages',[]) if m['role']=='user'),'新会话')
        payload=json.dumps(session,ensure_ascii=False)
        with self.wb.db() as conn:
            conn.execute('INSERT INTO wb_agent_sessions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(self.key(project_id,scope),payload))
            conn.execute('INSERT INTO wb_agent_conversations VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(session['conversation_id'],project_id,scope,payload))
        if self.wb.access and session.get('messages'):
            self.wb.access.log('conversations', '评审会话', session['title'], project_id,
                detail={'conversation_id': session['conversation_id']}, event_key='conversation:' + session['conversation_id'], at=session['started_at'])

    def conversations(self, project_id, scope):
        active=self.active(project_id,scope)
        with self.wb.db() as conn:
            sessions=[json.loads(row['payload']) for row in conn.execute('SELECT payload FROM wb_agent_conversations WHERE project_id=? AND scope IS ?',(project_id,scope))]
        return {'active_id':active['conversation_id'],'conversations':[{
            **{k:s[k] for k in ('conversation_id','title','started_at','updated_at')},'messages':s['messages'],
            'run_ids':list(dict.fromkeys(m['run_id'] for m in s['messages'] if m.get('run_id')))
        } for s in sorted(sessions,key=lambda s:s['updated_at'],reverse=True)]}

    def activate(self, project_id, scope, conversation_id=None):
        if conversation_id:
            with self.wb.db() as conn:
                row=conn.execute('SELECT payload FROM wb_agent_conversations WHERE id=? AND project_id=? AND scope IS ?',(conversation_id,project_id,scope)).fetchone()
            if not row:raise HTTPException(404,'未找到当前项目范围内的会话。')
            session=json.loads(row['payload'])
        else:
            session={'messages':[],'proposals':[]}
        self.save(project_id,scope,session)
        return session

    def begin(self, project_id, scope, session, action, detail, run):
        log_id=uuid4().hex
        with self.wb.db() as conn:
            conn.execute('INSERT INTO wb_agent_logs(id,project_id,scope,conversation_id,started_at,action,status,detail,run_id,report_name,run_created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (log_id,project_id,scope,session['conversation_id'],now(),action,'running',detail,
                 run['id'] if run else None,run['project']['name'] if run else None,run['created_at'] if run else None))
        return log_id

    def finish(self, log_id, status, result, run=None):
        with self.wb.db() as conn:
            conn.execute('UPDATE wb_agent_logs SET finished_at=?,status=?,result=? WHERE id=?',(now(),status,result,log_id))
            if run:
                conn.execute('UPDATE wb_agent_logs SET run_id=?,report_name=?,run_created_at=? WHERE id=?',(run['id'],run['project']['name'],run['created_at'],log_id))
            log = conn.execute('SELECT * FROM wb_agent_logs WHERE id=?', (log_id,)).fetchone()
        if self.wb.access and log and status == 'error':
            self.wb.access.log('conversations', log['action'], log['report_name'] or log['action'], log['project_id'], log['run_id'],
                               status=status, detail={'conversation_id': log['conversation_id'], 'message': result}, event_key='agent:' + log_id)

    def logs(self, project_id, scope):
        self.wb.project(project_id)
        with self.wb.db() as conn:
            rows=[dict(r) for r in conn.execute('SELECT * FROM wb_agent_logs WHERE project_id=? AND scope IS ? ORDER BY started_at DESC',(project_id,scope))]
        for row in rows:
            row['duration_seconds']=round((datetime.fromisoformat(row['finished_at'])-datetime.fromisoformat(row['started_at'])).total_seconds(),2) if row['finished_at'] else None
        return {'logs':rows}

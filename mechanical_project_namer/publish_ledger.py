# SPDX-License-Identifier: GPL-3.0-only
"""Private per-instance publishing sidecar, deliberately excluded from 3B backups."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from .publish_protocol import safe, sha, data, ProtocolError


class PublishLedger:
    def __init__(self, snapshots):
        self.root = safe(snapshots.db.path.parent/'.publish-state')
        self.root.mkdir(exist_ok=True)
        self.path = safe(self.root/'state.sqlite')
        # A manually copied sidecar is not authoritative in a new/restored location.
        with snapshots.db.connect() as c:
            restored = c.execute("SELECT value FROM snapshot_meta WHERE key='restored_instance'").fetchone()
        self.instance = sha([str(snapshots.db.path.absolute()), snapshots.store_id, restored[0] if restored else None])
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('CREATE TABLE IF NOT EXISTS publishes(target TEXT PRIMARY KEY,instance TEXT,service TEXT,project TEXT,store TEXT,version TEXT,task TEXT,fingerprint TEXT,state TEXT,confirmed TEXT,confirmed_at REAL,error TEXT,config_ref TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,target TEXT,state TEXT,code TEXT,created REAL,task TEXT)')
            if 'task' not in {r[1] for r in c.execute('PRAGMA table_info(events)')}:
                c.execute('ALTER TABLE events ADD COLUMN task TEXT')
    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()
    def key(self, config, store, version):
        return sha([config['service'], config['project'], store, version])
    def get(self, key):
        with self.db() as c:
            row = c.execute('SELECT * FROM publishes WHERE target=? AND instance=?', (key,self.instance)).fetchone()
        return dict(row) if row else None
    def begin(self, config, m, *, restart=False, config_ref=''):
        key = self.key(config,m['store'],m['version'])
        old = self.get(key)
        if old and old['fingerprint'] != sha(m):
            raise ProtocolError('local-manifest-conflict')
        if old and old['state']=='cancelled' and not restart:
            raise ProtocolError('task-cancelled-explicit-restart-required')
        if old and not (restart and old['state']=='cancelled'):
            self.update(key,'retrying',code='retry')
            return key, old['task']
        task = str(uuid.uuid4()) if old else str(uuid.uuid5(uuid.UUID(config['service']),config['project']+m['store']+m['version']))
        with self.db() as c:
            c.execute('INSERT OR REPLACE INTO publishes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (key,self.instance,config['service'],config['project'],m['store'],m['version'],task,sha(m),'waiting','',0,'',config_ref))
        self.update(key,'waiting',code='explicit-new-attempt' if old else 'created')
        return key,task
    def update(self, key, state, *, confirmation=None, code=''):
        # Only controlled error codes / whitelisted server status, never exceptions,
        # HTTP headers, passwords or token-bearing config bodies.
        with self.db() as c:
            if confirmation is None:
                c.execute('UPDATE publishes SET state=?,error=? WHERE target=? AND instance=?',(state,code,key,self.instance))
            else:
                c.execute('UPDATE publishes SET state=?,error=?,confirmed=?,confirmed_at=? WHERE target=? AND instance=?',
                          (state,code,data(confirmation).decode(),time.time(),key,self.instance))
            old=c.execute('SELECT state,code FROM events WHERE target=? ORDER BY id DESC LIMIT 1',(key,)).fetchone()
            if not old or tuple(old)!=(state,code):
                if c.execute('SELECT count(*) FROM events').fetchone()[0] >= 10000:
                    raise ProtocolError('local-audit-limit')
                task=c.execute('SELECT task FROM publishes WHERE target=? AND instance=?',(key,self.instance)).fetchone()
                if task:
                    c.execute('INSERT INTO events(target,state,code,created,task) VALUES (?,?,?,?,?)',(key,state,code,time.time(),task[0]))

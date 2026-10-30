"""Crash-safe evaluation checkpoints; JSON is a readable export, SQLite authoritative."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def _decode(raw):
    state=json.loads(raw)
    if (not isinstance(state,dict) or not isinstance(state.get('history'),list)
        or 'version' not in state or 'consumed_until' not in state):
        raise ValueError('Incomplete evaluation checkpoint')
    return state


def _export(path,raw):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w') as output:
        output.write(raw);output.flush();os.fsync(output.fileno())
    os.replace(temporary,path)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)


def load(path):
    path=Path(path);checkpoint=path.with_suffix('.checkpoint.sqlite')
    choices=[];errors=[];present=False
    if checkpoint.exists():
        present=True
        try:
            db=sqlite3.connect('file:'+str(checkpoint.resolve())+'?mode=ro',uri=True)
            try:row=db.execute('SELECT body,checksum FROM checkpoint WHERE id=1').fetchone()
            finally:db.close()
            if row:
                if hashlib.sha256(row[0].encode()).hexdigest()!=row[1]:raise ValueError('Checksum mismatch')
                state=_decode(row[0]);choices.append((state.get('_checkpoint_sequence',0),2,checkpoint.stat().st_mtime_ns,state,'sqlite'))
        except (sqlite3.Error,ValueError,KeyError,TypeError) as error:errors.append('durable checkpoint: '+type(error).__name__)
    primary=None
    for file,label,priority in ((path,'json',1),(path.with_suffix('.tmp'),'temporary',0)):
        if not file.exists():continue
        present=True
        try:
            state=_decode(file.read_text())
            choices.append((state.get('_checkpoint_sequence',0),priority,file.stat().st_mtime_ns,state,label))
            if file==path:primary=state
        except (ValueError,KeyError,TypeError) as error:errors.append(label+': '+type(error).__name__)
    if not choices:
        if present:raise ValueError('Evaluation recovery blocked; preserve checkpoint files: '+', '.join(errors))
        return None,None
    # Generation wins; legacy checkpoints without generations use write time.
    if max(row[0] for row in choices)==0:
        chosen=max(choices,key=lambda row:(row[2],row[1]))
    else:chosen=max(choices,key=lambda row:(row[0],row[1],row[2]))
    state=chosen[3]
    recovered=primary!=state or bool(errors)
    reason=('Recovered evaluation from '+chosen[4]+'; '+('; '.join(errors) if errors else 'JSON export was missing or behind the durable checkpoint')) if recovered else None
    return state,reason


def save(path,state):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    checkpoint=path.with_suffix('.checkpoint.sqlite')
    db=sqlite3.connect(checkpoint)
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS checkpoint(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL,checksum TEXT NOT NULL)')
        state['_checkpoint_sequence']=state.get('_checkpoint_sequence',0)+1
        raw=json.dumps(state,sort_keys=True,allow_nan=False)
        with db:
            db.execute('INSERT OR REPLACE INTO checkpoint VALUES(1,?,?)',
                (raw,hashlib.sha256(raw.encode()).hexdigest()))
    finally:db.close()
    _export(path,raw)

"""Local API usage ledger and read-only provider quota snapshots. Never stores prompts or keys."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
import requests

ROOT=Path(__file__).resolve().parent
DB=ROOT/'data/api_usage.sqlite3'
PROVIDERS=('gemini','elevenlabs','groq')
LINKS={'gemini':'https://aistudio.google.com/usage','elevenlabs':'https://elevenlabs.io/app/settings/subscription','groq':'https://console.groq.com/usage'}
_REFRESH_LOCK=threading.Lock()
try:TZ=ZoneInfo('Europe/Madrid')
except ZoneInfoNotFoundError:TZ=timezone.utc


@contextmanager
def connection():
    DB.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB,timeout=2)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at REAL NOT NULL,provider TEXT NOT NULL,model TEXT,ok INTEGER NOT NULL,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,characters INTEGER);
    CREATE INDEX IF NOT EXISTS events_at ON events(at);
    CREATE TABLE IF NOT EXISTS snapshots(provider TEXT NOT NULL,scope TEXT NOT NULL,model TEXT NOT NULL DEFAULT '',at REAL NOT NULL,data TEXT NOT NULL,PRIMARY KEY(provider,scope,model));
    CREATE TABLE IF NOT EXISTS budgets(provider TEXT PRIMARY KEY,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    """)
    con.execute("INSERT OR IGNORE INTO metadata VALUES('started_at',?)",(str(time.time()),));con.commit()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def number(value):
    try:
        value=int(value)
        return value if 0<=value<=10**15 else None
    except (TypeError,ValueError,OverflowError):return None


def fingerprint(key):return hashlib.sha256((key or '').encode()).hexdigest()[:24]


def keys():
    from dotenv import dotenv_values
    values=dotenv_values(ROOT/'.env')
    return {p:str(values.get(name) or os.environ.get(name,'') or '').strip().strip('"').strip("'")
            for p,name in [('gemini','GEMINI_API_KEY'),('elevenlabs','ELEVENLABS_API_KEY'),('groq','GROQ_API_KEY')]}


def record(provider,model='',ok=True,input_tokens=None,output_tokens=None,total_tokens=None,characters=None):
    # Telemetry must never make a generation fail or expose its input text.
    try:
        if provider not in PROVIDERS:return
        with connection() as con:
            con.execute('INSERT INTO events(at,provider,model,ok,input_tokens,output_tokens,total_tokens,characters) VALUES(?,?,?,?,?,?,?,?)',
                        (time.time(),provider,str(model or '')[:160],int(bool(ok)),number(input_tokens),number(output_tokens),number(total_tokens),number(characters)))
    except Exception:logging.getLogger(__name__).warning('No se pudo registrar una métrica local de API')


def save_snapshot(provider,scope,model,data,at=None):
    with connection() as con:
        con.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?)',(provider,scope,model,time.time() if at is None else at,json.dumps(data)))


def snapshots(provider,scope):
    with connection() as con:
        rows=con.execute('SELECT model,at,data FROM snapshots WHERE provider=? AND scope=? ORDER BY at DESC',(provider,scope)).fetchall()
    return [{**json.loads(r['data']),'model':r['model'],'at':r['at']} for r in rows]


def seconds(value):
    if not isinstance(value,str):return None
    pieces=re.findall(r'(\d+(?:\.\d+)?)(ms|s|m|h|d)',value)
    if not pieces or ''.join(n+u for n,u in pieces)!=value:return None
    return sum(float(n)*{'ms':.001,'s':1,'m':60,'h':3600,'d':86400}[u] for n,u in pieces)


def usage_post(url,*args,**kwargs):
    host=urlparse(str(url)).hostname
    provider='groq' if host=='api.groq.com' else 'elevenlabs' if host=='api.elevenlabs.io' else None
    if provider is None:return requests.post(url,*args,**kwargs)
    payload=kwargs.get('json') or {};model=str(payload.get('model') or payload.get('model_id') or '')
    try:
        response=requests.post(url,*args,**kwargs)
    except Exception:
        record(provider,model,ok=False);raise
    try:
        ok=200<=response.status_code<300;usage={}
        if provider=='groq':
            try:usage=response.json().get('usage') or {}
            except (ValueError,AttributeError):pass
            header={str(k).lower():v for k,v in response.headers.items()}
            credential=(kwargs.get('headers') or {}).get('Authorization','')
            credential=credential.removeprefix('Bearer ').strip()
            limits={k:number(header.get('x-ratelimit-'+k)) for k in ['limit-requests','remaining-requests','limit-tokens','remaining-tokens']}
            now=time.time()
            for unit in ['requests','tokens']:
                duration=seconds(header.get('x-ratelimit-reset-'+unit))
                limits['reset_'+unit+'_at']=now+duration if duration is not None else None
            if any(v is not None for v in limits.values()):save_snapshot('groq',fingerprint(credential),model,{'status':'ok','limits':limits})
        record(provider,model,ok,usage.get('prompt_tokens'),usage.get('completion_tokens'),usage.get('total_tokens'),len(payload.get('text','')) if provider=='elevenlabs' and ok else None)
    except Exception:logging.getLogger(__name__).warning('No se pudo leer una métrica de la respuesta API')
    return response


def usage_generate(call,*args,**kwargs):
    try:response=call(*args,**kwargs)
    except Exception:
        record('gemini',kwargs.get('model',''),ok=False);raise
    usage=getattr(response,'usage_metadata',None)
    def field(name):return usage.get(name) if isinstance(usage,dict) else getattr(usage,name,None)
    record('gemini',kwargs.get('model',''),True,field('prompt_token_count'),field('candidates_token_count'),field('total_token_count'))
    return response


def usage_tts(call,*args,**kwargs):
    ok=False
    try:
        yield from call(*args,**kwargs)
        ok=True
    finally:record('elevenlabs',kwargs.get('model_id',''),ok,characters=len(kwargs.get('text','')) if ok else None)


def refresh_eleven(key,force=False):
    scope=fingerprint(key);previous=snapshots('elevenlabs',scope)
    if not force and previous and time.time()-previous[0]['at']<120:return previous[0]
    result={'status':'unavailable'}
    try:
        r=requests.get('https://api.elevenlabs.io/v1/user/subscription',headers={'xi-api-key':key},timeout=10)
        if r.status_code==200:
            d=r.json();used=number(d.get('character_count'));limit=number(d.get('character_limit'))
            result={'status':'ok','used':used,'limit':limit,'remaining':max(0,limit-used) if used is not None and limit is not None else None,
                    'tier':str(d.get('tier',''))[:60],'reset_at':number(d.get('next_character_count_reset_unix')),
                    'period':str(d.get('character_refresh_period') or d.get('billing_period') or ''),
                    'overage_possible':d.get('max_credit_limit_extension') not in (None,0,'0') or bool(d.get('allowed_to_extend_character_limit'))}
        elif r.status_code in (401,403):
            try:
                detail=r.json().get('detail') or {};category=detail.get('status') if isinstance(detail,dict) else None
            except ValueError:category=None
            result={'status':'permission_required' if category=='missing_permissions' else 'unauthorized'}
        elif r.status_code==429:result={'status':'rate_limited'}
    except requests.RequestException:result={'status':'network_error'}
    except (ValueError,TypeError):result={'status':'unavailable'}
    save_snapshot('elevenlabs',scope,'',result)
    return result


def refresh_accounts(force=False):
    with _REFRESH_LOCK:
        current=keys()
        if current['elevenlabs']:refresh_eleven(current['elevenlabs'],force)
    return {'success':True}


def save_budget(provider,limit,unit,period):
    if provider not in PROVIDERS or unit not in ['requests','tokens','characters'] or period not in ['daily','monthly']:raise ValueError('Presupuesto no válido')
    if (provider=='elevenlabs' and unit=='tokens') or (provider!='elevenlabs' and unit=='characters'):raise ValueError('Unidad no válida para este proveedor')
    if limit is not None and (not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=10**12):raise ValueError('El límite debe ser un entero positivo')
    with connection() as con:con.execute('INSERT OR REPLACE INTO budgets VALUES(?,?)',(provider,json.dumps({'limit':limit,'unit':unit,'period':period})))


def dashboard(days=30):
    now=datetime.now(TZ);first=(now-timedelta(days=days-1)).date();start=datetime.combine(first,datetime.min.time(),TZ).timestamp()
    current=keys()
    with connection() as con:
        rows=[dict(r) for r in con.execute('SELECT * FROM events WHERE at>=? ORDER BY at',(min(start,now.replace(day=1,hour=0,minute=0,second=0,microsecond=0).timestamp()),))]
        started=float(con.execute("SELECT value FROM metadata WHERE key='started_at'").fetchone()[0])
        budgets={r['provider']:json.loads(r['data']) for r in con.execute('SELECT * FROM budgets')}
    providers=[]
    for provider in PROVIDERS:
        events=[r for r in rows if r['provider']==provider];visible=[r for r in events if r['at']>=start]
        series=[]
        for n in range(days):
            day=first+timedelta(days=n);items=[r for r in visible if datetime.fromtimestamp(r['at'],TZ).date()==day]
            series.append({'date':str(day),'requests':len(items),'errors':sum(not r['ok'] for r in items),
                           'tokens':sum(r['total_tokens'] or 0 for r in items),'characters':sum(r['characters'] or 0 for r in items)})
        counts={'requests':len(visible),'errors':sum(not r['ok'] for r in visible),'tokens':sum(r['total_tokens'] or 0 for r in visible),
                'characters':sum(r['characters'] or 0 for r in visible),'unknown_token_calls':sum(r['ok'] and r['total_tokens'] is None for r in visible) if provider!='elevenlabs' else 0}
        budget=budgets.get(provider,{'limit':None,'unit':'characters' if provider=='elevenlabs' else 'requests','period':'monthly'})
        boundary=now.replace(hour=0,minute=0,second=0,microsecond=0)
        if budget['period']=='monthly':boundary=boundary.replace(day=1);reset=(boundary.replace(day=28)+timedelta(days=4)).replace(day=1)
        else:reset=boundary+timedelta(days=1)
        selected=[r for r in events if r['at']>=boundary.timestamp()]
        used=len(selected) if budget['unit']=='requests' else sum(r['characters' if budget['unit']=='characters' else 'total_tokens'] or 0 for r in selected)
        budget={**budget,'used':used,'remaining':max(0,budget['limit']-used) if budget['limit'] is not None else None,'reset_at':reset.timestamp(),
                'unknown_calls':sum(r['ok'] and r['total_tokens'] is None for r in selected) if budget['unit']=='tokens' else 0}
        quota={'status':'not_connected' if not current[provider] else 'not_available','source':'provider'}
        if current[provider]:
            records=snapshots(provider,fingerprint(current[provider]))
            if provider=='elevenlabs':
                quota=records[0] if records else {'status':'not_refreshed'}
                quota={**quota,'source':'provider'}
                if quota.get('reset_at') and quota['reset_at']<time.time():quota={**quota,'status':'expired','remaining':None}
            elif provider=='groq':
                quota={'status':'ok' if records else 'awaiting_request','source':'response_headers','models':[]}
                for snap in records[:6]:
                    limits=dict(snap['limits'])
                    for unit in ['requests','tokens']:
                        reset_at=limits.get('reset_'+unit+'_at')
                        if reset_at is None or reset_at<=time.time():limits['remaining-'+unit]=None
                    quota['models'].append({'model':snap['model'],'at':snap['at'],'limits':limits})
        providers.append({'id':provider,'configured':bool(current[provider]),'dashboard_url':LINKS[provider],'quota':quota,'local':counts,'series':series,'budget':budget})
    return {'days':days,'timezone':str(TZ),'started_at':started,'updated_at':time.time(),'providers':providers,
            'scope':'Historial local de esta app desde su activación; incluye las claves usadas. No recoge consumos de otras apps ni reconstruye el pasado.'}

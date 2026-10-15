import os, sys, json, random, string, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir('D:/AI_health')
for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy"): os.environ.pop(k,None)
os.environ["NO_PROXY"]="*"; os.environ["no_proxy"]="*"
import requests
B='http://127.0.0.1:8019'
u='kbr5_'+''.join(random.choice(string.ascii_lowercase) for _ in range(6))
requests.post(B+'/api/v1/auth/register', json={'username':u,'password':'Test@2026'}, timeout=20)
lg=requests.post(B+'/api/v1/auth/login', json={'username':u,'password':'Test@2026'}, timeout=20).json()
tk=lg['data']['access_token']; uid=lg['data']['user']['user_id']; H={'Authorization':'Bearer '+tk}

def ask(q):
    t0=time.time()
    text=''; refs=[]; state=None
    with requests.post(B+'/api/v1/chat/query', json={'question':q}, headers=H, stream=True, timeout=180) as r:
        cur='?'
        for line in r.iter_lines(decode_unicode=True, chunk_size=1):
            if not line: continue
            if line.startswith('event:'): cur=line.split(':',1)[1].strip()
            elif line.startswith('data:'):
                try:
                    d=json.loads(line.split(':',1)[1].strip())
                except Exception: continue
                if cur=='delta' and isinstance(d.get('text'),str): text+=d['text']
                if cur=='reference': refs=d.get('docs') or []
                if cur=='done': state=d.get('state') or 'done'
    return {'q':q,'ms':int((time.time()-t0)*1000),'text':text,'refs':refs,'state':state}

print('=== 4 类问题（RAG 端到端） ===')
total=0; hits=0
for q in ('山药有什么功效？','哪些食物含铁丰富？','糖尿病患者能吃什么水果？','超出知识库范围的冷门问题 xyz123'):
    r=ask(q)
    total+=1
    has_ans = bool(r['text'] and len(r['text'])>20)
    has_ref = len(r['refs'])>0
    if has_ans and has_ref: hits+=1
    print(f"\nQ: {r['q']}")
    print(f"   耗时 {r['ms']}ms | state={r['state']} | 文本 {len(r['text'])}字 | 引用 {len(r['refs'])}条")
    if r['refs']:
        t=r['refs'][0]
        print(f"   top 引用: {t.get('title')} (sim={t.get('similarity')})")
    print(f"   答: {r['text'][:140]!r}")
print(f"\n=== 汇总: {hits}/{total} 同时拿到'回答+引用' ===")

from app.utils.db import get_session
from sqlalchemy import text as T
sess=get_session()
sess.execute(T("DELETE FROM chat_session WHERE user_id=:u"), {'u':uid})
sess.execute(T("DELETE FROM user_profile WHERE user_id=:u"), {'u':uid})
sess.execute(T("DELETE FROM sys_user WHERE user_id=:u"), {'u':uid})
sess.commit(); sess.close()
print('清理完成', u)
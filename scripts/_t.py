import httpx, json
resp = httpx.post('http://localhost:8001/api/v1/auth/login', json={'username':'purchase','password':'123456'}, timeout=5)
token = resp.json()['token']
resp = httpx.post('http://localhost:8001/api/v1/chat/stream', 
    json={'query':'什么是安全库存','stream':True},
    headers={'Authorization':f'Bearer {token}'}, timeout=90)
for line in resp.text.split('\n'):
    if line.startswith('data:'):
        d = line[5:].strip()
        if d == '[DONE]': break
        try:
            j = json.loads(d)
            t = j.get('type','?')
            if t in ('error','content'):
                print(f'{t}: {json.dumps(j,ensure_ascii=False)[:200]}')
        except: pass

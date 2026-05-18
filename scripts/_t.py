import httpx, json
resp = httpx.post('http://localhost:8001/api/v1/auth/login', json={'username':'admin','password':'admin123'}, timeout=5)
token = resp.json()['token']
resp = httpx.post('http://localhost:8001/api/v1/chat/completions', 
    json={'query':'什么是安全库存','stream':False},
    headers={'Authorization':f'Bearer {token}'}, timeout=60)
print('Status:', resp.status_code)
print('Body:', resp.text[:500])

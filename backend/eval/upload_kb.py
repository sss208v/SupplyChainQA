"""上传测试知识库文档到 SmartQA"""
import urllib.request
import json
import os

file_path = r"c:/Users/sss208/Desktop/agent/smartqa/backend/eval/knowledge_base/企业IT支持知识库.md"
with open(file_path, "rb") as f:
    file_content = f.read()

boundary = "----SmartQAUploadBoundary"
filename = "企业IT支持知识库.md"

body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
    f"Content-Type: text/markdown\r\n\r\n"
).encode("utf-8")
body += file_content
body += f"\r\n--{boundary}--\r\n".encode()

req = urllib.request.Request(
    "http://localhost:8000/api/v1/knowledge/upload",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="POST",
)

try:
    r = urllib.request.urlopen(req, timeout=300)
    result = json.loads(r.read())
    print("Upload result:", json.dumps(result, ensure_ascii=False, indent=2))
except urllib.error.HTTPError as e:
    print(f"HTTP Error {e.code}:", e.read().decode())
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")

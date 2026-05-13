"""Download CLIP model using huggingface_hub Python API"""
import os, sys
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from huggingface_hub import snapshot_download

model = "openai/clip-vit-base-patch32"
cache = os.path.expanduser(r"~\.cache\huggingface\hub\models--openai--clip-vit-base-patch32")

print(f"Downloading {model}...")
print(f"Target: {cache}")

try:
    path = snapshot_download(
        model,
        cache_dir=os.path.expanduser(r"~\.cache\huggingface\hub"),
        resume_download=True,
    )
    print(f"Done: {path}")
    
    # Verify
    total = 0
    for dp, dn, fn in os.walk(path):
        for f in fn:
            total += os.path.getsize(os.path.join(dp, f))
    print(f"Total: {total/1024/1024:.1f}MB")
except Exception as e:
    print(f"Error: {e}")

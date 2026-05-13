import os, sys

# Check CLIP model cache
cache = os.path.expanduser(r"~\.cache\huggingface\hub\models--openai--clip-vit-base-patch32")
print(f"Cache path: {cache}")
print(f"Exists: {os.path.exists(cache)}")

if os.path.exists(cache):
    total = 0
    files = 0
    model_files = []
    for dp, dn, fn in os.walk(cache):
        for f in fn:
            fp = os.path.join(dp, f)
            sz = os.path.getsize(fp)
            total += sz
            files += 1
            if "pytorch_model" in f or "model.safetensors" in f or f.endswith(".bin"):
                model_files.append((f, sz))
    print(f"Files: {files}, Size: {total/1024/1024:.1f}MB")
    for name, sz in model_files:
        print(f"  Model file: {name} ({sz/1024/1024:.1f}MB)")
    
    if not model_files:
        print("  WARNING: No model weights found! Incomplete download.")
        # List what's there
        for dp, dn, fn in os.walk(cache):
            for f in fn[:10]:
                print(f"    {f}")
else:
    print("CLIP model NOT cached - needs download")

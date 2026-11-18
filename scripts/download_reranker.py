import urllib.request, pathlib, time

BASE = "https://www.modelscope.cn/api/v1/models/BAAI/bge-reranker-base/repo?Revision=master&FilePath="
OUT = pathlib.Path("models/bge-reranker-base")
OUT.mkdir(parents=True, exist_ok=True)

FILES = ["config.json", "tokenizer_config.json", "tokenizer.json",
         "special_tokens_map.json", "sentencepiece.bpe.model", "model.safetensors"]

for name in FILES:
    dst = OUT / name
    if dst.exists() and dst.stat().st_size > 0:
        print(f"跳过 {name}（已存在 {dst.stat().st_size/1024/1024:.1f} MB）", flush=True)
        continue
    t0 = time.time()
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total and got % (1024 * 1024 * 50) < 1024 * 256:
                print(f"  {name}: {got/1024/1024:.0f}/{total/1024/1024:.0f} MB", flush=True)
    print(f"✅ {name}  {dst.stat().st_size/1024/1024:.1f} MB  用时 {time.time()-t0:.0f}s", flush=True)

print("全部完成")

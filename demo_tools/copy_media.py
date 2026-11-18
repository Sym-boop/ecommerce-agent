"""把项目里已有的海报/视频素材复制到运行目录，使 /media 能正常访问（临时工具）。"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "backend" / "data" / "media"
DST = ROOT / "runtime" / "data" / "media"

mapping = {
    "images": DST / "images",
    "uploads": DST / "uploads",
    "videos": DST / "videos",
}

copied = 0
for sub, target in mapping.items():
    src_dir = SRC / sub
    if not src_dir.exists():
        continue
    target.mkdir(parents=True, exist_ok=True)
    for f in src_dir.iterdir():
        if f.is_file() and f.name != "_probe2.png":
            shutil.copy2(f, target / f.name)
            copied += 1
            print(f"  {sub}/{f.name}  ({f.stat().st_size // 1024} KB)")

print(f"\n共复制 {copied} 个媒体文件到 runtime/data/media/")

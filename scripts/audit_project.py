"""项目现状审计：找出仍然存在的问题（不修，只诊断）。

运行：python scripts/audit_project.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

FINDINGS: list[tuple[str, str, str]] = []   # (级别, 问题, 证据)


def add(level: str, problem: str, evidence: str = "") -> None:
    FINDINGS.append((level, problem, evidence))


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()[:10]


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60).stdout
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- 1. 仓库卫生
print("=" * 72)
print("[1] 仓库卫生")
print("=" * 72)

status = git("status", "--porcelain")
modified = [l for l in status.splitlines() if l[:2].strip() == "M"]
deleted = [l for l in status.splitlines() if l[:2].strip() == "D"]
untracked = [l for l in status.splitlines() if l.startswith("??")]
print(f"  已修改 {len(modified)}  已删除 {len(deleted)}  未跟踪 {len(untracked)}")
if len(untracked) > 50:
    add("高", f"{len(untracked)} 个文件未提交到 git，"
             f"换机器/推仓库会全部丢失",
        "所有硬化改造（测试/风控/审计/队列）都在工作区")

dist = git("ls-files", "frontend/dist").splitlines()
if dist:
    add("中", f"前端构建产物 frontend/dist 被 git 跟踪（{len(dist)} 个文件）",
        "应加入 .gitignore，否则每次构建都产生冲突")

env_tracked = git("ls-files", ".env").strip()
print(f"  .env 被跟踪: {bool(env_tracked)}")

# ---------------------------------------------------------------- 2. data 目录
print("\n" + "=" * 72)
print("[2] data 目录：有几份？是否一致？")
print("=" * 72)

dirs = [Path("data"), Path("backend/data"), Path("runtime/data")]
existing = [d for d in dirs if d.exists()]
print(f"  存在 {len(existing)} 个 data 目录: {[str(d) for d in existing]}")
if len(existing) > 1:
    for d in existing:
        n = len(list(d.rglob("*")))
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        print(f"    {str(d):<16} {n:>4} 项  {size/1024/1024:.1f} MB")

    # 同名文件内容是否一致
    names: dict[str, list[Path]] = {}
    for d in existing:
        for f in d.rglob("*.json"):
            rel = f.relative_to(d).as_posix()
            names.setdefault(rel, []).append(f)
    divergent = []
    for rel, paths in names.items():
        if len(paths) < 2:
            continue
        hashes = {md5(p) for p in paths}
        if len(hashes) > 1:
            divergent.append((rel, [str(p) for p in paths]))
    print(f"  同名但内容不一致的 JSON: {len(divergent)} 个")
    for rel, paths in divergent[:6]:
        print(f"    {rel}  ← {paths}")
    if divergent:
        add("高", f"{len(divergent)} 个 JSON 在多个 data 目录里内容不一致",
            "同一份数据有 3 个副本且已分叉，'以哪份为准'没有答案")
    add("中", f"存在 {len(existing)} 份 data 目录（data / backend/data / runtime/data）",
        "数据和媒体文件被复制多份，排查和演示时容易看错")

# 媒体文件重复占空间
media = [f for d in existing for f in d.rglob("*.mp4")]
if media:
    total = sum(f.stat().st_size for f in media) / 1024 / 1024
    uniq = len({md5(f) for f in media})
    print(f"  视频文件 {len(media)} 个 / 去重后 {uniq} 个，共 {total:.1f} MB")

tracked_data = git("ls-files", "data", "backend/data").splitlines()
print(f"  data 下被 git 跟踪的文件: {len(tracked_data)}")

# ---------------------------------------------------------------- 3. 文档漂移
print("\n" + "=" * 72)
print("[3] 文档与代码是否一致")
print("=" * 72)

req = Path("docs/requirements.md")
if req.exists():
    t = req.read_text(encoding="utf-8", errors="replace")
    stale_terms = []
    for term in ["竞价", "定价", "任务拓扑", "9 个", "九个", "supervisor"]:
        if term in t:
            stale_terms.append(term)
    if stale_terms:
        add("中", "需求文档仍在描述已摘除的旧范围",
            f"docs/requirements.md 出现: {', '.join(stale_terms)}")

# 代码里是否还有 9 平台的痕迹
dr = Path("backend/app/api/delivery_router.py")
if dr.exists():
    n = len(re.findall(r'"value":\s*"', dr.read_text(encoding="utf-8")))
    print(f"  delivery_router 平台数: {n}")

# 遗留模块
legacy = {"backend/app/agent/pricing.py": "定价 Agent（0 引用）",
          "backend/app/agent/supervisor.py": "Supervisor（API-only）",
          "frontend/src/views/AgentCanvas.vue": "任务看板（不在菜单）"}
for f, desc in legacy.items():
    if Path(f).exists():
        lines = len(Path(f).read_text(encoding="utf-8", errors="replace").splitlines())
        print(f"  遗留: {f} ({lines} 行) — {desc}")

# ---------------------------------------------------------------- 4. 安全
print("\n" + "=" * 72)
print("[4] 安全")
print("=" * 72)

# 新加的 metrics 接口有没有鉴权
mr = Path("backend/app/api/metrics_router.py")
if mr.exists():
    t = mr.read_text(encoding="utf-8")
    has_auth = bool(re.search(r"require_perm|require_role|current_user_required", t))
    print(f"  metrics 接口有鉴权: {has_auth}")
    if not has_auth:
        add("高", "POST /api/v1/metrics/report 无任何鉴权",
            "任何人都能往效果库里灌假数据，CTR/CVR 会被污染")

# 精确统计：多少端点真正挂了权限依赖
routers = list(Path("backend/app/api").glob("*_router.py"))
total_ep = total_guarded = 0
guarded_files = []
for r in routers:
    t = r.read_text(encoding="utf-8")
    eps = len(re.findall(r"@router\.(get|post|put|delete|patch)\(", t))
    guarded = len(re.findall(r"require_perm|require_role", t))
    total_ep += eps
    total_guarded += guarded
    if guarded:
        guarded_files.append(f"{r.name}({guarded})")
print(f"  端点总数 {total_ep}，挂权限依赖的 {total_guarded}"
      f"（{', '.join(guarded_files) or '无'}）")
if total_guarded < total_ep * 0.5:
    add("高", f"仅 {total_guarded}/{total_ep} 个端点挂了权限校验",
        "deps.py 有完整 RBAC 设施（resolve_actor / require_perm / 职责分离），"
        "但生成、审批、投放、效果回传等核心接口都没挂")

# 中间件是否拦截未登录
mp = Path("backend/app/main.py").read_text(encoding="utf-8")
m = re.search(r"async def actor_context_middleware.*?return await call_next\(request\)", mp, re.S)
if m and "raise" not in m.group(0) and "HTTPException" not in m.group(0):
    print("  actor 中间件只解析身份、不拒绝未登录请求: True")

# 硬编码密钥
hard = []
for p in Path("backend/app").rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r'(sk-[A-Za-z0-9]{16,}|api_key\s*=\s*["\'][A-Za-z0-9]{16,})', t):
        hard.append(f"{p}: {m.group(0)[:24]}")
if hard:
    add("高", f"代码里疑似硬编码密钥 {len(hard)} 处", "; ".join(hard[:3]))
else:
    print("  代码中未发现硬编码密钥")

# SQL 拼接
jq = Path("backend/app/common/job_queue.py")
if jq.exists():
    t = jq.read_text(encoding="utf-8")
    fstring_sql = len(re.findall(r'f"""?(?:SELECT|INSERT|UPDATE|DELETE|CREATE)', t))
    print(f"  job_queue 中 f-string 拼 SQL 处: {fstring_sql}（表名内置，非用户输入）")

# ---------------------------------------------------------------- 5. 代码质量
print("\n" + "=" * 72)
print("[5] 代码质量")
print("=" * 72)

todo = Counter()
for p in list(Path("backend").rglob("*.py")) + list(Path("frontend/src").rglob("*.vue")):
    if "node_modules" in str(p):
        continue
    t = p.read_text(encoding="utf-8", errors="replace")
    for kind in ("TODO", "FIXME", "XXX", "HACK"):
        todo[kind] += len(re.findall(rf"\b{kind}\b", t))
print(f"  TODO/FIXME 等标记: {dict(todo)}")

# debug print
prints = 0
for p in Path("backend/app").rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="replace")
    prints += len(re.findall(r"^\s*print\(", t, re.M))
print(f"  backend/app 中 print(): {prints} 处（应改用 logger）")
if prints > 5:
    add("低", f"backend/app 仍有 {prints} 处 print() 用于输出",
        "部分降级日志走 stderr 打印而非 logger")

# 依赖一致性
has_req = Path("requirements.txt").exists()
has_pyproject = Path("pyproject.toml").exists()
print(f"  requirements.txt: {has_req}   pyproject.toml: {has_pyproject}")
if has_req and has_pyproject:
    add("低", "同时存在 requirements.txt 与 pyproject.toml",
        "依赖声明双份，容易不同步（langgraph/pymysql 只在其中一边）")

# pymysql 是否声明
for f in ("requirements.txt", "pyproject.toml"):
    p = Path(f)
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="replace")
        print(f"  {f} 声明 pymysql: {'pymysql' in t}  langgraph: {'langgraph' in t}")

# ---------------------------------------------------------------- 6. 功能缺口
print("\n" + "=" * 72)
print("[6] 功能缺口")
print("=" * 72)

graph = Path("backend/app/agent/ad_graph.py")
if graph.exists():
    t = graph.read_text(encoding="utf-8")
    has_retry = "RetryPolicy" in t
    print(f"  编排节点级重试: {has_retry}")
    if not has_retry:
        add("低", "编排没有节点级重试策略", "节点抛异常直接降级，无退避重试")

# 评测样本
samples = Path("data/samples")
if samples.exists():
    files = [f.name for f in samples.iterdir() if f.is_file() and f.name != ".keep"]
    print(f"  data/samples 内容: {files}")
    if len(files) <= 1:
        add("中", "会议纪要承诺交付的评测样本不在仓库里",
            "09-23 纪要写 1000 条检索评测 + 50/10 违禁词样本，实际只有 test_input.json")

# 前端页面在菜单里吗
app_vue = Path("frontend/src/App.vue")
views = {p.stem for p in Path("frontend/src/views").glob("*.vue")}
if app_vue.exists():
    t = app_vue.read_text(encoding="utf-8", errors="replace")
    in_menu = {v for v in views if v in t}
    orphan = views - in_menu
    print(f"  views 共 {len(views)} 个，未出现在 App.vue 的: {sorted(orphan)}")

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 72)
print("结论")
print("=" * 72)
order = {"高": 0, "中": 1, "低": 2}
FINDINGS.sort(key=lambda x: order.get(x[0], 9))
for i, (level, problem, evidence) in enumerate(FINDINGS, 1):
    tag = {"高": "🔴", "中": "🟡", "低": "⚪"}.get(level, "•")
    print(f"{tag} {i}. [{level}] {problem}")
    if evidence:
        print(f"        证据: {evidence}")

print(f"\n共 {len(FINDINGS)} 项："
      f"高 {sum(1 for f in FINDINGS if f[0]=='高')} / "
      f"中 {sum(1 for f in FINDINGS if f[0]=='中')} / "
      f"低 {sum(1 for f in FINDINGS if f[0]=='低')}")

out = Path("data/reports/audit.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(
    [{"level": l, "problem": p, "evidence": e} for l, p, e in FINDINGS],
    ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已保存: {out}")

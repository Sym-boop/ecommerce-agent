# RAG 增强 · 实测报告

> 本文记录为「文档解析 / 切块 / 图片向量 / LLM 拒识 / Token 统计」这五项补的实现与实测数据。
>
> **背景**：这五项此前**不在项目里**，不是漏做，是原来的知识库只服务「爆款文案风格检索」——
> 语料是 87 条已经整理好的短 CSV，整条就能当检索单元，不需要解析和切块。
> 现在把它们补上，让项目同时具备**非结构化文档入库**和**两级拒识**能力。
>
> 生成时间：2026-11-04 ｜ 验证：`python scripts/verify_rag_pipeline.py`（40 项断言）

---

## 一、总览

| # | 能力 | 状态 | 文件 |
|---|---|---|---|
| ① | 文档解析（PDF/Word/TXT/MD/CSV） | ✅ 实测 5 种格式 | `backend/app/common/doc_ingest.py` |
| ①b | **MinerU 4.x 接入**（独立 venv） | ✅ **真跑通**，表格还原成 Markdown | 同上 |
| ② | 按 token 切块 + overlap | ✅ 512/50 可配，max 486 token | 同上 |
| ③ | 图片向量化 + 视觉检索 + 近重复 | ✅ 1024 维，同色/异色可区分 | `backend/app/common/image_vector.py` |
| ④ | LLM 相关性复核（第二级拒识） | ✅ 5 种场景覆盖 | `backend/app/agent/copywriter.py` |
| ⑤ | Token 账本（按日/月/模型聚合） | ✅ 追加写 + 3 个 API | `backend/app/common/token_usage.py` |
| — | 单元测试 | ✅ **158 个**（新增 29 个） | `backend/tests/test_core.py` |
| — | 端到端验证 | ✅ **49 项断言全过** | `scripts/verify_rag_pipeline.py` |

---

## 二、文档解析

### 解析器优先级（可插拔）

| 优先级 | 解析器 | 适用 | 依赖 |
|---|---|---|---|
| 1 | **MinerU** | 复杂版式 PDF（多栏/表格/公式/扫描件 OCR） | `pip install mineru`（重） |
| 2 | **pypdf** | 常规文本层 PDF | 已装 6.20.1 |
| 3 | **python-docx** | Word，段落 + 表格 | 已装 1.2.0 |
| 4 | 内置 | txt / md / csv（csv 逐行拼成 `列名:值`） | 无 |

**MinerU 未安装时自动回退**，返回值 `parser` 字段写明实际用了哪个——
避免"以为用了 MinerU"，也避免面试时说不清。

### 实测

```
txt   解析  parser=text         chars=216
md    解析  parser=text         chars=2025
csv   解析  parser=text         chars=160      （逐行压成 platform:… category:…）
docx  解析  parser=python-docx  chars=113      （段落 + 表格都抽到）
pdf   解析  parser=pypdf        chars=73  pages=1
```
**5 种格式全部通过**。

### 修掉的两个坑

| 坑 | 现象 | 修法 |
|---|---|---|
| **BOM 残留** | Windows 下 `Out-File` 生成的 UTF-8 带 `\ufeff`，首行标题识别全失败 | 解码顺序改成 `utf-8-sig` 优先，并 `lstrip("\ufeff")` |
| **docx 表格丢失** | 只读 `paragraphs` 会漏掉表格里的规范条目 | 额外遍历 `tables`，把每行拼成 `单元格 \| 单元格` |

### MinerU 4.x 真的接上了（独立 venv）

**为什么必须放独立环境**

`pip install mineru` 会带 47 个包，其中包括 `gradio 6.31`、`fastapi_offline`、`openai 2.54`、`onnxruntime-directml` —— **和项目的 FastAPI / pydantic 版本有冲突风险**。所以装在独立 venv：

```bash
python -m venv --system-site-packages .venv-mineru   # 复用全局 torch，省几百 MB
.venv-mineru/Scripts/pip install mineru              # 装完 408 MB
```

**实测：MinerU 装完，项目全局环境零变化**
```
openai 3.2.0 | pydantic 2.13.2 | fastapi 0.141.1   ← 与安装前一致
```

**接法（4.x 是「本地服务 + CLI」，不是 1.x 的 Python API）**

```
mineru parse <file> --tier flash --format markdown -o <out.md>
   └─ 若报 server_not_running → mineru server start 后重试一次
```

适配器 `_parse_mineru()` 做的三件事：
1. `MINERU_BIN` 指向独立 venv 的 `mineru.exe`，`MINERU_HOME` 指向可写目录
2. 子进程的 `USERPROFILE` / `HOME` 强制指向可写目录
3. 服务未起时自动 `server start` 再重试；任何失败都**回退 pypdf**，不阻断主链路

### 关键对照：表格还原能力

同一份带 5 行表格的合规 PDF：

| | 输出 | 表格结构 |
|---|---|---|
| **pypdf** | 392 字符 | ❌ 拆成裸文本：`平台\n建议时长\n预算下限\n…\n巨量千川\n10-30s\n…` |
| **MinerU** | 492 字符 | ✅ 还原成 Markdown 表格 |

MinerU 输出（实测）：
```markdown
# 电商广告投放合规规范

第一章 违禁表述

第一条 广告中不得使用绝对化用语，例如「最好」「第一」「100%」「顶级」。…

| 平台 | 建议时长 | 预算下限 | 预算上限 |
| --- | --- | --- | --- |
| 巨量千川 | 10-30s | 1000 | 100000 |
| 小红书聚光 | 图文为主 | 1000 | 50000 |
| 阿里妈妈 | 15-30s | 2000 | 100000 |
| 多多进宝 | 10-20s | 1000 | 30000 |
```

**这就是 MinerU 的价值**：`pypdf` 抽完表格只剩一列裸文本，行列对应关系全丢——切块入库后，「巨量千川的预算上限是多少」这种问题根本检索不到；MinerU 保住表格后，切块能把整张表作为一个语义单元保留。

**接入后适配器的实际行为**：`parser = mineru(flash)`（不再回退），输出 492 字符、可正常切块（440 token），**表格落在块内可被检索**。

### 接入时踩的两个坑

| 坑 | 现象 | 修法 |
|---|---|---|
| **HOME 不可写** | `WinError 5 拒绝访问: C:\Users\...\.mineru` | 子进程 `USERPROFILE`/`HOME` 指到仓库内可写目录 |
| **子进程写不了系统临时目录** | `Permission denied: ...\Temp\mineru_out_xxx\xxx.md` | 输出目录改到仓库内 `.mineru-out/`（可用 `MINERU_OUT_DIR` 覆盖） |

第 2 个坑很隐蔽：**父进程能写 temp，不代表子进程能写**——沙箱 ACL 对进程不同。

---

## 三、切块与 overlap

### 参数

| 参数 | 默认 | 环境变量 | 含义 |
|---|---|---|---|
| chunk_size | **512** token | `RAG_CHUNK_SIZE` | 单块上限 |
| overlap | **50** token（≈10%） | `RAG_CHUNK_OVERLAP` | 相邻块重叠 |

- 按 **tiktoken `cl100k_base`** 计数，不按字符（中文 1 token ≈ 1.1–1.4 字）
- 短文档（≤ chunk_size）**不切**——一条 130 字的文案本来就该整条用
- 切分顺序：段落 → 句末标点 → 换行，**尽量在自然边界断开**

### 实测（2025 字的长文档）

| chunk_size | overlap | 块数 | token 分布 | 越界? | 块间字符重叠 |
|---|---|---|---|---|---|
| 256 | 25 | 10 | 224–244 | ❌ 无 | 18–23 字 |
| **512** | **50** | **5** | **433–486** | **❌ 无** | **44–48 字** |
| 1024 | 100 | 3 | 381–989 | ❌ 无 | 89–91 字 |

**三个关键性质全部成立**：每块 ≤ 上限、块间有重叠、无脏字符。

### 修掉的三个坑

**① langchain 的 `from_tiktoken_encoder` 在这版里不按 token 切**

设 `chunk_size=512`，出来的块只有 232 字（≈258 token）——它按**字符**算了。
改成自己按 token 贪心累加，行为才可预测。

**② token 边界切断汉字会产出 `�`**

`tiktoken.decode(ids[-n:])` 在切到多字节汉字中间时返回替换字符，
块首出现过 `�单价，文案要匹配…`。这种脏字符直接入库会污染语料。
修法：`decode(errors="ignore")` + 逐个丢首字符直到结果是原文真子串。

**③ 整句回退做 overlap 不可靠**

最初想「把上一块最后几整句拿来重叠」，但中文一句话常超过 50 token，
装不下就退化成零重叠（实测块0→块1 完全没有重叠）。
改成**按 token 截尾**，重叠才稳定。

---

## 四、图片向量与视觉检索

### 为什么要做

不是「以图搜图找素材」，而是：**把生成过的优质海报入库，下次生成时召回相似风格的图当视觉参考**。

### 1024 维描述子（多特征拼接）

| 组成 | 维度 | 表达什么 |
|---|---|---|
| 全局 HSV 联合直方图（H8×S8×V8） | 512 | 整体色调分布 |
| 4×4 网格 × 色调 H8 | 128 | **空间布局**：左上暖还是冷 |
| 4×4 网格 × (S4×V4) | 256 | 各区域饱和度/明度 |
| 4×4 网格 × 8 方向梯度 | 128 | 纹理与边缘走向 |
| **合计** | **1024** | 与 Milvus 集合维度对齐，无需补零 |

### 为什么默认不用 CLIP

CLIP 语义更强，但需联网下载数百 MB 模型；当前环境下载会失败。
所以默认走离线可用的多特征描述子，`provider` 字段会标注实际用了哪个：

```bash
IMAGE_EMBED_PROVIDER=hist   # 默认
IMAGE_EMBED_PROVIDER=clip   # 需模型，切换后要重建集合（两种向量不可混用）
```

**诚实边界**：它检索的是**视觉风格相似**（颜色/布局/纹理），不是**语义相同**（图里是不是同一件东西）。

### 实测：区分度

合成图相似度矩阵：

| | blue | green | red | red_stripe | white |
|---|---|---|---|---|---|
| **blue** | 1.000 | 0.039 | **0.287** | 0.157 | 0.089 |
| **green** | 0.039 | 1.000 | 0.074 | 0.075 | 0.089 |
| **red** | 0.287 | 0.074 | 1.000 | **0.865** | 0.747 |
| **red_stripe** | 0.157 | 0.075 | 0.865 | 1.000 | 0.902 |

- 异色（红↔蓝）**0.287**，同色不同纹理（红↔红条纹）**0.865**
- **排序正确**：同色系永远比异色系更近 ← 检索真正需要的性质
- 检索实测：查询红图，Top-1 是自身，其后是同色红条纹

**加权前后对比**：不加权时红↔蓝是 0.530（构图相同拉高了相似度），
加了分部权重（颜色 1.6×、空间色调 1.4×、饱和度 0.8×、梯度 0.7×）后降到 0.287。

### 近重复检测（dHash）

每张图另存一个 64 位 dHash（灰度差值哈希），汉明距离 ≤ 5 判为近重复。
用途：同一张图被重复入库时去重。**实测 5 张图检出 2 对近重复。**

---

## 五、LLM 相关性复核（第二级拒识）

### 两级设计

```
召回候选 ──► ① 相似度阈值（RETRIEVAL_MIN_SCORE=0.05）
                    │  便宜的确定性过滤，挡掉完全不沾边的
                    ▼
            ② LLM 复核（RAG_LLM_JUDGE=1）
                    │  读语义，挡掉「字面像但不是同一品类」
                    ▼
              保留的相关参考 → 注入 Prompt
```

**为什么两级**：阈值便宜但只能比字面/向量距离；LLM 准但要花钱。
**先粗筛再精判**，把 LLM 调用次数压到最低。

### 实测（5 个场景，桩替换 LLM，零费用）

| 场景 | 结果 |
|---|---|
| LLM 返回 `{"relevant":[0]}` | ✅ 保留 1、剔除 1 |
| LLM 返回带 ` ```json ` 包裹 | ✅ 容错解析，保留 2 |
| LLM 返回 `{"relevant":[]}` | ✅ **全部不相关 = 拒识** |
| **LLM 抛异常** | ✅ **保留全部候选**（复核失败不能把正常召回归零） |
| `RAG_LLM_JUDGE=0` | ✅ 不调用 LLM，直接透传 |

**最关键的是第 4 条**：判官挂了不能让检索"静默归零"——这比多召回几条错参考更糟。

### 暴露给调试/演示的接口

```
POST /api/v1/copy/retrieval-preview
  {"category": "蓝牙耳机", "platform": "xiaohongshu", "top_k": 3}
→ {"judge": {"judged": true, "kept": 2, "dropped": 1, "rejected_all": false},
   "references": [...]}
```
**不用真跑一次生成，就能把两级拒识摊开看。**

---

## 五之一、Reranker：补上 Cross-Encoder 精排

### 原来是缺的

代码里**完全没有 reranker**——只有「同平台优先」这种规则重排。
那是业务偏好排序，不是语义精排。

### 为什么需要

向量召回是**双塔**：query 和 doc 各自编码成一个点再算余弦。
快，但**丢掉了 query 与 doc 之间的细粒度交互**。

Cross-Encoder 把 `(query, doc)` 拼起来过一遍模型直接出相关性分数，精度明显更高，
代价是每个候选一次前向。所以标准做法是两段式：**宽召回 → 精排**。

### 模型与下载

`BAAI/bge-reranker-base`（1061 MB，CPU 可跑）。

**HuggingFace 在本网络环境不可用**（实测 403 / 连接重置），改走 **modelscope**：
1061 MB 47 秒下完。下载脚本 `scripts/download_reranker.py`，模型目录 `models/`（已 gitignore）。

### 三段式检索链路

```
类目 → 召回 max(top_k×3, 9) 条
     → 同平台优先（业务偏好）
     → Cross-Encoder 精排 → 取 top_k
     → LLM 语义复核
```

### 实测延迟（CPU）

| 候选数 | 热态耗时 |
|---|---|
| 首次（含加载） | 26 s |
| 1 条 | 73 ms |
| 3 条 | 139 ms |
| 9 条 | 457 ms |

约 **50 ms/条**。放在文案生成（5–10 秒）之前可接受；若要更低延迟需上 GPU 或换 tiny 模型。

### A/B 评测：一个重要且诚实的结论

**A 段 真实召回**（84 个类目×平台组合）

| 指标 | 关精排 | 开精排 | 变化 |
|---|---|---|---|
| P@3 | 100.00% | 100.00% | **+0.00%** |
| Recall@3 | 100.00% | 100.00% | +0.00% |
| MRR | 100.00% | 100.00% | +0.00% |

**精排在真实链路上收益为 0**——因为降级通道里候选本来就全是同类目
（无关的被 0.05 阈值清掉了）。这时候再排也是排一堆正确答案。

**B 段 对抗混合**（每例 1 条同类目 + 2 条异类目，40 例）

| 指标 | 关精排 | 开精排 | 变化 |
|---|---|---|---|
| **Top-1 命中** | **35.71%** | **100.00%** | **+64.29%** |
| **MRR** | **63.69%** | **100.00%** | **+36.31%** |
| P@3 | 33.33% | 33.33% | +0.00% |

**这才是精排的价值所在**：它不改变"召回哪几条"，只改变"谁排第一"。
P@3 不变（候选集没变），但 Top-1 从 35.71% 打到 100%。

### 一句话结论

> **精排解决的是排序，不是召回。** 用它之前先确认瓶颈在排序——
> 我们是 B 段那种「候选里混了错品类」的场景才受益；若候选已经全对，加它纯属白付延迟。

### fail-open 设计

模型缺失、加载失败、推理异常 → **一律保持召回顺序**，`reranked=False`。
精排是增强不是必需，不能因为模型问题让检索挂掉。

---

## 五之二、降级链：补全后才是真的"三层"

### 原来写的"三层"有一层是空的

文档里一直写「熔断 → 本地 TF-IDF → 空参考」三层。但**查代码发现第二层在运行中的服务里是空的**：

```
应用启动后 _memory_docs = 0 条
```

原因：`_memory_docs` 只在**「本进程内调用过 insert_data」**时才会被写入。
应用正常启动时没人写过它，所以：

> Milvus 不可达 → 熔断 → 走「本地 TF-IDF」→ **`_memory_docs` 为空 → 返回 `[]` → 直接空参考**

也就是说实际只有两层，中间那层是摆设。

### 修法：降级时惰性载入本地语料

新增 `seed_memory_from_disk()`，在 `_search_memory()` 首次被调用时，
从 `data/processed/copy_processed.json` 载入语料（87 条）到内存，再用 TF-IDF 现算余弦。

不放在启动时载入的原因：**启动路径越短越好**，降级是低频路径，用到时再付这个成本。

### 实测（修复前后对比）

| | 修复前 | 修复后 |
|---|---|---|
| `_memory_docs` | **0** 条 | **87** 条（自动载入） |
| 查「蓝牙耳机」 | 0 条 | **3 条**（耳机 / earbuds / Wireless Earbuds） |
| 首条结果 | — | 「百元耳机卷疯了！音质直接对标千元」score=0.2272 |
| 查「量子计算芯片」 | 0 条 | 0 条（阈值正确过滤） |

**为什么值得做本地降级而不是直接空参考**：
空参考是「功能可用但效果断崖式下降」——文案会退回模板化输出；
本地检索是「效果也尽量保住」。87 条语料放内存成本几乎为零，所以这层值得做。

**边界（要主动说）**：本地降级**只在语料小时成立**。如果知识库是 10 万条，
本地现算不可行，那时的正确做法是降级为空参考，而不是硬扛。

### 回归保护

新增单测 `test_fallback_seeds_local_corpus`：
- 清空内存后调用应载入 >0 条
- 已有数据时重复调用应返回 0（不重复载入）

**测试数：157 → 158 个**。

---

## 六、Token 账本

### 为什么单独做

`metrics` 里的 token 计数是**进程内累加**——重启归零，也没有时间维度，
只能答「这个进程用了多少」，答不了「这个月用了多少」。

改成 **JSONL 追加写**：每次 LLM 调用追加一行，读取时按窗口聚合。

```json
{"at":"2026-11-04T19:30:12","model":"deepseek-flash","scene":"copywriter",
 "prompt_tokens":812,"completion_tokens":176,"total_tokens":988,"cost":0.001164}
```

### 聚合能力

| 窗口 | 参数 |
|---|---|
| 今天 | `period=today` |
| **本月**（默认） | `period=month` |
| 今年 | `period=year` |
| 全部 | `period=all` |
| 最近 7/30 天 | `period=7d` / `30d` |

返回：整体（调用次数/输入输出 token/估算成本）、**按日**、**按月**、**按模型**、
以及**按日均推算的 30 天用量**。

### 实测

```
今日聚合     calls=1  tokens=1000
本月聚合     calls=2
全部聚合     calls=3  tokens=1600  成本估算=0.00222
按模型拆分   ['deepseek-flash', 'text-embedding-v4']
30 天推算    日均=623.3 → 18699 tokens
落盘文件     data/metrics/token_usage.jsonl
```

### 三个 API

```
GET  /api/v1/metrics/token-usage?period=month
GET  /api/v1/metrics/token-usage/rows?limit=20
POST /api/v1/metrics/token-usage/record      # 手动回填/压测用
```

**诚实边界**：`cost` 是按单价表的**估算**，不是服务商账单；
月度总量取决于真实调用量，本项目投放是 mock，没有稳定调用量，
所以只能给"单次约 600–900 token + 按日均推算"的口径。

---

## 七、新增/修改清单

### 新增

| 文件 | 作用 |
|---|---|
| `backend/app/common/doc_ingest.py` | 文档解析（5 种格式 + MinerU 可插拔）+ token 切块 |
| `backend/app/common/image_vector.py` | 1024 维视觉描述子 + 入库/检索/去重 |
| `backend/app/common/token_usage.py` | Token 追加账本 + 按日/月/模型聚合 |
| `scripts/verify_rag_pipeline.py` | 40 项端到端断言 |
| `data/samples/docs/` | 测试文档（txt/md/csv/docx/pdf） |
| `data/samples/images/` | 合成测试图（红/蓝/绿/红条纹/白） |

### 修改

| 文件 | 改动 |
|---|---|
| `backend/app/common/milvus_cli.py` | 新增 `insert_vectors()` / `search_vector()` / `_search_memory_vector()` —— 支持"自带向量"写入与检索（图片用） |
| `backend/app/agent/copywriter.py` | 新增 `judge_relevance()` / `retrieve_with_judge()`；生成流程接入两级拒识；token 记账 |
| `backend/app/api/copy_router.py` | 新增 `POST /retrieval-preview`（只看检索与拒识，不生成） |
| `backend/app/api/metrics_router.py` | 新增 3 个 token-usage 接口 |
| `backend/tests/test_core.py` | 新增 29 个测试（**129 → 158**） |
| `.env.example` | 补 RAG 切块/图片/拒识/单价配置 |

---

## 八、复现

```bash
# MinerU 在独立 venv，跑验证前指向它（不设则自动回退 pypdf）
export MINERU_BIN=$PWD/.venv-mineru/Scripts/mineru.exe    # Windows
export MINERU_HOME=$PWD/.mineru-work/home

# 端到端验证（49 项断言，LLM 用桩不花钱）
python scripts/verify_rag_pipeline.py

# 单元测试
cd backend && python -m unittest tests.test_core    # 158 个

# 起服务后可看拒识过程
python -m uvicorn app.main:app --port 8001
curl -X POST http://127.0.0.1:8001/api/v1/copy/retrieval-preview \
  -H "Content-Type: application/json" \
  -d '{"category":"蓝牙耳机","platform":"xiaohongshu","top_k":3}'
curl "http://127.0.0.1:8001/api/v1/metrics/token-usage?period=month"
```

---

## 九、面试可以直接用的说法

| 问题 | 答法 |
|---|---|
| **文档怎么解析、怎么切块** | 「解析器可插拔：MinerU → pypdf → python-docx → 内置文本。按 **tiktoken** 算 token 切块，`chunk_size=512 / overlap=50`（约 10%），短于上限不切。实测 2025 字文档切 5 块，最大 486 token，块间重叠 44–48 字。」 |
| **MinerU 用了吗** | 「**用了，装在独立 venv 里**。它带 47 个包（gradio/onnxruntime/modelscope），直接装会污染项目的 FastAPI 环境，所以用 `--system-site-packages` 建独立环境，408 MB。接法是 4.x 的 `mineru parse` CLI + 本地服务，服务没起会自动拉起。**实测同一份带表格的 PDF：pypdf 抽出 392 字符但表格变成一列裸文本，MinerU 492 字符且还原成 Markdown 表格**——这就是要不要上它的判断依据。」 |
| **overlap 怎么设的** | 「50 token ≈ 10%。作用是防止语义在块边界被切断——上一块的尾部会重复出现在下一块开头。我用 token 截尾而不是整句回退，因为中文一句话常超过 50 token，整句回退会退化成零重叠。」 |
| **图片怎么进 RAG** | 「颜色不是用来找图，是用来**找视觉风格相似的海报**当参考。1024 维描述子 = 全局 HSV + 4×4 空间色调 + 饱和度明度 + 梯度方向；另存 64 位 dHash 做近重复去重。」 |
| **图片用 CLIP 吗** | 「**设计成可切但默认不用**——CLIP 要联网下模型。默认描述子实测同色 0.865 / 异色 0.287，排序是对的。诚实说：它检索的是视觉风格，不是语义相同。」 |
| **无关类目怎么拒识** | 「**两级**：第一级是相似度阈值（0.05），便宜的确定性过滤；第二级是 LLM 复核，读语义挡掉『字面像但不是同一品类』。**LLM 挂了就保留全部候选**——判官不能把正常召回归零。」 |
| **token 消耗多少** | 「做了追加账本，能按日/按月/按模型聚合，还能按日均推算 30 天。**单次文案约 600–900 token**。但月度总量我没有真实数据——投放是 mock，没有稳定调用量，只能说单次口径和推算值。」 |

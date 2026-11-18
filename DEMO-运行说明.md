# 演示运行说明（当前会话实测可用）

> 这份文件由 DSH 会话生成，用于说明**怎么把项目跑起来演示**、当前环境哪些功能可用、
> 以及怎么获得完整效果。项目本身的说明见 [readme.md](readme.md)。

**讲解材料（两份，建议都看）**

| 文件 | 用途 |
|---|---|
| [前端页面讲解稿.md](前端页面讲解稿.md) | **对着屏幕逐页讲**用：八个页面每页的开场句、讲什么、机制、可能被追问的问题；含时间分配建议 |
| `docs/presentation-guide.md` | 整体介绍稿：30 秒 / 2 分钟 / 5 分钟话术，按听众分层的讲点，演示动线 |

---

## 一、现在就能用（已经在跑）

浏览器打开：**http://127.0.0.1:5173**

| 项 | 值 |
|---|---|
| 演示账号 | `demo` / `demo123456` |
| 后端 API | http://127.0.0.1:8001 （`/docs` 接口文档、`/health` 健康检查） |
| 前端入口 | http://127.0.0.1:5173 |

> **注意端口**：后端在本环境跑在 **8001**（不是 readme 里写的 8000）。
> 原因见第二节第 1 条——8000 那个实例的工作目录是 `backend/`，它的 `data/` 不可写。
> `demo_server.py` 已把 `/api`、`/media`、`/ws` 全部代理到 8001。

已实测可用的链路：

- ✅ 注册 / 登录 / 退出（真实 SQLite 用户表，PBKDF2 加盐哈希）
- ✅ 首页概览指标与动态视觉
- ✅ 广告生成：真实调用 DeepSeek 生成文案（实测输出见下），文案审查结论正常返回
- ✅ 新增自定义商品、商品下拉列表
- ✅ 存到投放 / 半成品暂停 / 删除
- ✅ 广告投放：四平台（千川 / 聚光 / 阿里妈妈 / 多多进宝）与投放历史
- ✅ 历史广告发售：8 条记录（含 1 条红色「半成品」）
- ✅ 审查中心：违禁词审查 / 预算审查 / 待审批 / 已处理
- ✅ `/media` 海报与视频素材可正常访问
- ✅ WebSocket `/ws/dashboard` 心跳往返（页面顶部显示「实时通道已连接」）

实测的大模型文案输出（商品：法式碎花连衣裙，关键词：约会穿搭）：

> **标题**：3个同事追着问链接！这条法式碎花裙太会了
> **标签**：#法式碎花连衣裙 #度假穿搭 #约会穿搭 #显瘦连衣裙 #梨形身材穿搭

---

## 二、当前环境的三处限制（重要）

本会话运行在 DSH 的 **workspace-write 沙箱**里，有三个硬性限制：

### 1. 不能写 `backend/`、`frontend/`、`data/`、`docs/` 子目录

只有**直接位于仓库根目录的文件**可写。因此：

- 无法执行 `npm install`（装不到 `frontend/node_modules`），所以**跑不了 `npm run dev`**；
  当前 5173 端口是我用 `demo_server.py` 托管的**已构建产物** `frontend/dist`
  （该产物与当前源码时间戳一致，内容相同）。
- 后端原本要写 `<仓库>/data/*.json` 和 `backend/data/app.db`，全部会被拒。
  所以 `demo_bootstrap.py` 把持久化路径重定向到了根目录下可写的 `runtime/`。

### 2. 我不能「看到」页面，也不能截图

本会话**无法启动任何浏览器**，所以我看不到你的界面，也没法自己截图核对：

- Playwright 启动 Chromium → `PermissionError [WinError 5]`
  （`asyncio\windows_utils.py` 创建命名管道失败）
- 直接用 Chrome/Edge `--headless --screenshot` → `FATAL:platform_channel.cc(89) Check failed: 拒绝访问`
  （Chrome 自己的 mojo IPC 同样走命名管道）

「不能创建命名管道」是沙箱的文档化边界，**会话内无法绕过**。
因此我对页面的所有描述都来自**读源码 + 调接口**，不是来自渲染结果；
像素级的东西（对齐、间距、换行、动画）我无法确认，需要你反馈或截图给我。

### 3. 不能启动 Chromium（图文海报渲染失败）

`图文生成失败：[WinError 5] 拒绝访问。`

与上一条同源：Playwright 通过命名管道与浏览器通信。
普通子进程管道是正常的（已验证），只有浏览器启动受影响。

影响：**图文广告生成**失败。**视频广告生成**另有独立原因——`.env` 里 `VIDEO_API_KEY=***` 是占位符，未配置真实密钥。

> 也就是说：本环境适合演示「文案生成 + 审查 + 审批 + 投放 + 历史」，
> 不适合现场演示「图文渲染 / 视频生成」。

---

## 三、完整效果的启动方式（推荐正式演示前做一次）

要拿到**全部功能**（含图文渲染），需要在**沙箱外**启动后端，两种方式任选：

### 方式 A：你本机终端手动启动（最简单，推荐）

在**普通 PowerShell 窗口**（不是 DSH 里）执行：

```powershell
cd C:\项目\project_ecommerce_agent项目\guanggao-agent\backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

然后再开一个窗口启动前端：

```powershell
cd C:\项目\project_ecommerce_agent项目\guanggao-agent\frontend
npm install
npm run dev
```

浏览器打开 http://localhost:5173 即可。这样用的是项目**原始路径**（`data/`、`backend/data/`），
不需要 `demo_bootstrap.py`，功能与设计完全一致。

### 方式 B：把 DSH 会话切到「完全权限」后重启

重启会话并选择完全权限，我就能直接 `npm install`、起 `npm run dev`、
并按原始路径启动后端，图文渲染也能工作。

### 无论哪种方式，都请先改掉这个配置错误

`.env` 第 15 行：

```ini
LLM_MODEL=deepseek-v41-flash     # ← 无效模型名，DeepSeek 返回 400
LLM_MODEL=deepseek-flash         # ← 改成这个
```

**为什么必改**：模型名错误时 DeepSeek 返回 HTTP 400，`copywriter.py` 捕获异常后
**静默降级为模板文案**——不报错，但输出是「夏季纯棉T恤｜真的太好用啦✨」这类模板，
完全没有大模型能力，演示效果会大打折扣。

> 本次会话我没有改动你的 `.env`（该文件在沙箱里不可写），
> 而是在 `demo_bootstrap.py` 里**在内存中**把模型名改成 `deepseek-flash`，仅当前进程生效。

---

## 四、本次会话新增的文件

| 文件 | 作用 | 正式演示后可否删除 |
|---|---|---|
| `demo_bootstrap.py` | 沙箱适配启动器：把持久化重定向到 `runtime/`、内存修正模型名、播种演示账号 | 可以（沙箱外不需要） |
| `demo_server.py` | 托管 `frontend/dist` 并把 `/api`、`/media` 代理到后端 8001 | 可以（有 `npm run dev` 后不需要） |
| `demo_tools/copy_media.py` | 把已有海报/视频素材复制到 `runtime/`，让 `/media` 可访问 | 可以 |
| `demo_tools/probe_flow.py` | 全链路自检：登录 → 生成 → 存投放 → 校验物料/历史 | 可以 |
| `demo_tools/probe_asset.py` | 图文/视频生成与幂等性探针 | 可以 |
| `demo_tools/show_result.py` | 取某次生成会话快照并写成 UTF-8 JSON | 可以 |
| `runtime/` | 运行期数据（SQLite、JSON、媒体、日志），**可随时删除重建** | 可以 |
| `docs/presentation-guide.md` | 投屏演示指南（介绍稿 + 演示动线） | 建议保留 |

`runtime/` 与上面几个脚本都不影响项目本身，`git` 里是未跟踪状态。

---

## 五、本次会话发现的项目问题（建议修）

1. **`LLM_MODEL` 配置错误**（见第三节）——最影响演示，**必修**。
2. **`.env` 已被 git 跟踪且含真实 `LLM_API_KEY`**。建议 `git rm --cached .env`，
   确认 `.gitignore` 生效，并**轮换这把已进入版本库的密钥**。
3. **`docs/presentation-guide.md` 与 `readme.md` 描述已过时**：readme 还在讲
   「任务拓扑看板 / 定价与竞品监控 / /canvas」，而当前系统已重构为
   「首页 / 广告生成 / 批量生成 / 广告投放 / 历史广告发售 / 审查中心 / 充值中心 / 帮助中心」。
   `readme.md` 第三节的启动步骤也缺少 `batch`、`billing`、`store` 等新模块说明。
4. **Milvus / Redis 未启动**：`.env` 指向 `192.168.198.131`，`/health` 返回
   `degraded`。系统会降级运行（向量召回不可用），演示时可主动说明；
   或在根目录 `docker compose up -d` 后把地址改回 `127.0.0.1`。

---

## 六、沙箱内重启这两个服务（如果进程掉了）

```powershell
# 在项目根目录
python demo_bootstrap.py --port 8001     # 后端（另开一个 DSH 后台任务）
python demo_server.py                    # 前端 + API 代理
```

自检：

```powershell
python demo_tools/probe_flow.py          # 全链路应全绿
```

注意：`demo_server.py` 里 `BACKEND_PORT` 已指向 `8001`。
若端口被上次残留进程占用，先清理：

```powershell
netstat -ano | Select-String ":8001|:5173" | Select-String LISTENING
Stop-Process -Id <PID> -Force
```

# 部署手册（单机版，一天能跑起来）

> 目标：一台云服务器上，用 Docker Compose 起「前端 + 后端」，公网能访问、能登录、能点一次生成。
>
> **不包含**：真实平台投放（需要公司资质与巨量千川授权）、多实例水平扩容（需要 MySQL + 共享 checkpointer）。

---

## 0. 需要准备什么

| 项 | 说明 | 花钱吗 |
|---|---|---|
| 云服务器 | **2 核 4G，Ubuntu 22.04**（内存别低于 4G，Playwright 渲染海报吃内存） | 几十元/月 |
| 域名 | 可选。没有就用 `http://IP` 访问 | 可选 |
| DeepSeek API Key | 文案生成要用；不填也能跑，但文案会退化成模板 | 按量计费 |

**不需要**：Milvus（代码有 TF-IDF 降级）、Redis（有线程池/MySQL 队列）、平台授权。

---

## 1. 服务器初始化

```bash
# 用 root 或 sudo 用户登录服务器
ssh root@<你的服务器IP>

# 装 Docker（官方脚本）
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# 验证
docker --version && docker compose version
```

---

## 2. 把项目传上去

**方式 A：从本机 scp**（项目不大，几分钟）

```bash
# 在本机项目根目录执行
# 排除掉不该上传的大目录
tar --exclude='.venv*' --exclude='models' --exclude='node_modules' \
    --exclude='.git' --exclude='data/media' --exclude='.mineru-*' \
    -czf /tmp/app.tar.gz .

scp /tmp/app.tar.gz root@<服务器IP>:/opt/
ssh root@<服务器IP> "mkdir -p /opt/ecommerce-agent && tar -xzf /opt/app.tar.gz -C /opt/ecommerce-agent"
```

**方式 B：git clone**（如果仓库已推送）

```bash
ssh root@<服务器IP>
git clone <你的仓库地址> /opt/ecommerce-agent
```

---

## 3. 填生产配置

```bash
cd /opt/ecommerce-agent
cp .env.prod.example .env.prod

# 至少填这一项，否则文案生成会退化成模板
vi .env.prod
#   LLM_API_KEY=sk-xxxxxxx
```

**关键三项**（默认值已经是对的，**不要乱改**）：

| 变量 | 值 | 为什么不能改 |
|---|---|---|
| `STORE_BACKEND` | `json` | 单机用 JSON 最稳；改 mysql 需要先起 MySQL |
| `QIANCHUAN_DELIVERY_MODE` | **`mock`** | 没有平台授权，改成 real 会直接报错 |
| `MILVUS_URI` | 空 | 留空即走本地 TF-IDF 降级，检索照样可用 |

---

## 4. 先跑预检（1 分钟，能省掉大部分失败）

```bash
cd /opt/ecommerce-agent
bash deploy/preflight.sh
```

它会检查六类东西，**有问题会明确指出怎么修**：

| 检查 | 拦什么 |
|---|---|
| 内存 / 磁盘 / CPU | 4G 以下会提醒（Playwright 渲染海报吃内存） |
| Docker 与 compose | 守护进程没起、只有 v1 的 `docker-compose` |
| 项目文件 | 少 Dockerfile / entrypoint / 语料 |
| `.env.prod` | `LLM_API_KEY` 没填、`DELIVERY_MODE=real`（没授权会直接报错） |
| 网络与端口 | 出网不通、80 被占 |
| **依赖清单完整性** | 曾经漏过 `jieba/sklearn/tiktoken`——漏了会**静默降级**而非报错 |

看到 `预检通过` 再往下走。**有硬性问题它会退出码非 0**，别硬上。

---

## 5. 构建并启动

```bash
cd /opt/ecommerce-agent

# 首次构建（下载基础镜像 + 装依赖 + Chromium，约 8–15 分钟）
docker compose -f docker-compose.prod.yml up -d --build

# 需要本地 embedding / 精排（多 1GB torch）时：
# docker compose -f docker-compose.prod.yml build --build-arg INSTALL_ML=1

# 看日志（等 "Application startup complete"）
docker compose -f docker-compose.prod.yml logs -f backend
```

**看到这行就算起来了**：

```
[entrypoint] 数据卷为空，灌入种子数据：/app/seed-data → /app/data
[entrypoint] 启动：uvicorn app.main:app --host 0.0.0.0 --port 8000 ...
INFO:     Application startup complete.
```

---

## 6. 验证（四步，缺一步不算成功）

```bash
# ① 容器都在跑且 healthy
docker compose -f docker-compose.prod.yml ps

# ② 后端健康检查
curl -s http://127.0.0.1:8000/health | head -c 300   # 容器内端口，需进容器或走 nginx
curl -s http://<服务器IP>/healthz | head -c 300      # 走 nginx

# ③ 前端页面能打开
curl -s -o /dev/null -w "%{http_code}\n" http://<服务器IP>/     # 期望 200

# ④ 登录接口通
curl -s -X POST http://<服务器IP>/api/v1/auth/login \
     -H "Content-Type: application/json" \
     -d '{"username":"demo","password":"demo123456"}'
```

**然后用浏览器打开 `http://<服务器IP>`，用 `demo / demo123456` 登录**，能看到 10 个页面、能点一次文案生成，就算 A 路完成。

---

## 7. 云厂商安全组 / 防火墙

**必须开 80 端口**（如果要用 HTTPS 再开 443）。阿里云/腾讯云默认只开 22。

```bash
# 服务器内防火墙（如果开了 ufw）
ufw allow 80/tcp
ufw allow 443/tcp
```

---

## 8. 常用运维命令

```bash
cd /opt/ecommerce-agent
C="docker compose -f docker-compose.prod.yml"

$C ps                      # 状态
$C logs -f backend         # 后端日志
$C logs --tail=200 frontend
$C restart backend         # 重启后端
$C down                    # 停止（数据卷保留）
$C down -v                 # 停止并删数据卷（⚠️ 会丢所有数据）

$C up -d --build           # 改代码后重新构建并起
```

**备份数据**（就一个卷）：

```bash
docker run --rm -v ea_data:/data -v $(pwd):/backup alpine \
    tar -czf /backup/data-$(date +%F).tar.gz -C /data .
```

---

## 9. 一定会踩的六个坑

| # | 现象 | 原因 | 解法 |
|---|---|---|---|
| 1 | 后端日志报 `Milvus 熔断` 刷屏 | 正常现象，不是错误 | 不用管，检索走 TF-IDF 降级 |
| 2 | 页面能开但接口 502 | 后端还没起来 / 不 healthy | `logs -f backend` 等 `startup complete`；前端 `depends_on: service_healthy` 已处理 |
| 3 | 中文海报全是方块 | 镜像缺中文字体 | Dockerfile 已装 `fonts-noto-cjk`，别删 |
| 4 | 重启后图片/视频打不开 | 素材没落持久卷 | compose 已把 `ea_data:/app/data` 挂上；别用 `down -v` |
| 5 | 生成要等很久然后 504 | Nginx 默认超时 60s | nginx.conf 已把 `/api/` 的 read timeout 调到 300s |
| 6 | 首次访问慢几秒 | 后端首次加载 + 种子灌入 | 正常，之后正常 |

---

## 10. 想加 HTTPS（可选，10 分钟）

用 Caddy 替掉 Nginx 的 80 端口最简单，或者：

```bash
# 装 certbot，申请证书
apt install -y certbot
certbot certonly --standalone -d 你的域名     # 需要域名已解析到这台机器

# 然后在 frontend/nginx.conf 加 443 server 块，把证书目录挂进容器
```

**面试演示其实不需要 HTTPS**，`http://IP` 足够。

---

## 11. 从「单机 demo」到「能给自己团队用」还差什么

| 要做 | 为什么 |
|---|---|
| 起 MySQL，`STORE_BACKEND=mysql` | JSON 单实例能跑，但写完再读有全量重写放大 |
| checkpointer 换共享存储 | 现在 `SqliteSaver` 是本机文件，多实例恢复不了审批 |
| 素材换对象存储 | 本地卷只能单机共享 |
| 视频生成挪到独立 worker | 现在占着 web 进程轮询 300 秒 |
| 加监控与告警 | 现在只有 `/health` |

**这些是"内部试用"要做的，不是"公网 demo"要做的。** 别一次全做。

---

## 附：面试怎么说

> 「我在云服务器上用 Docker Compose 部署过完整环境——Nginx 托管前端并反代 `/api`、`/ws`、`/media`，后端单 worker，数据卷持久化，公网能访问、能登录、能跑生成。
>
> 但有三点我要说清：
> **一、投放是 mock 模式**——巨量千川要企业资质和应用审核，个人拿不到授权。
> **二、Milvus 没启**——它要 etcd + MinIO，2C4G 起不动；代码本来就有 TF-IDF 降级通道，检索照样可用。
> **三、后端只跑 1 worker**——JSON 存储和 SqliteSaver checkpointer 都是单机假设，要扩多实例得先换 MySQL 和共享 checkpointer。」

**三句话，把"能跑 / 我自己部署的 / 边界在哪"都说清了。**

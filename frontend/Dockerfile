# 前端镜像：Node 构建 → Nginx 托管静态 + 反代后端
#
# 前端代码里 API 都是相对路径（/api/v1、/ws/dashboard、/media），
# 所以构建时不需要注入后端地址——由 Nginx 在同一域名下转发即可。

FROM node:20-alpine AS build

WORKDIR /app

# 先拷依赖清单，利用层缓存
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY . .
RUN npm run build

FROM nginx:alpine

# 时区与健康检查工具
RUN apk add --no-cache tzdata curl && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1/ >/dev/null || exit 1

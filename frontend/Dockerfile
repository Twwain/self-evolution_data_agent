# ── Stage 1: 构建静态产物 ──
FROM node:20-alpine AS build

WORKDIR /app

# 先 COPY 依赖清单 (lockfile + package.json) 利用 layer 缓存
COPY package.json package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com

# 再 COPY 源码构建
COPY . .
RUN npm run build

# ── Stage 2: nginx 托管 ──
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

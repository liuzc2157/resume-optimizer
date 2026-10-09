# ---- 构建阶段：Next.js standalone 构建 ----
# 注意：本 Dockerfile 以「仓库根」为构建上下文（Railway 默认），
#       因此所有 COPY 源路径带 frontend/ 前缀；服务的 Root Directory 请保持空白。
FROM node:20-alpine AS build
WORKDIR /app

# 先装依赖（利用层缓存）
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# NEXT_PUBLIC_API_URL 为构建期变量：Railway 在「构建」作用域注入后端域名
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

# ---- 运行阶段：Next.js standalone（与后端同构，直接监听 Railway 注入的 PORT）----
FROM node:20-alpine
WORKDIR /app
ENV HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]

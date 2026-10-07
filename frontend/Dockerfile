# ---- 构建阶段：Next.js 静态导出（out/）----
FROM node:20-alpine AS build
WORKDIR /app

# 先装依赖（利用层缓存）
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# NEXT_PUBLIC_API_URL 为构建期变量：Railway 在「构建」作用域注入后端域名
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

# ---- 运行阶段：nginx 托管静态文件 ----
# nginx 配置直接内联写入（避免依赖构建上下文中的 nginx.conf 文件，提升构建兼容性）
FROM nginx:1.27-alpine
COPY --from=build /app/out /usr/share/nginx/html
RUN printf 'server {\n  listen 80;\n  server_name _;\n  root /usr/share/nginx/html;\n  index index.html;\n  location / { try_files $uri $uri/ /index.html =404; }\n}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80

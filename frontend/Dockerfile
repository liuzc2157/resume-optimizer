# ---- 构建阶段：Next.js 静态导出（out/）----
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

# ---- 运行阶段：nginx 托管静态文件 ----
FROM nginx:1.27-alpine
COPY --from=build /app/out /usr/share/nginx/html
# 模板用 ${PORT}（不带默认值语法，envsubst 才能替换）；
# CMD 里先给 PORT 兜底默认 80，再 envsubst 生成配置并启动 nginx。
RUN printf 'server {\n  listen ${PORT};\n  server_name _;\n  root /usr/share/nginx/html;\n  index index.html;\n  location / { try_files $uri $uri/ /index.html =404; }\n}\n' > /etc/nginx/conf.d/default.conf.template
CMD ["/bin/sh", "-c", "echo \"[nginx] PORT=$PORT\"; PORT=${PORT:-80}; envsubst '${PORT}' < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
EXPOSE 80

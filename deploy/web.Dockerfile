# 前端镜像：多阶段构建 + nginx 反代 /api
# build context 为仓库根（docker-compose.yml 中 context: ..），nginx.conf 随 deploy/ 一同 COPY
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

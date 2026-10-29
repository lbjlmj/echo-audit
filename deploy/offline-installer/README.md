# 离线一键安装（目标 2 小时/所，NFS-05）

流程：
1. `docker image save` 七容器镜像为 tar 包，随安装介质分发（内网无外网环境）
2. 运行初始化向导：建所（租户）、建管理员、导入模板包（templates/ + skills/ + mapping/）
3. `docker compose -f deploy/docker-compose.yml up -d`
4. 验证：访问 `http://<host>:8080/health` 返回 `{"status":"ok"}`

约束：
- 安装介质内不含任何客户数据
- 出站白名单默认全空；顺丰通道需所内显式开启（§4.2.7）

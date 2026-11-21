#!/usr/bin/env bash
# 在本机运行：把代码传到服务器并重启服务。
#   用法: deploy/push.sh [ssh目标]      默认 root@120.77.59.95
# 只覆盖代码，不碰服务器上的数据库和密钥。
set -euo pipefail

HOST="${1:-root@120.77.59.95}"
cd "$(dirname "$0")/.."

ssh "$HOST" 'mkdir -p /opt/lab-dashboard'
# 先清掉旧代码（保留虚拟环境），避免本地删掉的文件残留在服务器上
ssh "$HOST" 'cd /opt/lab-dashboard && find . -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {} +'
COPYFILE_DISABLE=1 tar czf - --no-xattrs --exclude='__pycache__' --exclude='.DS_Store' \
    client server deploy requirements.txt README.md \
    | ssh "$HOST" 'tar xzf - -C /opt/lab-dashboard'
ssh "$HOST" 'bash /opt/lab-dashboard/deploy/install.sh'

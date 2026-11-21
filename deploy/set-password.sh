#!/usr/bin/env bash
# 在服务器上以 root 运行，修改看板的访问密码:
#   ssh -t root@120.77.59.95 bash /opt/lab-dashboard/deploy/set-password.sh
set -euo pipefail
ENV_FILE=/etc/lab-dashboard.env

read -rsp "新的访问密码（至少 8 位，只用字母、数字、- 和 _）: " pw; echo
read -rsp "再输入一次: " pw2; echo
[ "$pw" = "$pw2" ] || { echo "两次输入不一致" >&2; exit 1; }
[[ "$pw" =~ ^[A-Za-z0-9_-]{8,64}$ ]] || { echo "密码不符合要求" >&2; exit 1; }

sed -i '/^LAB_VIEW_PASSWORD=/d' "$ENV_FILE"
echo "LAB_VIEW_PASSWORD=$pw" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"
systemctl restart lab-dashboard
echo "已更新。访问看板时用户名随意，密码填刚才设置的值。"

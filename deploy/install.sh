#!/usr/bin/env bash
# 在服务器上以 root 运行，可重复执行。由 deploy/push.sh 调用，一般不用手动跑。
#   代码      /opt/lab-dashboard
#   数据库    /var/lib/lab-dashboard/lab.db
#   配置      /etc/lab-dashboard.env（访问密码、允许推送的组织等，仅 root 可读）
set -euo pipefail

APP=/opt/lab-dashboard
DATA=/var/lib/lab-dashboard
ENV_FILE=/etc/lab-dashboard.env

command -v python3.11 >/dev/null || dnf install -y -q python3.11

id lab >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /sbin/nologin lab
install -d -o lab -g lab -m 750 "$DATA"

if [ ! -f "$ENV_FILE" ]; then
    (   # 子 shell：收紧的 umask 只作用于配置文件，不影响后面创建的程序文件
        umask 077
        echo "LAB_DB_PATH=$DATA/lab.db" > "$ENV_FILE"
    )
    echo "已生成 $ENV_FILE"
fi
# 允许推送的 GitHub 组织或用户。修改后 systemctl restart lab-dashboard
if ! grep -q '^LAB_ALLOWED_OWNERS=' "$ENV_FILE"; then
    echo "LAB_ALLOWED_OWNERS=POLYU-ZK-GROUP,FranklinZty" >> "$ENV_FILE"
    echo "已设置允许推送的组织: POLYU-ZK-GROUP,FranklinZty"
fi
# 访问密码：没有就随机生成一个，保证看板不会在无密码状态下对外。用 deploy/set-password.sh 修改。
if ! grep -q '^LAB_VIEW_PASSWORD=' "$ENV_FILE"; then
    echo "LAB_VIEW_PASSWORD=$(python3.11 -c 'import secrets; print(secrets.token_urlsafe(12))')" >> "$ENV_FILE"
    echo "已生成访问密码"
fi
chmod 600 "$ENV_FILE"

[ -x "$APP/.venv/bin/python" ] || python3.11 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"

chown -R root:root "$APP"
chmod -R u=rwX,go=rX "$APP"      # 运行用户 lab 只需要读和执行

install -m 644 "$APP/deploy/lab-dashboard.service" /etc/systemd/system/lab-dashboard.service
systemctl daemon-reload
systemctl enable -q lab-dashboard
systemctl restart lab-dashboard

for _ in $(seq 1 20); do
    if curl -fs -o /dev/null http://127.0.0.1/robots.txt; then
        echo "部署完成，服务运行中"
        exit 0
    fi
    sleep 0.5
done
echo "服务没有起来，最近的日志:" >&2
journalctl -u lab-dashboard -n 30 --no-pager >&2
exit 1

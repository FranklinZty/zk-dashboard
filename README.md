# zk-dashboard

课题组项目进度看板：各项目仓库的 CI 把 `project.yaml` 和最近提交推送到服务器，看板只读展示。

## 目录

| 路径 | 内容 |
|---|---|
| `docs/project-yaml.md` | `project.yaml` 规范 |
| `client/project.example.yaml` | 示例，复制到项目仓库根目录并改名为 `project.yaml` |
| `client/.github/workflows/lab-sync.yml` | 工作流，复制到项目仓库的同名路径 |
| `client/lab_sync/` | 校验、采集、签名推送脚本（由工作流自动拉取，不用复制） |
| `server/app.py` | 服务端：`/api/v1/ingest` 写入，`/api/v1/*` 只读接口，托管页面 |
| `server/ccfddl.py` | 会议截稿同步（ccfddl.com → 北京时间），支持手工补充 |
| `server/schema.sql` | SQLite 表结构 |
| `server/static/` | 看板页面：总览 `/`、投稿日程 `/deadlines`、项目详情 `/p/{id}` |
| `tools/seed_demo.py` | 往本地开发服务器灌示例项目 |
| `tools/dev_receiver.py` | 只验证推送链路用的极简接收端 |
| `tests/` | 客户端与服务端测试 |
| `skills/connect-lab-dashboard/` | 给 AI agent 用的接入流程（SKILL.md） |

## 本地开发

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt httpx
.venv/bin/python -m unittest discover -s tests -v
```

本地启动看板并灌入示例数据：

```bash
LAB_SYNC_SECRET=dev-secret LAB_DB_PATH=dev.db .venv/bin/python -m uvicorn server.app:app --port 8765
```

```bash
LAB_SYNC_SECRET=dev-secret .venv/bin/python tools/seed_demo.py
```

然后打开 http://localhost:8765 。

## 看板的筛选链接

筛选条件都在网址里，可以直接分享：`/?owner=alice&due=7`。

| 参数 | 含义 |
|---|---|
| `owner` | 负责人 |
| `member` | 负责人或成员 |
| `stage` | 阶段 |
| `tag` | 标签 |
| `due` | N 天内有投稿日期 |
| `stall` | `1` 放缓或停滞，`2` 仅停滞 |

## 手工补充会议

ccfddl 没有或日期有误的会议，写进 `server/deadlines.manual.yaml`（时间按北京时间填），同 id 时以手工为准：

```yaml
- id: myworkshop27
  name: My Workshop 2027
  url: https://example.org
  rounds:
    - due: "2027-02-01 19:59"
```

## 接入一个项目仓库

推荐让 AI agent 来做：本仓库带了一个 skill，[skills/connect-lab-dashboard/SKILL.md](skills/connect-lab-dashboard/SKILL.md)，写明了从建仓库、生成 `project.yaml`、校验到推送确认的完整流程。在 Claude Code 里安装一次即可在任何项目中使用：

```bash
mkdir -p ~/.claude/skills/connect-lab-dashboard && curl -fsSL https://raw.githubusercontent.com/FranklinZty/zk-dashboard/main/skills/connect-lab-dashboard/SKILL.md -o ~/.claude/skills/connect-lab-dashboard/SKILL.md
```

之后在项目目录里对 agent 说「把这个项目接入看板」。其他 agent 可以直接读这个文件照做。

手动接入的步骤：

1. 仓库要建在允许名单里的组织或用户下，目前是 `POLYU-ZK-GROUP` 和 `FranklinZty`。
2. 把 `client/project.example.yaml` 复制为仓库根目录的 `project.yaml` 并填写。
3. 把 `client/.github/workflows/lab-sync.yml` 复制到仓库的 `.github/workflows/`。
4. push 一次，在 Actions 页查看 `lab-sync` 是否通过。

不需要设置任何 Secret，也不需要服务器权限。

## 推送协议

`POST /api/v1/ingest`，请求体为 JSON，请求头 `Authorization: Bearer <GitHub Actions OIDC 令牌>`。

- 工作流用 `permissions: id-token: write` 向 GitHub 申请令牌，`audience` 为 `zk-dashboard:` + 请求体的 SHA-256，令牌因此只对这一次推送的内容有效。
- 服务端用 GitHub 公开的公钥验证签名，要求令牌里的 `repository_owner` 在 `LAB_ALLOWED_OWNERS` 名单里，`repository` 和 `sha` 与请求体一致。所以每个仓库只能更新自己的项目。
- 同一 `(project_id, sha)` 重复推送是幂等的。

服务端配置了 `LAB_SYNC_SECRET` 时，也接受旧的共享密钥 HMAC 签名（`X-Lab-Timestamp`、`X-Lab-Signature` 两个请求头），只用于本地开发；线上已停用。实现见 `client/lab_sync/signing.py` 和 `server/oidc.py`。

## 部署

服务器：阿里云 ECS（Alibaba Cloud Linux 3），看板地址 http://120.77.59.95/ 。

更新线上版本，在本机运行（需要已配置好到服务器的 SSH 密钥）：

```bash
deploy/push.sh
```

它只覆盖代码并重启服务，不动数据库和密钥。服务器上的布局：

| 路径 | 内容 |
|---|---|
| `/opt/lab-dashboard` | 代码和 Python 3.11 虚拟环境 |
| `/var/lib/lab-dashboard/lab.db` | 数据库 |
| `/etc/lab-dashboard.env` | 访问密码、允许推送的组织名单等配置，仅 root 可读 |
| `lab-dashboard.service` | systemd 服务，以普通用户 `lab` 运行，开机自启 |

常用命令（在服务器上）：

```bash
systemctl status lab-dashboard
```

```bash
journalctl -u lab-dashboard -n 50
```

看板有访问密码（首次部署时随机生成）。打开看板会先进登录页，输一次密码后这台设备 30 天内不用再输；页面右上角可以退出。修改密码后所有人都要重新登录。脚本访问只读接口时可以直接用 `curl -u lab:密码`。查看当前密码：

```bash
ssh root@120.77.59.95 "grep LAB_VIEW_PASSWORD /etc/lab-dashboard.env"
```

修改密码：

```bash
ssh -t root@120.77.59.95 bash /opt/lab-dashboard/deploy/set-password.sh
```

注意看板目前走 HTTP，密码在网络上不加密，只能挡住随手访问；上域名和 HTTPS 之后才算真正保密。

允许推送的组织或用户（逗号分隔）。新组织的仓库要接入，管理员在这里加上它：

```bash
ssh root@120.77.59.95 "grep LAB_ALLOWED_OWNERS /etc/lab-dashboard.env"
```

```bash
ssh root@120.77.59.95 "sed -i 's/^LAB_ALLOWED_OWNERS=.*/LAB_ALLOWED_OWNERS=POLYU-ZK-GROUP,FranklinZty,新账号/' /etc/lab-dashboard.env && systemctl restart lab-dashboard"
```

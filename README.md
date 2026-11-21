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

1. 把 `client/project.example.yaml` 复制为仓库根目录的 `project.yaml` 并填写。
2. 把 `client/.github/workflows/lab-sync.yml` 复制到仓库的 `.github/workflows/`。
3. 在仓库 Settings → Secrets and variables → Actions 里添加 `LAB_SYNC_URL` 和 `LAB_SYNC_SECRET`。
4. push 一次，在 Actions 页查看 `lab-sync` 是否通过。

## 推送协议

`POST /api/v1/ingest`，请求体为 JSON，请求头：

- `X-Lab-Timestamp`: Unix 秒
- `X-Lab-Signature`: `sha256=` + HMAC-SHA256(密钥, `"<timestamp>." + 请求体`)

服务端拒绝时间戳偏差超过 300 秒的请求；同一 `(project_id, sha)` 重复推送是幂等的。

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
| `/etc/lab-dashboard.env` | 签名密钥等配置，仅 root 可读 |
| `lab-dashboard.service` | systemd 服务，以普通用户 `lab` 运行，开机自启 |

常用命令（在服务器上）：

```bash
systemctl status lab-dashboard
```

```bash
journalctl -u lab-dashboard -n 50
```

查看签名密钥（填到各项目仓库的 `LAB_SYNC_SECRET`）：

```bash
ssh root@120.77.59.95 "grep LAB_SYNC_SECRET /etc/lab-dashboard.env"
```

各项目仓库的 `LAB_SYNC_URL` 填 `http://120.77.59.95/api/v1/ingest`。

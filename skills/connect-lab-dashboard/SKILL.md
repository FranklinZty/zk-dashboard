---
name: connect-lab-dashboard
description: 把一个研究项目（论文仓库）接入课题组看板 zk-dashboard，让它每次 push 后自动把进度推送到看板。会检查或建立 GitHub 仓库、生成并校验 project.yaml、加入 lab-sync 工作流、推送并确认看板收到；不需要任何密钥或服务器权限。当用户说「把这个项目接入看板」「连接 zk-dashboard」「让这个仓库自动推送到看板」「新项目上看板」，或要修改项目在看板上的负责人、阶段、投稿目标时使用。
---

# 把项目接入课题组看板

看板仓库：https://github.com/FranklinZty/zk-dashboard （公开）。
看板地址：http://120.77.59.95/ （有访问密码）。

## 它是怎么工作的

```
项目仓库 push
  └─ .github/workflows/lab-sync.yml（GitHub Actions）
       ├─ 从 zk-dashboard 拉取同步脚本 client/lab_sync/
       ├─ validate.py  校验 project.yaml，并联网核对会议 id
       ├─ collect.py   读取 project.yaml、最近 10 条提交、论文摘要
       └─ push.py      向 GitHub 申请身份令牌，带着它 POST 到看板
看板服务器验证令牌 → 存入 SQLite → 页面展示
```

每个项目仓库只需要两样东西：

| 东西 | 位置 | 内容 |
|---|---|---|
| `project.yaml` | 仓库根目录 | 项目信息，规范见 zk-dashboard 的 `docs/project-yaml.md` |
| `lab-sync.yml` | `.github/workflows/lab-sync.yml` | 原样复制 zk-dashboard 的 `client/.github/workflows/lab-sync.yml`，不用改 |

**不需要设置任何 Secret，也不需要看板服务器的权限。** 推送用 GitHub Actions 签发的身份令牌认证：令牌由 GitHub 签名，写明「来自哪个仓库的哪个提交」，看板据此确认，并且只更新这个仓库自己的项目。

看板只接受**允许名单里的组织或用户**下的仓库，目前是 `POLYU-ZK-GROUP` 和 `FranklinZty`。新项目建议放在组织 `POLYU-ZK-GROUP` 下。仓库在别的账号下时，要请看板管理员把那个账号加进名单（见「出了问题」）。

同步脚本不复制到项目仓库，由工作流每次从 zk-dashboard 拉取。

## 开始前

1. 准备好看板仓库的本地副本，后面的命令用 `$LAB` 指代它：

   ```bash
   LAB=$(mktemp -d)/zk-dashboard && gh repo clone FranklinZty/zk-dashboard "$LAB" -- -q --depth 1
   ```

   本机已经有副本（如 `/Volumes/ZTY/PythonProjects/zk-dashboard`）就直接用，先 `git -C "$LAB" pull -q`。

2. 准备一个装了 PyYAML 的 Python，后面用 `$PY` 指代：

   ```bash
   python3 -m venv "$LAB/.venv" && "$LAB/.venv/bin/pip" install -q pyyaml && PY="$LAB/.venv/bin/python"
   ```

3. 确认 `gh auth status` 已登录，且有权限往项目仓库推送；需要新建仓库时，还要有在目标组织下建仓库的权限。

4. macOS 上 python.org 安装的 Python 可能缺根证书，联网校验会报 `CERTIFICATE_VERIFY_FAILED`。给联网命令加 `SSL_CERT_FILE=/etc/ssl/cert.pem` 即可。

## 步骤

### 1. 确认项目在 GitHub 上

```bash
git -C <项目目录> remote -v
```

**已经是 GitHub 仓库**：记下 `owner/repo` 和当前分支，跳到第 2 步。

记下仓库的 owner：它必须是 `POLYU-ZK-GROUP` 或 `FranklinZty`，否则推送会被看板拒绝。不在名单里就先告诉用户，由用户决定是把仓库转到组织下，还是请管理员加名单。

**不是 git 仓库，或没有远程**：需要新建 GitHub 仓库。这是对外操作，先向用户确认仓库名、所属账号和可见性。默认建在组织 `POLYU-ZK-GROUP` 下、**私有**，名字沿用文件夹名。然后：

1. 先写 `.gitignore`，再 `git init`。研究仓库里常有几个 G 的构建产物，必须排除。参考规则：

   ```
   .DS_Store
   __pycache__/
   *.pyc
   .cache/
   .cra/
   .lake/
   target/
   target-*/
   *-target/
   *.aux
   *.log
   *.out
   *.fls
   *.fdb_latexmk
   *.synctex.gz
   *.blg
   ```

2. `git add -A` 之后、提交之前，检查要进仓库的内容：
   - 总大小，以及最大的几个文件。单个文件超过 50 MB 要停下来问用户；GitHub 拒收超过 100 MB 的文件。
   - 有没有密钥、令牌、私钥（`ghp_`、`github_pat_`、`sk-`、`AKIA`、`BEGIN ... PRIVATE KEY` 等）。发现了就停下来告诉用户，不要提交。
   - 用 `du -sh` 看被排除的大目录确实是构建产物，不是数据。

3. 分两次提交：先提交项目原有内容，再单独提交看板相关的两个文件。这样以后撤掉看板接入很干净。

4. 建仓库并推送第一次提交，看板相关的提交在第 4 步推送：

   ```bash
   gh repo create <owner>/<repo> --private
   git remote add origin https://github.com/<owner>/<repo>.git
   git push origin <第一次提交的完整哈希>:refs/heads/main
   git branch --set-upstream-to=origin/main main
   ```

### 2. 写 project.yaml

先读项目里已有的信息，能自动填的自动填，拿不准的问用户。

| 字段 | 从哪里来 | 说明 |
|---|---|---|
| `title` | 论文 tex 的 `\title{...}`，其次 `STATE.md` 第一行 | 去掉 LaTeX 换行和 `\large` 之类的命令；模板占位符 `<<...>>` 不能用 |
| `owner` | **问用户** | 负责人的 GitHub 用户名。用 `gh api users/<login>` 确认账号存在 |
| `members` | **问用户** | 参与者的 GitHub 用户名列表，不用重复写 owner；可以不填 |
| `stage` | `STATE.md` 的 `Phase:`，按下表换算 | 换算结果要告诉用户，让用户核对 |
| `tags` | 从标题和主题归纳 2–3 个 | 小写字母、数字、连字符 |
| `targets` | `STATE.md` 或 `SUBMISSION.md` 里的目标会议或期刊 | 见下文；拿不准就问用户 |
| `blockers` | `STATE.md` 里明确写着被什么卡住时才填 | 填之前告诉用户 |
| `summary` | 见「项目描述」一节 | 多数情况不需要填 |

**stage 的换算**（课题组研究流程的 P0–P8 阶段）：

| STATE.md 的阶段 | stage |
|---|---|
| P0 立项、P1 文献、P2 选题 | `idea` |
| P3 理论设计 | `design` |
| P4 基线、P5 实验、P6 证伪 | `implementation` |
| P7 写作、P8 审阅与投稿准备 | `writing` |
| 已投出、等结果 | `submitted` |
| 收到审稿意见在改 | `revision` |
| 已录用 | `accepted` |
| 暂停 | `paused` |

没有 `STATE.md` 的项目，直接问用户阶段。

**投稿目标**，每一项二选一：

- **会议**：只写 ccfddl.com 上的会议 id，日期由看板自动同步。

  ```yaml
  targets:
    - venue: ccs27          # 系列名小写 + 两位年份，个别会议是四位年份（如 blocksys2027）
      role: target          # target | backup | submitted
  ```

  确定 id 的办法：在 https://ccfddl.com/conference/allconf.yml 里搜会议名，看它的 `confs[].id`。下一届还没收录时，按往届的格式推出 id（往届是 `crypto26`，就写 `crypto27`），看板会显示「待定」，收录后自动更新。

- **期刊**：写名称和预计投稿日期（北京时间），**日期问用户**，不要自己编。

  ```yaml
  targets:
    - journal: IEEE TDSC
      planned: 2027-03-31
      role: target
  ```

用户说的目标和 `STATE.md` 不一致时，以用户为准。

**已经投出的论文**：`stage: submitted`，所投目标 `role: submitted`。看板会改为显示录用通知日期，优先用目标上的 `notification: YYYY-MM-DD`，没有就用会议数据源里的日期。会议数据源有日期时不用填；期刊或数据源没有时，问用户是否知道通知日期，知道就填上，不知道就不填（看板显示「待定」）。

**项目描述**由 CI 自动决定，按优先级取第一个有内容的：

1. 论文摘要：从 `paper/sections/abstract.tex` 等约定路径提取。摘要在别的位置时，用 `abstract_file: 相对路径.tex` 指定。
2. `STATE.md` 里的 thesis 条目（`- **One-sentence thesis ...:** ...` 或 `- **Working thesis:** ...`）。
3. `project.yaml` 的 `summary`。前两者都没有时，才请用户写一句话；不要替用户编造。

文件开头加上这几行注释，方便以后的人看懂：

```yaml
# 课题组看板的项目信息。规范: https://github.com/FranklinZty/zk-dashboard/blob/main/docs/project-yaml.md
# 日期一律为北京时间 YYYY-MM-DD。每次 push 后由 .github/workflows/lab-sync.yml 同步到看板。
# stage 可选: idea | design | implementation | writing | submitted | revision | accepted | paused
schema: 1
```

### 3. 校验，并预览看板会收到什么

在项目目录里运行：

```bash
"$PY" "$LAB/client/lab_sync/validate.py" project.yaml --check-venues
"$PY" "$LAB/client/lab_sync/collect.py" project.yaml -o /tmp/lab-payload.json
```

- 「校验通过」才能继续。报错就改 `project.yaml`。
- 「待定 — ccfddl.com 尚未收录」是警告，可以接受。
- 「未找到会议」是错误，多半是 id 拼错。
- `collect.py` 会说明描述取自哪个文件。如果它说「请在 project.yaml 里填写 summary」，回到第 2 步请用户补一句话。

然后复制工作流：

```bash
mkdir -p .github/workflows && cp "$LAB/client/.github/workflows/lab-sync.yml" .github/workflows/
```

**只提交这两个文件**。仓库里可能有用户自己没提交的改动，不要顺带提交：

```bash
git add project.yaml .github/workflows/lab-sync.yml
git commit -m "Add lab dashboard sync (project.yaml + lab-sync workflow)" -- project.yaml .github/workflows/lab-sync.yml
```

### 4. 推送并确认

```bash
git push origin <当前分支>
```

工作流在任何分支的 push 上都会触发。等这次提交对应的运行结束：

```bash
gh run list -R <owner>/<repo> --workflow lab-sync -L 1
gh run watch <run-id> -R <owner>/<repo> --exit-status
```

成功的标志是运行日志里「推送到看板」一步打印 `推送成功 (200，GitHub 身份认证)`：

```bash
gh run view <run-id> -R <owner>/<repo> --log | grep -E "推送成功|看板拒绝|暂时不可达"
```

从 push 到完成通常 15–30 秒。最后告诉用户：项目已接入、看板上会显示的标题、阶段、投稿目标和描述来源，以及看板地址。

## 出了问题

| 现象 | 原因和处理 |
|---|---|
| 「校验 project.yaml」一步失败 | 看 Annotations 里的具体字段，改好再推 |
| 报 `无法认证：工作流缺少 permissions: id-token: write` | 工作流文件不是最新版，或被改过。重新从 zk-dashboard 复制 |
| 报 `看板拒绝了推送 (401)` 且提到「不在看板允许的组织名单里」 | 仓库的 owner 不在名单里。请用户把仓库转到 `POLYU-ZK-GROUP`，或请看板管理员运行下面的命令把账号加进名单 |
| 报 `看板拒绝了推送 (401)`，提到「需要 GitHub 身份令牌」 | 用的是旧版工作流（共享密钥方式已停用）。重新从 zk-dashboard 复制工作流 |
| 报 `看板拒绝了推送 (403)` | 令牌里的仓库或提交与推送内容不一致，通常是改动了同步脚本。用原版工作流 |
| 报 `看板拒绝了推送 (400)` | 数据格式问题，日志里有原因 |
| 警告「看板暂时不可达」，但 CI 是绿的 | 服务器停机。这是故意设计的：每天北京时间 03:17 会在默认分支上自动重推补齐 |
| 「获取同步脚本」失败 | zk-dashboard 仓库不可访问，确认它仍是公开仓库 |
| 看板上没有出现项目 | 先确认 CI 日志里有 `推送成功`；再确认访问看板时用的是 `http://` 而不是 `https://` |

看板管理员把账号加进允许名单（需要服务器权限，只有管理员能做；名单用逗号分隔，保留已有的）：

```bash
ssh root@120.77.59.95 "sed -i 's/^LAB_ALLOWED_OWNERS=.*/LAB_ALLOWED_OWNERS=POLYU-ZK-GROUP,FranklinZty,新账号/' /etc/lab-dashboard.env && systemctl restart lab-dashboard"
```

## 以后改项目信息

只改 `project.yaml`，提交并 push，看板随之更新，不需要再动工作流。改之前同样跑一遍第 3 步的校验。

常见的改动：

- **论文投出去了**：`stage` 改为 `submitted`，所投目标的 `role` 改为 `submitted`，可选填 `notification`。
- **收到审稿意见在改**：`stage` 改为 `revision`。
- **录用了**：`stage` 改为 `accepted`。
- **被拒后改投**：`stage` 改回 `writing` 或 `revision`，原目标删掉或改为 `backup`，新目标 `role: target`。

## 不要做的事

- 不要把看板访问密码写进任何文件或提交信息。
- 不要给项目仓库添加任何与看板相关的 Secret，现在不需要。
- 不要修改 `lab-sync.yml` 的内容；需要改工作流时改 zk-dashboard 里的版本，再同步到各项目仓库。
- 不要提交用户未提交的其他改动，不要切换用户的分支。
- 未经用户确认，不要新建 GitHub 仓库，不要把私有仓库改为公开。
- 投稿日期、负责人、成员不要猜，问用户。

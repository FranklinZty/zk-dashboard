# project.yaml 规范（schema 1）

每个项目仓库的根目录放一个 `project.yaml`。CI 在每次 push 时校验它，并把内容连同最近提交一起推送到看板。

项目 id 不写在文件里，固定取仓库名（`owner/my-paper` 的 id 是 `my-paper`）。

## 完整示例

见 [client/project.example.yaml](../client/project.example.yaml)。

## 字段

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `schema` | 是 | 整数 | 固定为 `1` |
| `title` | 是 | 字符串 | 项目标题，1–120 字符 |
| `owner` | 是 | 字符串 | 负责人的 GitHub 用户名 |
| `stage` | 是 | 枚举 | 见下方「阶段」 |
| `members` | 否 | 字符串列表 | 参与者的 GitHub 用户名，不必重复写 owner |
| `tags` | 否 | 字符串列表 | 供筛选，小写字母、数字、连字符 |
| `summary` | 否 | 字符串 | 一句话说明，最多 300 字符。论文没有摘要、STATE.md 里也没有 thesis 时才需要填 |
| `abstract_file` | 否 | 字符串 | 摘要所在的 .tex 文件，相对仓库根目录。不填则按约定路径自动查找 |
| `milestones` | 否 | 列表 | 见下方 |
| `blockers` | 否 | 列表 | 见下方。列表非空即视为「阻塞」 |
| `targets` | 否 | 列表 | 投稿目标，见下方 |

出现未列出的字段会校验失败，防止拼写错误被悄悄忽略。

## 项目描述的来源

看板上每个项目的描述按下面的顺序取，用第一个有内容的：

1. **论文摘要**：CI 从 tex 源文件里提取，论文改了看板跟着变。
2. **STATE.md 的 thesis**：仓库根目录 `STATE.md` 里形如 `- **One-sentence thesis ...:** ...` 或 `- **Working thesis:** ...` 的条目。
3. **`summary`**：前两者都没有时，由作者在 `project.yaml` 里填写。三者都没有，CI 会给出警告。

摘要的查找顺序：

1. `abstract_file` 指定的文件。
2. 约定路径，取第一个存在且含有效摘要的：`paper/sections/abstract.tex`、`paper/abstract.tex`、`sections/abstract.tex`、`abstract.tex`、`paper/main.tex`、`main.tex`。

文件里有 `\begin{abstract}...\end{abstract}` 就取其中的内容；没有该环境但文件名是 `abstract.tex` 则取整个文件。LaTeX 命令会被转成纯文本，引用和脚注被去掉。

还是模板占位符（含 `<<...>>`）或不足 80 字符的内容不算摘要，会继续往下一级来源找。

## 阶段（stage）

| 值 | 含义 |
|---|---|
| `idea` | 选题、调研 |
| `design` | 方案与理论设计 |
| `implementation` | 实现与实验 |
| `writing` | 写作 |
| `submitted` | 已投稿，等待结果 |
| `revision` | 修改、rebuttal、重投 |
| `accepted` | 已录用 |
| `paused` | 暂停 |

`submitted`、`accepted`、`paused` 三个阶段不计入停滞。

## 里程碑（milestones）

```yaml
milestones:
  - title: 完成基线复现
    status: done          # todo | doing | done
    due: 2026-12-15       # 可选，北京时间日期
```

## 阻塞项（blockers）

```yaml
blockers:
  - text: 等待 GPU 配额
    since: 2026-11-10     # 可选，北京时间日期
    owner: some-login     # 可选，谁来解决
```

## 投稿目标（targets）

每一项要么是会议，要么是期刊，二选一。

**会议**：只写 ccfddl.com 的会议 id，日期由看板自动同步，属于 firm 截稿。

```yaml
targets:
  - venue: eurocrypt27    # ccfddl.com 上的 id
    role: target          # target | backup | submitted
```

**期刊**：写名称和预计投稿日期，日期只对本项目有效。

```yaml
targets:
  - journal: IEEE TIFS
    planned: 2027-03-15   # 预计投稿日期，北京时间
    role: target
```

**投出之后**：把 `stage` 改成 `submitted`，所投目标的 `role` 改成 `submitted`。看板就不再显示投稿截止，改为显示**录用通知日期**。

```yaml
stage: submitted
targets:
  - venue: eurocrypt27
    role: submitted
    notification: 2027-01-18   # 可选，录用通知日期（北京时间）
```

录用通知日期的来源：

1. `notification`：作者自己填的，优先使用。投稿系统里看到的日期可以填在这里。
2. 会议的数据源（ccfddl.com）里所投那一轮的通知日期。多轮截稿时，按截稿已过的最后一轮算。
3. 都没有时显示「录用通知：待定」。

期刊没有统一的通知日期，需要的话自己填 `notification`。只改了 `stage` 没改 `role` 时，看板把 `role: target` 的目标当作已投出。

规则：

- 日期一律是北京时间（UTC+8）的 `YYYY-MM-DD`。
- 会议条目不能写日期，期刊条目必须写 `planned`。
- `role: submitted` 的期刊条目，`planned` 填实际投稿日期。
- 同一个会议或期刊在一个项目里只能出现一次。

## 本地校验

```bash
python client/lab_sync/validate.py project.yaml
```

加 `--check-venues` 会联网到 ccfddl.com 核对会议 id：

- id 存在：通过。
- id 不存在，但同系列的往届存在（如写了 `crypto27`，目前只有 `crypto26`）：只给警告，标注「待定」。下一届还没被收录，看板在日期公布前也显示「待定」。
- 系列也对不上：报错，标注「未找到会议」，多半是拼写错误。

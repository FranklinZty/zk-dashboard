"""取项目的文字描述。只依赖标准库。

来源按优先级（describe() 实现这个顺序）:
    1. 论文摘要：从 LaTeX 源文件提取并转成纯文本
    2. STATE.md 里的 thesis 一行
    3. 都没有时返回空，由看板改用 project.yaml 里作者填写的 summary

摘要的查找顺序:
    1. project.yaml 里 abstract_file 指定的文件
    2. 约定路径（见 DEFAULT_FILES），取第一个存在且含有效摘要的

文件里有 \\begin{abstract}...\\end{abstract} 就取环境内的内容；
没有该环境但文件名是 abstract.tex，则取整个文件。
模板占位符（<<...>>）或过短的内容不算摘要。
"""
from __future__ import annotations

import os
import re

DEFAULT_FILES = (
    "paper/sections/abstract.tex", "paper/abstract.tex", "sections/abstract.tex",
    "abstract.tex", "paper/main.tex", "main.tex",
)
MIN_CHARS, MAX_CHARS = 80, 4000
STATE_FILE = "STATE.md"
STATE_MIN_CHARS, STATE_MAX_CHARS = 40, 1500
# "- **One-sentence thesis (hypothesis):** ..." / "- **Working thesis:** ..."
_THESIS_RE = re.compile(r"^- \*\*[^*\n]*thesis[^*\n]*:\*\*[ \t]*(.*)$", re.I)

SYMBOLS = {
    "to": "→", "rightarrow": "→", "times": "×", "ge": "≥", "geq": "≥", "le": "≤", "leq": "≤",
    "approx": "≈", "neq": "≠", "cdot": "·", "log": "log", "sqrt": "√", "infty": "∞",
    "ldots": "…", "dots": "…", "in": "∈", "pm": "±", "ll": "≪", "gg": "≫",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "lambda": "λ", "mu": "μ", "pi": "π", "sigma": "σ", "tau": "τ", "phi": "φ", "omega": "ω", "ell": "ℓ",
    "Theta": "Θ", "Omega": "Ω", "Sigma": "Σ", "Pi": "Π", "Delta": "Δ", "Lambda": "Λ",
}
DROP_WITH_ARGS = {   # 命令名 -> 要一并丢弃的 {} 参数个数
    "cite": 1, "citep": 1, "citet": 1, "ref": 1, "cref": 1, "Cref": 1, "eqref": 1, "label": 1,
    "footnote": 1, "keywords": 1, "evid": 1, "setlength": 2, "vspace": 1, "hspace": 1,
    "todo": 1, "index": 1,
    "DIFdel": 1, "DIFdelFL": 1,   # latexdiff 标记的删除文本；\DIFadd{...} 保留内容
}
# latexdiff 把删除的片段（含公式）包在 \DIFdelbegin ... \DIFdelend 之间
_DIFDEL_BLOCK_RE = re.compile(r"\\DIFdelbegin(?:FL)?\b.*?\\DIFdelend(?:FL)?\b", re.S)
_DEF_RE = re.compile(
    r"\\(?:re|provide)?newcommand\*?\s*\{?\\([A-Za-z]+)\}?\s*(\[\d\])?\s*\{"
    r"|\\DeclareRobustCommand\*?\s*\{?\\([A-Za-z]+)\}?\s*(\[\d\])?\s*\{"
    r"|\\def\s*\\([A-Za-z]+)\s*\{")


def _strip_comments(text):
    return re.sub(r"(?<!\\)%.*", "", text)


def _balanced(text, start):
    """text[start] 是 '{'，返回 (花括号内的内容, 右花括号之后的位置)；不配对返回 None。"""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{" and (i == 0 or text[i - 1] != "\\"):
            depth += 1
        elif text[i] == "}" and text[i - 1] != "\\":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
    return None


def _drop_commands(text):
    """丢掉引用、脚注、关键词等命令连同它们的参数。"""
    out, i = [], 0
    pattern = re.compile(r"\\([A-Za-z]+)\*?")
    while True:
        m = pattern.search(text, i)
        if not m:
            out.append(text[i:])
            return "".join(out)
        nargs = DROP_WITH_ARGS.get(m.group(1))
        if nargs is None:
            out.append(text[i:m.end()])
            i = m.end()
            continue
        out.append(text[i:m.start()])
        pos = m.end()
        for _ in range(nargs):
            while True:   # 跳过可选参数 [...]
                opt = re.match(r"\s*\[[^\]]*\]", text[pos:])
                if not opt:
                    break
                pos += opt.end()
            brace = re.match(r"\s*\{", text[pos:])
            got = _balanced(text, pos + brace.end() - 1) if brace else None
            if not got:
                break
            pos = got[1]
        i = pos


def find_macros(tex_sources):
    """收集无参数的自定义命令: {'vicuna': '\\textsf{VICUNA}\\xspace'}。"""
    macros = {}
    for src in tex_sources:
        src = _strip_comments(src)
        for m in _DEF_RE.finditer(src):
            name = m.group(1) or m.group(3) or m.group(5)
            if m.group(2) or m.group(4):   # 带参数的命令不展开
                continue
            got = _balanced(src, m.end() - 1)
            if got and len(got[0]) <= 200:
                macros.setdefault(name, got[0])
    return macros


def to_plain(tex, macros=None):
    """LaTeX 片段 -> 纯文本。段落用空行分隔。"""
    text = _DIFDEL_BLOCK_RE.sub("", _strip_comments(tex))
    macros = macros or {}
    for _ in range(3):   # 自定义命令可能嵌套
        text, n = re.subn(r"\\([A-Za-z]+)(\{\})?",
                          lambda m: macros[m.group(1)] if m.group(1) in macros else m.group(0), text)
        if not n:
            break
    text = _drop_commands(text)
    text = re.sub(r"\\(?:begin|end)\{[^}]*\}", "", text)
    text = re.sub(r"\\([A-Za-z]+)(?![A-Za-z])",
                  lambda m: SYMBOLS.get(m.group(1), m.group(0)), text)
    for src, dst in (("\\\\", " "), ("\\,", ""), ("\\;", " "), ("\\:", " "), ("\\!", ""), ("\\ ", " "),
                     ("~", " "), ("---", "—"), ("--", "–"), ("``", "“"), ("''", "”")):
        text = text.replace(src, dst)
    text = re.sub(r"\\([#%&_$])", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\*?", "", text)          # 其余命令只留参数内容
    text = re.sub(r"(?<!\\)[{}$]", "", text).replace("\\{", "{").replace("\\}", "}")
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    text = "\n\n".join(p for p in paragraphs if p)
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    # 引用被去掉后留下的 "; see)" 和空括号
    text = re.sub(r"[;,]?\s*\b(?:see|cf\.?)\s*(?=[).,;])", "", text)
    return re.sub(r"\s*\(\s*\)", "", text).replace("\\", "")


def _body(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", src, re.S)
    if m:
        return m.group(1)
    return src if os.path.basename(path) == "abstract.tex" else None


def _macro_sources(repo_dir, tex_path):
    """摘要所在目录及其上两级目录里的 .tex/.sty，用来找自定义命令。"""
    sources, folder = [], os.path.dirname(os.path.abspath(tex_path))
    root = os.path.abspath(repo_dir)
    for _ in range(3):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            break
        for name in names:
            if name.endswith((".tex", ".sty", ".cls")):
                try:
                    with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
                        sources.append(f.read(400_000))
                except OSError:
                    pass
        if folder == root:
            break
        folder = os.path.dirname(folder)
    return sources


def extract(repo_dir=".", abstract_file=None):
    """返回 (摘要纯文本, 相对路径)；找不到有效摘要返回 (None, None)。"""
    for rel in ([abstract_file] if abstract_file else DEFAULT_FILES):
        path = os.path.join(repo_dir, rel)
        if not os.path.isfile(path):
            continue
        body = _body(path)
        if body is None or "<<" in body:      # <<...>> 是模板占位符
            continue
        text = to_plain(body, find_macros(_macro_sources(repo_dir, path)))
        if len(text) >= MIN_CHARS:
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS].rstrip() + "…"
            return text, rel
    return None, None


def from_state(repo_dir="."):
    """STATE.md 里的 thesis 条目（含缩进的续行），去掉 Markdown 标记。没有返回 (None, None)。"""
    path = os.path.join(repo_dir, STATE_FILE)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None, None
    for i, line in enumerate(lines):
        m = _THESIS_RE.match(line)
        if not m:
            continue
        parts = [m.group(1)]
        for cont in lines[i + 1:]:
            if not cont.startswith((" ", "\t")) or not cont.strip():
                break                       # 续行必须缩进；空行或下一个条目就结束
            parts.append(cont.strip())
        text = " ".join(p for p in parts if p)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # [文字](链接) -> 文字
        text = re.sub(r"[*`]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= STATE_MIN_CHARS:
            if len(text) > STATE_MAX_CHARS:
                text = text[:STATE_MAX_CHARS].rstrip() + "…"
            return text, STATE_FILE
    return None, None


def describe(repo_dir=".", abstract_file=None):
    """按优先级取描述: 论文摘要 -> STATE.md 的 thesis。返回 (文本, 来源路径) 或 (None, None)。"""
    text, source = extract(repo_dir, abstract_file)
    if text:
        return text, source
    return from_state(repo_dir)

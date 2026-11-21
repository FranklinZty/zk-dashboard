// 课题组看板前端：只读，无构建。三个页面按路径分发，筛选状态只存在于 URL。
'use strict';

const STAGES = { idea: '选题', design: '设计', implementation: '实现', writing: '写作',
  submitted: '已投稿', revision: '修改', accepted: '已录用', paused: '暂停' };
const ROLES = { target: '目标', backup: '备选', submitted: '已投' };
const MS_STATUS = { todo: '未开始', doing: '进行中', done: '已完成' };
const DAY = 86400000;
const app = document.getElementById('app');

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const safeUrl = u => (/^https?:\/\//i.test(u || '') ? esc(u) : '');

async function api(path) {
  const r = await fetch('/api/v1' + path);
  if (!r.ok) throw new Error(r.status === 404 ? '没有找到' : `请求失败 (${r.status})`);
  return r.json();
}

// ---- 时间：一律按北京时间显示，不跟随浏览器时区 ----
const partsOf = (iso, opts) => Object.fromEntries(
  new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, ...opts })
    .formatToParts(new Date(iso)).map(p => [p.type, p.value]));
const thisYear = () => partsOf(Date.now(), { year: 'numeric' }).year;

function fmtDateTime(iso) {
  const p = partsOf(iso, { year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  return `${p.year === thisYear() ? '' : p.year + '年'}${p.month}月${p.day}日 ${p.hour}:${p.minute}`;
}
function fmtDate(ymd) {
  const [y, m, d] = String(ymd).slice(0, 10).split('-').map(Number);
  return `${String(y) === thisYear() ? '' : y + '年'}${m}月${d}日`;
}
// 剩余时间用浏览器本地时钟计算
function leftText(iso) {
  const ms = new Date(iso) - Date.now();
  if (ms < 0) return '已过';
  if (ms < 3600000) return '不足 1 小时';
  if (ms < DAY) return `还剩 ${Math.floor(ms / 3600000)} 小时`;
  return `还剩 ${Math.floor(ms / DAY)} 天`;
}
const isUrgent = iso => new Date(iso) - Date.now() < 7 * DAY;
const agoText = d => (d === 0 ? '今天提交' : `${d} 天前提交`);

// ---- 投稿目标的统一显示：会议 firm 醒目，期刊浅色，未收录显示待定 ----
function targetName(t) {
  const url = safeUrl(t.url);
  const name = url ? `<a href="${url}" target="_blank" rel="noopener">${esc(t.name)}</a>` : esc(t.name);
  return (t.firm ? '<span class="pill firm">firm</span> ' : '') + name;
}
function targetWhen(t) {
  if (t.status === 'tbd') return '<span class="muted">待定</span>';
  if (!t.due) return '<span class="muted">—</span>';
  if (!t.firm) {
    const label = t.status === 'submitted' ? '投稿于' : t.status === 'past' ? '计划已过' : '预计';
    return `<span class="soft">${label} ${fmtDate(t.due)}</span>`;
  }
  if (t.status === 'submitted') return `<span class="muted">${fmtDateTime(t.due)}</span>`;
  if (t.status === 'past') return `<span class="muted">${fmtDateTime(t.due)} · 已过</span>`;
  return `<span class="${isUrgent(t.due) ? 'c-bad' : ''}">${fmtDateTime(t.due)} · ${leftText(t.due)}</span>`;
}

// ---- 总览 ----
const FILTERS = ['owner', 'member', 'stage', 'tag', 'due', 'stall'];

function filterBar(facets, q) {
  const sel = (key, label, options) => {
    const cur = q.get(key) || '';
    const opts = options.map(([v, text]) =>
      `<option value="${esc(v)}"${String(v) === cur ? ' selected' : ''}>${esc(text)}</option>`).join('');
    return `<select data-filter="${key}" class="${cur ? 'set' : ''}" aria-label="${label}">
      <option value="">${label}：全部</option>${opts}</select>`;
  };
  const same = list => list.map(v => [v, v]);
  return `<div class="filters">
    ${sel('owner', '负责人', same(facets.owners))}
    ${sel('member', '成员', same(facets.members))}
    ${sel('stage', '阶段', facets.stages.map(s => [s, STAGES[s] || s]))}
    ${sel('tag', '标签', same(facets.tags))}
    ${sel('due', '截稿', [[7, '7 天内'], [14, '14 天内'], [30, '30 天内']])}
    ${sel('stall', '进度', [[1, '放缓或停滞'], [2, '仅停滞']])}
    ${FILTERS.some(k => q.get(k)) ? '<button data-clear>清除筛选</button>' : ''}
  </div>`;
}

function projectCard(p) {
  const exempt = ['submitted', 'accepted', 'paused'].includes(p.stage);
  const t = p.next_target;
  const people = esc(p.owner) + (p.members.length ? ` +${p.members.length}` : '');
  return `<a class="card" href="/p/${encodeURIComponent(p.id)}">
    <div class="card-head">
      <span class="card-title">${esc(p.title)}</span>
      <span class="pill stage">${esc(STAGES[p.stage] || p.stage)}</span>
    </div>
    <div class="meta">
      <span>${people}</span>
      <span><span class="dot ${exempt ? 'sx' : 's' + p.stall_level}"></span>${agoText(p.days_since_commit)}</span>
      ${p.blocker_count ? `<span class="c-warn">${p.blocker_count} 个阻塞</span>` : ''}
      ${p.tags.length ? `<span>${p.tags.map(x => '#' + esc(x)).join(' ')}</span>` : ''}
    </div>
    ${t ? `<div class="target${t.firm ? '' : ' soft'}">
      <span>${(t.firm ? '<span class="pill firm">firm</span> ' : '') + esc(t.name)}</span>${targetWhen(t)}</div>` : ''}
  </a>`;
}

function sidePanel(deadlines) {
  const soon = deadlines.filter(d => d.status === 'upcoming' && d.days_left <= 14);
  const items = soon.map(d => `<div class="side-item">
      <div class="row"><span class="${d.firm ? '' : 'soft'}">${esc(d.name)}</span>
        ${d.firm ? '<span class="pill firm">firm</span>' : ''}</div>
      <div>${targetWhen(d)}</div>
      <div class="muted">${d.projects.map(p =>
        `<a href="/p/${encodeURIComponent(p.id)}">${esc(p.title)}</a>`).join('、')}</div>
    </div>`).join('');
  return `<aside class="card side"><h2>14 天内</h2>
    ${items || '<p class="muted small">14 天内没有投稿日期。</p>'}
    <p class="small"><a href="/deadlines">查看全部投稿日程</a></p></aside>`;
}

async function renderHome() {
  const q = new URLSearchParams(location.search);
  const params = new URLSearchParams(FILTERS.filter(k => q.get(k)).map(k => [k, q.get(k)]));
  const [data, dl] = await Promise.all([api('/projects?' + params), api('/deadlines')]);
  const s = data.summary;
  const filtered = [...params].length > 0;
  app.innerHTML = `
    <div class="summary">
      <div class="metric"><span class="muted small">${filtered ? '筛选出的项目' : '项目'}</span><b>${s.total}</b></div>
      <div class="metric ok"><span class="muted small">活跃（7 天内有提交）</span><b>${s.active}</b></div>
      <div class="metric warn"><span class="muted small">有阻塞</span><b>${s.blocked}</b></div>
      <div class="metric bad"><span class="muted small">停滞（超过 14 天）</span><b>${s.stalled}</b></div>
    </div>
    ${filterBar(data.facets, q)}
    <div class="layout">
      <div class="cards">${data.projects.map(projectCard).join('') ||
        `<div class="empty">${filtered ? '没有符合筛选条件的项目。' : '还没有项目推送进来。'}</div>`}</div>
      ${sidePanel(dl.deadlines)}
    </div>`;

  const go = next => { history.replaceState(null, '', next.toString() ? '?' + next : location.pathname); renderHome().catch(showError); };
  app.querySelectorAll('[data-filter]').forEach(el => el.addEventListener('change', () => {
    const next = new URLSearchParams(location.search);
    el.value ? next.set(el.dataset.filter, el.value) : next.delete(el.dataset.filter);
    go(next);
  }));
  app.querySelector('[data-clear]')?.addEventListener('click', () => go(new URLSearchParams()));
}

// ---- 投稿日程 ----
async function renderDeadlines() {
  const { deadlines } = await api('/deadlines');
  const groups = [
    ['7 天内', d => d.status === 'upcoming' && d.days_left <= 7],
    ['30 天内', d => d.status === 'upcoming' && d.days_left > 7 && d.days_left <= 30],
    ['更晚', d => d.status === 'upcoming' && d.days_left > 30],
    ['待定', d => d.status === 'tbd'],
    ['已过', d => d.status === 'past'],
  ];
  const table = rows => `<table><thead><tr><th>会议 / 期刊</th><th>日期</th><th>项目</th></tr></thead><tbody>
    ${rows.map(d => `<tr>
      <td>${targetName(d)}${d.comment ? `<div class="muted small">${esc(d.comment)}</div>` : ''}</td>
      <td class="nowrap">${targetWhen(d)}${d.firm && d.abstract_due && d.abstract_due !== d.due
        ? `<div class="muted small">摘要截稿 ${fmtDateTime(d.abstract_due)}</div>` : ''}</td>
      <td>${d.projects.map(p => `<a href="/p/${encodeURIComponent(p.id)}">${esc(p.title)}</a>
        <span class="pill role">${esc(ROLES[p.role] || p.role)}</span>`).join('<br>')}</td>
    </tr>`).join('')}</tbody></table>`;
  const sections = groups.map(([title, test]) => {
    const rows = deadlines.filter(test);
    if (title === '已过') rows.reverse();
    return rows.length ? `<h2>${title}（${rows.length}）</h2>${table(rows)}` : '';
  }).join('');
  app.innerHTML = `<h1>投稿日程</h1>
    <p class="muted small">会议日期来自 ccfddl.com 并已换算为北京时间，标 firm 的是硬截稿；期刊为各项目自定的预计投稿日期。投稿前请以会议官网为准。</p>
    ${sections || '<div class="empty">还没有项目填写投稿目标。</div>'}`;
}

// ---- 项目详情 ----
async function renderProject(id) {
  const { project: p } = await api('/projects/' + encodeURIComponent(id));
  const today = partsOf(Date.now(), { year: 'numeric', month: '2-digit', day: '2-digit' });
  const todayYmd = `${today.year}-${today.month}-${today.day}`;
  const repoUrl = 'https://github.com/' + p.repo.split('/').map(encodeURIComponent).join('/');
  const exempt = ['submitted', 'accepted', 'paused'].includes(p.stage);
  const list = (rows, head, empty) => rows.length
    ? `<table><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`
    : `<p class="muted">${empty}</p>`;

  app.innerHTML = `
    <p class="small"><a href="/">← 返回总览</a></p>
    <h1>${esc(p.title)} <span class="pill stage">${esc(STAGES[p.stage] || p.stage)}</span></h1>
    ${p.summary ? `<p class="muted">${esc(p.summary)}</p>` : ''}
    <div class="card"><dl class="kv">
      <dt>负责人</dt><dd>${esc(p.owner)}</dd>
      <dt>成员</dt><dd>${p.members.map(esc).join('、') || '—'}</dd>
      <dt>标签</dt><dd>${p.tags.map(x => '#' + esc(x)).join(' ') || '—'}</dd>
      <dt>仓库</dt><dd><a href="${esc(repoUrl)}" target="_blank" rel="noopener">${esc(p.repo)}</a></dd>
      <dt>最近提交</dt><dd><span class="dot ${exempt ? 'sx' : 's' + p.stall_level}"></span>${agoText(p.days_since_commit)}（${fmtDateTime(p.last_commit_at)}）</dd>
    </dl></div>

    <h2>投稿目标</h2>
    ${list(p.targets.map(t => `<tr><td>${targetName(t)}</td><td class="nowrap">${targetWhen(t)}</td>
      <td><span class="pill role">${esc(ROLES[t.role] || t.role)}</span></td></tr>`),
      ['会议 / 期刊', '日期', '角色'], '还没有填写投稿目标。')}

    <h2>阻塞项</h2>
    ${list(p.blockers.map(b => `<tr><td class="c-warn">${esc(b.text)}</td>
      <td class="nowrap">${b.since ? '自 ' + fmtDate(b.since) : '—'}</td><td>${esc(b.owner || '—')}</td></tr>`),
      ['内容', '开始', '负责解决'], '没有阻塞项。')}

    <h2>里程碑</h2>
    ${list(p.milestones.map(m => {
      const overdue = m.due && m.status !== 'done' && m.due < todayYmd;
      return `<tr><td>${esc(m.title)}</td>
        <td class="${m.status === 'done' ? 'c-ok' : ''}">${esc(MS_STATUS[m.status] || m.status)}</td>
        <td class="nowrap ${overdue ? 'c-bad' : ''}">${m.due ? fmtDate(m.due) + (overdue ? ' · 已过期' : '') : '—'}</td></tr>`;
    }), ['标题', '状态', '计划日期'], '还没有填写里程碑。')}

    <h2>最近提交</h2>
    ${list(p.commits.map(c => `<tr><td class="nowrap">${fmtDateTime(c.date)}</td><td>${esc(c.author)}</td>
      <td>${esc(c.message)} <code>${esc(String(c.sha).slice(0, 7))}</code></td></tr>`),
      ['时间', '作者', '说明'], '没有提交记录。')}`;
  document.title = `${p.title} · 课题组看板`;
}

// ---- 分发 ----
function showError(e) {
  app.innerHTML = `<div class="empty">${esc(e.message || e)}。<a href="/">返回总览</a></div>`;
}
(function main() {
  const path = location.pathname;
  const m = path.match(/^\/p\/([^/]+)\/?$/);
  const page = m ? 'project' : path.startsWith('/deadlines') ? 'deadlines' : 'home';
  document.querySelector(`[data-nav="${page}"]`)?.classList.add('on');
  (m ? renderProject(decodeURIComponent(m[1])) : page === 'deadlines' ? renderDeadlines() : renderHome())
    .catch(showError);
})();

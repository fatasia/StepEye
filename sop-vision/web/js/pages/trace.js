import { api } from "../api.js";
import { gantt } from "../charts.js";
import { icon } from "../icons.js";
import { store } from "../store.js";
import { emptyBlock, errorBlock, openDrawer, openLightbox, resultBadge, toast, violationTag } from "../ui.js";
import { $, copyText, debounce, durHtml, esc, fmtTime, setHTML } from "../util.js";

const RESULTS = [["", "全部"], ["PASS", "合格"], ["FAIL", "不合格"], ["UNCERTAIN", "待复核"]];
const FRAME_TAG = {
  cycle_start: "周期开始", step_done: "步骤完成", cycle_end: "周期结束",
  violation_missing_step: "漏步", violation_wrong_order: "错序", violation_step_timeout: "步骤超时", violation_cycle_overtime: "周期超节拍",
};
const END_REASON = { end: "工件离开", max_cycle: "超过最长周期" };

const stationName = (id) => store.metas.find((m) => m.id === id)?.name ?? id;

function tableHtml(rows) {
  if (!rows.length) return emptyBlock("没有符合条件的周期", "调整筛选条件，或等待工位完成新的周期。");
  const body = rows.map((r) => {
    const takt = r.takt_s;
    const ratio = takt ? Math.min(r.duration_s / takt, 1.5) / 1.5 : 0;
    const vs = (r.violations || []).map((v) => violationTag(v.kind)).join("") || '<span class="faint">—</span>';
    return `<tr tabindex="0" data-id="${esc(r.cycle_id)}">
      <td class="num nowrap">${fmtTime(r.start_wall)}</td>
      <td class="ellipsis" title="${esc(r.station)}">${esc(stationName(r.station))}</td>
      <td class="mono" title="${esc(r.cycle_id)}">${esc(r.cycle_id.split("-").slice(-1)[0])}</td>
      <td class="mono">${r.sn ? esc(r.sn) : '<span class="faint">—</span>'}</td>
      <td>${resultBadge(r.result)}</td>
      <td class="num right"><span class="dur-cell">${durHtml(r.duration_s)}${takt ? `<span class="mini-bar ${r.duration_s > takt ? "over" : ""}"><i style="width:${(ratio * 100).toFixed(0)}%"></i></span>` : ""}</span></td>
      <td><div class="tags">${vs}</div></td></tr>`;
  }).join("");
  return `<table class="table"><thead><tr><th>开始时间</th><th>工位</th><th>周期号</th><th>序列号</th><th>结果</th><th class="right">耗时</th><th>违规</th></tr></thead><tbody>${body}</tbody></table>`;
}

const hashRow = (label, value) => value
  ? `<div class="kv"><span class="muted">${label}</span><span class="mono ellipsis" title="${esc(value)}">${esc(value.slice(0, 12))}…</span><button class="icon-btn" data-copy="${esc(value)}" aria-label="复制${label}">${icon("copy", 14)}</button></div>`
  : "";

function detailHtml(r) {
  const frames = (r.frames ?? []).map((f) => {
    const name = f.file.split(/[\\/]/).pop();
    const cap = `${FRAME_TAG[f.tag] ?? f.tag}${f.step ? ` · ${f.step}` : ""}`;
    return `<button class="thumb" data-src="${esc(api.frame(r.cycle_id, name))}" data-cap="${esc(cap)}"><img src="${esc(api.frame(r.cycle_id, name))}" alt="${esc(cap)}" loading="lazy"><span>${esc(cap)}</span></button>`;
  }).join("");
  const viol = (r.violations || []).length
    ? r.violations.map((v) => `<div class="viol-item">${violationTag(v.kind)}<span class="ellipsis" title="${esc(v.message)}">${esc(v.message)}</span><span class="faint num">${v.ts.toFixed(1)} s</span></div>`).join("")
    : `<div class="inline-empty muted">${icon("check", 14)}本周期无违规</div>`;
  return `
    <div class="d-head">${resultBadge(r.result)}<span class="muted">${esc(stationName(r.station))}</span></div>
    <div class="kv-grid">
      <div class="kv"><span class="muted">开始</span><span class="num">${fmtTime(r.start_wall)}</span></div>
      <div class="kv"><span class="muted">结束</span><span class="num">${fmtTime(r.end_wall)}</span></div>
      <div class="kv"><span class="muted">耗时</span><span class="num">${durHtml(r.duration_s)}${r.takt_s ? `<span class="faint"> / 节拍 ${r.takt_s} s</span>` : ""}</span></div>
      <div class="kv"><span class="muted">序列号</span><span class="mono">${r.sn ? esc(r.sn) : "—"}</span></div>
      <div class="kv"><span class="muted">结束原因</span><span>${END_REASON[r.end_reason] ?? esc(r.end_reason)}</span></div>
      <div class="kv"><span class="muted">画质劣化</span><span class="num">${r.quality_degraded_s ? `${r.quality_degraded_s.toFixed(1)} s` : "无"}</span></div>
    </div>
    <h3 class="d-h">步骤耗时</h3>${gantt(r.steps || [], r.duration_s, r.takt_s)}
    <h3 class="d-h">违规 <span class="muted num">${(r.violations || []).length}</span></h3><div class="viol">${viol}</div>
    <h3 class="d-h">证据图 <span class="muted num">${(r.frames ?? []).length}</span></h3>
    ${frames ? `<div class="thumbs">${frames}</div>` : `<div class="inline-empty muted">没有保存证据图</div>`}
    <h3 class="d-h">追溯信息</h3>
    <div class="kv-list">${hashRow("规则指纹", r.rules_hash)}${hashRow("证据哈希", r.sha256)}${hashRow("周期号", r.cycle_id)}</div>`;
}

async function openCycle(id, isDead = () => false, onOpen = () => {}) {
  let closed = false;
  const d = openDrawer({ title: "周期详情", subtitle: `<span class="mono">${esc(id)}</span>`, html: `<div class="skeleton-block" style="height:220px"></div>`, onClose: () => { closed = true; } });
  onOpen(d);
  const load = async () => {
    try {
      const rec = await api.cycle(id);
      if (closed || isDead()) return;
      d.body.innerHTML = detailHtml(rec);
    } catch (e) {
      if (closed || isDead()) return;
      d.body.innerHTML = errorBlock(e.message);
      d.body.querySelector("[data-retry]").onclick = load;
    }
  };
  d.body.addEventListener("click", async (e) => {
    const c = e.target.closest("[data-copy]");
    if (c) {
      const ok = await copyText(c.dataset.copy);
      return toast(ok ? "已复制到剪贴板" : "复制失败，请手动选择文本复制", ok ? "success" : "error");
    }
    const t = e.target.closest(".thumb");
    if (t) openLightbox(t.dataset.src, t.dataset.cap);
  });
  await load();
}

const RESULT_TONE = { PASS: "pass", FAIL: "fail", UNCERTAIN: "uncertain" };
const safeDuration = (r) => Number.isFinite(r.duration_s) && r.duration_s >= 0;
const meanDuration = (rows) => { const valid = rows.filter(safeDuration); return valid.length ? valid.reduce((n,r) => n+r.duration_s,0)/valid.length : null; };
const durationText = (v) => v == null ? "—" : v.toFixed(1);

function durationSequence(rows) {
  const data = rows.filter(safeDuration).slice(0,60).reverse();
  if (!data.length) return `<div class="trace-chart-empty">${icon("clock",28)}<b>暂无可绘制的周期耗时</b><span>完成一个周期后，这里会显示真实采样点。</span></div>`;
  const w = 1040, h = 230, left = 42, right = 20, top = 20, bottom = 34;
  const iw = w-left-right, ih = h-top-bottom;
  const taktValues = [...new Set(data.map((r) => r.takt_s).filter((v) => Number.isFinite(v) && v > 0))];
  const takt = taktValues.length === 1 && data.every((r) => r.takt_s === taktValues[0]) ? taktValues[0] : null;
  const max = Math.max(1,...data.map((r) => r.duration_s),takt || 0), topValue = Math.ceil(max*1.15/5)*5;
  const y = (value) => top + ih - value/topValue*ih;
  const coords = data.map((r,i) => [left + (data.length === 1 ? iw/2 : i/(data.length-1)*iw),y(r.duration_s)]);
  const path = coords.map(([x,yy],i) => `${i ? "L" : "M"}${x.toFixed(2)},${yy.toFixed(2)}`).join(" ");
  const grid = Array.from({length:4},(_,i) => topValue*i/3).map((v) => `<line class="trace-chart-grid" x1="${left}" y1="${y(v)}" x2="${w-right}" y2="${y(v)}"/><text class="trace-chart-axis" x="${left-10}" y="${y(v)+4}" text-anchor="end">${v.toFixed(v%1 ? 1 : 0)}</text>`).join("");
  return `<svg class="trace-sequence" viewBox="0 0 ${w} ${h}" role="img" aria-label="按开始时间排序的最近 ${data.length} 个周期耗时，单位秒；采样点可打开证据">
    <defs><linearGradient id="trace-duration-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="var(--accent)" stop-opacity=".18"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
    ${grid}<text class="trace-chart-axis" x="${left}" y="10">耗时 / s</text>
    ${data.length > 1 ? `<path class="trace-sequence-area" d="${path} L${coords.at(-1)[0]},${top+ih} L${coords[0][0]},${top+ih}Z"/><path class="trace-sequence-line" d="${path}"/>` : ""}
    ${takt ? `<path class="trace-takt-line" d="M${left},${y(takt)}H${w-right}"/><text class="trace-takt-label" x="${w-right}" y="${y(takt)-7}" text-anchor="end">统一节拍 ${takt.toFixed(1)} s</text>` : ""}
    ${data.map((r,i) => `<g class="trace-cycle-point ${RESULT_TONE[r.result] || "other"}" data-cycle="${esc(r.cycle_id)}" tabindex="0" role="button" aria-label="${esc(stationName(r.station))} ${r.duration_s.toFixed(1)} 秒，${esc(r.result)}，打开证据"><circle class="trace-point-hit" cx="${coords[i][0]}" cy="${coords[i][1]}" r="9"/><circle class="trace-point-core" cx="${coords[i][0]}" cy="${coords[i][1]}" r="${data.length > 40 ? 2.7 : 4}"/><title>${esc(fmtTime(r.start_wall))} · ${esc(stationName(r.station))} · ${r.duration_s.toFixed(1)} s · ${esc(r.result)}</title></g>`).join("")}
    <text class="trace-chart-axis" x="${left}" y="${h-8}">${fmtTime(data[0].start_wall)}</text><text class="trace-chart-axis" x="${w-right}" y="${h-8}" text-anchor="end">${data.length > 1 ? fmtTime(data.at(-1).start_wall) : "单个真实采样点"}</text>
  </svg>`;
}

function distribution(rows, current) {
  const counts = Object.fromEntries(RESULTS.slice(1).map(([value]) => [value,rows.filter((r) => r.result === value).length]));
  const total = rows.length, radius = 66, circumference = 2*Math.PI*radius;
  const passRate = total ? counts.PASS/total*100 : null;
  let offset = 0;
  const arcs = RESULTS.slice(1).map(([value,label]) => {
    const length = total ? counts[value]/total*circumference : 0;
    const svg = length ? `<circle class="trace-ring-segment ${RESULT_TONE[value]} ${current===value ? "selected" : ""}" cx="100" cy="100" r="${radius}" pathLength="${circumference}" stroke-dasharray="${Math.max(0,length-2)} ${circumference}" stroke-dashoffset="${-offset}" data-result-filter="${value}" tabindex="0" role="button" aria-label="筛选${label}，${counts[value]}个周期"><title>${label} ${counts[value]} 个 · ${(counts[value]/total*100).toFixed(1)}%</title></circle>` : "";
    offset += length; return svg;
  }).join("");
  return `<div class="trace-distribution-visual"><svg class="trace-ring" viewBox="0 0 200 200" aria-label="当前查询周期结果分布"><circle class="trace-ring-track" cx="100" cy="100" r="${radius}"/>${arcs}</svg><div class="trace-ring-center"><b class="num">${passRate == null ? "—" : passRate.toFixed(1)}<small>${passRate == null ? "" : "%"}</small></b><span>周期合规占比</span></div></div>
    <div class="trace-distribution-legend">${RESULTS.slice(1).map(([value,label]) => `<button class="trace-distribution-row ${RESULT_TONE[value]} ${current===value ? "selected" : ""}" data-result-filter="${value}" aria-pressed="${current===value}"><i></i><span>${label}</span><b class="num">${counts[value]}<small>个</small></b><em class="num">${total ? (counts[value]/total*100).toFixed(1) : "—"}${total ? "%" : ""}</em></button>`).join("")}</div>`;
}

export function render(root, params = {}) {
  const f = { station: params.station || "", result: RESULTS.some(([v]) => v === params.result) ? params.result : "", sn: params.sn || "" };
  root.innerHTML = `<div class="trace-workspace"><header class="command-head trace-command-head"><div><span class="command-kicker">周期分析 / 证据追溯</span><h2>从异常，到证据。</h2><p>真实周期、真实耗时与关键帧，在同一个查询上下文中。</p></div><div class="trace-query-stamp"><i></i><span data-query-scope>正在读取周期记录</span><small data-query-stamp></small></div></header>
    <section class="trace-report" data-trace-report aria-busy="true"><div class="trace-metrics" data-trace-metrics><div class="skeleton-block"></div></div>
      <div class="trace-visual-grid"><section class="trace-duration-panel"><header><div><span class="trace-panel-kicker">耗时分析</span><h3>周期耗时序列</h3></div><div class="trace-duration-summary" data-duration-summary></div></header><div data-duration-chart><div class="skeleton-block" style="height:230px"></div></div><footer><span data-chart-scope></span><span>点击采样点打开证据</span></footer></section>
      <section class="trace-distribution-panel"><header><span class="trace-panel-kicker">结果构成</span><h3>合规分布</h3></header><div data-distribution></div><footer>点击结果分布，定位对应周期</footer></section></div></section>
    <section class="trace-records"><div class="trace-records-head"><div><span class="trace-panel-kicker">周期明细</span><h3>把每次判定，追溯到现场。</h3></div><span class="muted" data-filter-label></span></div>
      <div class="filters"><label class="field"><span>工位</span><select id="f-st"><option value="">全部工位</option>${store.metas.map((m) => `<option value="${esc(m.id)}" ${m.id===f.station ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></label>
      <div class="seg-ctl" role="group" aria-label="按结果筛选">${RESULTS.map(([value,label]) => `<button class="${value===f.result ? "on" : ""}" data-result-filter="${value}" aria-pressed="${value===f.result}">${label}</button>`).join("")}</div>
      <label class="field search">${icon("search",14)}<input id="f-sn" value="${esc(f.sn)}" placeholder="按序列号查询（按 / 聚焦）" autocomplete="off"></label><button class="btn" id="refresh">${icon("refresh",14)}刷新查询</button></div>
      <section class="panel table-panel"><div class="table-wrap" id="tbl"><div class="skeleton-rows"></div></div><footer class="table-foot muted num" id="count"></footer></section></section></div>`;
  const tbl = $("#tbl",root), count = $("#count",root), report = $("[data-trace-report]",root);
  let baseRows = [], seq = 0, dead = false, drawer = null;
  const showCycle = (id) => { drawer?.close(); openCycle(id,() => dead,(d) => { drawer = d; }); };
  const paint = () => {
    if (dead) return;
    const rows = baseRows.filter((r) => !f.result || r.result === f.result), total = baseRows.length;
    const pass = baseRows.filter((r) => r.result==="PASS").length, fail = baseRows.filter((r) => r.result==="FAIL").length, uncertain = baseRows.filter((r) => r.result==="UNCERTAIN").length;
    const metrics = [["周期样本",total,"个","","normal","本次查询的最近周期"],["周期合规占比",total ? (pass/total*100).toFixed(1) : "—",total ? "%" : "","PASS","pass",`${pass} 个周期判定合格`],["异常周期",fail,"个","FAIL","fail","点击定位不合格周期"],["待复核",uncertain,"个","UNCERTAIN","uncertain","点击定位不确定周期"]];
    setHTML($("[data-trace-metrics]",root),metrics.map(([label,value,unit,result,tone,hint]) => `<button class="trace-metric ${tone} ${f.result===result ? "selected" : ""}" data-result-filter="${result}" aria-pressed="${f.result===result}"><span class="trace-metric-label">${label}${icon("arrow",12)}</span><b class="num">${value}<small>${unit}</small></b><span class="trace-metric-hint">${hint}</span></button>`).join(""));
    setHTML($("[data-duration-chart]",root),durationSequence(rows));
    const avg = meanDuration(rows), durations = rows.filter(safeDuration).map((r) => r.duration_s).sort((a,b) => a-b);
    const p95 = durations.length ? durations[Math.ceil(durations.length*.95)-1] : null;
    $("[data-duration-summary]",root).innerHTML = `<div><b class="num">${durationText(avg)}<small>s</small></b><span>筛选平均耗时</span></div><div class="trace-p95"><b class="num">${durationText(p95)}<small>s</small></b><span title="当前结果筛选的全部有效周期耗时，采用最近秩法">P95 耗时</span></div>`;
    $("[data-chart-scope]",root).textContent = `按开始时间排序 · 绘制最近 ${Math.min(60,rows.filter(safeDuration).length)} 个有效周期${rows.length===1 ? " · 不连示意曲线" : ""}`;
    setHTML($("[data-distribution]",root),distribution(baseRows,f.result));
    setHTML(tbl,tableHtml(rows));
    count.textContent = `当前结果显示 ${rows.length} / ${total} 条 · 统计仅覆盖本次查询最近 500 条，不代表全部历史`;
    $("[data-filter-label]",root).textContent = `${f.station ? stationName(f.station) : "全部工位"} / ${RESULTS.find(([value]) => value===f.result)?.[1] || "全部"}${f.sn ? ` / SN ${f.sn}` : ""}`;
    const known = baseRows.map((r) => store.metas.find((m) => m.id===r.station));
    $("[data-query-scope]",root).textContent = known.length && known.every((m) => m?.source==="sim") ? "仿真工位样本" : "已完成周期样本";
    root.querySelectorAll("[data-result-filter]").forEach((node) => { const active = node.dataset.resultFilter===f.result; node.setAttribute("aria-pressed",active); if (node.closest(".seg-ctl")) node.classList.toggle("on",active); });
  };
  const load = async () => {
    if (dead) return; const my = ++seq;
    report.setAttribute("aria-busy","true"); $("#refresh",root).disabled = true;
    $("[data-query-stamp]",root).textContent = "正在查询…";
    try {
      const rows = await api.cycles({station:f.station,sn:f.sn,limit:500});
      if (dead || my!==seq) return;
      baseRows = rows; paint(); $("[data-query-stamp]",root).textContent = `查询于 ${fmtTime(new Date())}`;
    } catch (e) {
      if (dead || my!==seq) return;
      baseRows = []; paint(); setHTML(tbl,errorBlock(e.message)); $("[data-retry]",tbl).onclick = load;
      $("[data-query-scope]",root).textContent = "查询失败"; $("[data-query-stamp]",root).textContent = "请检查连接后重试";
    } finally {
      if (!dead && my===seq) { report.setAttribute("aria-busy","false"); $("#refresh",root).disabled = false; }
    }
  };
  const applyResult = (value) => { f.result = value; paint(); };
  root.addEventListener("click",(e) => {
    const filter = e.target.closest("[data-result-filter]"); if (filter) return applyResult(filter.dataset.resultFilter);
    const cycle = e.target.closest("[data-cycle]"); if (cycle) showCycle(cycle.dataset.cycle);
  });
  root.addEventListener("keydown",(e) => {
    if (!["Enter"," "].includes(e.key) || e.target.tagName === "BUTTON") return;
    const cycle = e.target.closest("[data-cycle]"), filter = e.target.closest("[data-result-filter]");
    if (cycle) { e.preventDefault(); showCycle(cycle.dataset.cycle); }
    if (filter) { e.preventDefault(); applyResult(filter.dataset.resultFilter); }
  });
  $("#f-st",root).onchange = (e) => { f.station=e.target.value; load(); };
  $("#f-sn",root).oninput = debounce((e) => { if (!dead) { f.sn=e.target.value.trim(); load(); } },300);
  $("#refresh",root).onclick = load;
  tbl.addEventListener("click",(e) => { const row=e.target.closest("tr[data-id]"); if (row) showCycle(row.dataset.id); });
  tbl.addEventListener("keydown",(e) => { if (e.key==="Enter") { const row=e.target.closest("tr[data-id]"); if (row) { e.preventDefault(); showCycle(row.dataset.id); } } });
  const slash = (e) => { if (e.key==="/" && !e.metaKey && !e.ctrlKey && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); $("#f-sn",root).focus(); } };
  document.addEventListener("keydown",slash);
  load().then(() => { if (!dead && params.cycle) showCycle(params.cycle); });
  return () => { dead=true; seq++; drawer?.close(); drawer=null; document.removeEventListener("keydown",slash); };
}

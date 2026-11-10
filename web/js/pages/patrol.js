import { api } from "../api.js";
import { store } from "../store.js";
import { emptyBlock, errorBlock, toast } from "../ui.js";
import { $, esc, setHTML } from "../util.js";

const STATUS = {
  arrived: ["已到位", "success"], missed: ["未到位", "danger"],
  unknown: ["待核实", "warning"], pending: ["待执行", "muted"],
};
const chinaDay = () => new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());
const clock = (value) => value ? value.slice(11, 19) : "—";

async function request(path, options) {
  let res;
  try { res = await fetch(path, options); } catch { throw new Error("无法连接服务，请稍后重试"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（HTTP ${res.status}）`);
  return data;
}

export function render(root) {
  root.innerHTML = `
    <header class="command-head"><div><span class="command-kicker">STEPEYE / PATROL INTELLIGENCE</span><h2>巡更态势</h2><p>到位有证据，未知不误判。</p></div><div class="command-orbit" aria-hidden="true"><i></i><i></i><span>区域<br>感知</span></div><a class="btn" href="#/sitemap">定位到厂区图</a></header>
    <div class="filters">
      <label class="field"><span>日期</span><input type="date" id="p-day" value="${chinaDay()}" required></label>
      <button class="btn btn-primary" id="p-query">查询报表</button>
      <span class="muted">北京时间 · 跨午夜时段归开始日期</span>
    </div>
    <div class="patrol-stat-grid" id="p-kpis"></div>
    <section class="panel">
      <header class="panel-head"><div class="head-left"><h2>巡更到位</h2><span class="muted" id="p-summary">正在读取…</span></div></header>
      <div class="patrol-note muted">只确认区域内有人，不识别巡更人员身份。离线、画质降级或观测不足时标为待核实，不计作未到。</div>
      <div id="p-report"><div class="skeleton-rows"></div></div>
    </section>
    <section class="panel">
      <header class="panel-head"><div class="head-left"><h2>每日计划</h2><span class="muted">按工位和区域设置时段，要求连续停留</span></div></header>
      <div id="p-plans"><div class="skeleton-rows"></div></div>
      <details class="patrol-editor" id="p-editor"><summary>添加计划</summary>
        <form id="p-form" class="patrol-form">
          <label class="field"><span>计划名称</span><input id="p-name" maxlength="80" placeholder="如：东门早巡" required></label>
          <label class="field"><span>工位</span><select id="p-station" required>${store.metas.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} · ${esc(s.id)}</option>`).join("")}</select></label>
          <label class="field"><span>区域</span><select id="p-zone" required></select></label>
          <label class="field"><span>开始</span><input type="time" id="p-start" value="08:00" required></label>
          <label class="field"><span>结束</span><input type="time" id="p-end" value="09:00" required></label>
          <label class="field"><span>连续停留 / 秒</span><input type="number" id="p-min" value="10" min="1" max="3600" step="1" required></label>
          <button class="btn btn-primary" id="p-save" type="submit">保存计划</button>
          <div class="muted patrol-form-hint" id="p-source-note"></div>
        </form>
      </details>
    </section>`;
  let dead = false, canWrite = false, reportVersion = 0, zonesVersion = 0, statusFilter = "";
  const names = new Map(store.metas.map((s) => [s.id, s.name]));
  const reportEl = $("#p-report", root), plansEl = $("#p-plans", root), form = $("#p-form", root);
  const loadReport = async () => {
    const version = ++reportVersion;
    const day = $("#p-day", root).value;
    if (!day) { toast("请选择日期", "error"); return; }
    try {
      const data = await request(`/api/patrol/report?day=${encodeURIComponent(day)}`);
      if (dead || version !== reportVersion) return;
      $("#p-kpis", root).innerHTML = Object.entries(STATUS).map(([key, [text, tone]], i) => `<button class="patrol-stat ${statusFilter === key ? "selected" : ""}" data-filter-status="${key}" style="--i:${i}"><span class="command-kicker">0${i + 1} / ${text}</span><b class="num tone-${tone}-text">${data.counts[key]}<small>项</small></b><span class="muted">${key === "unknown" ? "观测不足，待人工核实" : key === "arrived" ? "连续人员观测确认" : key === "missed" ? "充分观测内未满足要求" : "时段尚未完成"}</span></button>`).join("");
      $("#p-summary", root).textContent = `已到 ${data.counts.arrived} · 未到 ${data.counts.missed} · 待核实 ${data.counts.unknown} · 待执行 ${data.counts.pending}`;
      if (!data.rows.length) { setHTML(reportEl, emptyBlock("当天没有巡更计划", "添加每日计划后，真实相机的人员观测会生成到位记录。")); return; }
      setHTML(reportEl, data.rows.map((r) => {
        const [label, tone] = STATUS[r.status] || STATUS.unknown;
        const ev = r.evidence;
        return `<article class="patrol-row" data-patrol-result="${esc(r.status)}">
          <div class="patrol-row-head"><div><b>${esc(r.name)}</b><span class="muted">${esc(names.get(r.station) || r.station)} / ${esc(r.zone)}</span></div><span class="tag tone-${tone}">${label}</span></div>
          <div class="patrol-metrics"><span>${esc(r.start)}–${esc(r.end)}${r.end < r.start ? "（次日）" : ""}</span>
            <span>连续到位 <b class="num">${r.longest_presence_s}</b> / ${r.min_presence_s} s</span>
            <span>观测覆盖 <b class="num">${(r.coverage * 100).toFixed(1)}%</b></span>
            <a href="#/live/${encodeURIComponent(r.station)}">查看工位</a></div>
          <div class="muted patrol-reason">${esc(r.reason)}${ev ? ` · 到位 ${clock(r.arrived_at)} · 观测证据 #${ev.first_id}–#${ev.last_id}` : ""}</div>
        </article>`;
      }).join(""));
      reportEl.querySelectorAll("[data-patrol-result]").forEach((row) => { row.hidden = !!statusFilter && row.dataset.patrolResult !== statusFilter; });
    } catch (e) {
      if (dead || version !== reportVersion) return;
      $("#p-summary", root).textContent = "读取失败";
      setHTML(reportEl, errorBlock(e.message));
      reportEl.querySelector("[data-retry]").onclick = loadReport;
    }
  };
  $("#p-kpis", root).onclick = (e) => {
    const b = e.target.closest("[data-filter-status]");
    if (!b) return;
    statusFilter = statusFilter === b.dataset.filterStatus ? "" : b.dataset.filterStatus;
    root.querySelectorAll("[data-filter-status]").forEach((x) => x.classList.toggle("selected", x.dataset.filterStatus === statusFilter));
    reportEl.querySelectorAll("[data-patrol-result]").forEach((row) => { row.hidden = !!statusFilter && row.dataset.patrolResult !== statusFilter; });
  };
  const loadPlans = async () => {
    try {
      const plans = await request("/api/patrol/plans");
      if (dead) return;
      setHTML(plansEl, plans.length ? plans.map((p) => `<div class="patrol-row" data-plan="${esc(p.id)}">
        <div class="patrol-row-head"><div><b>${esc(p.name)}</b><span class="muted">${esc(names.get(p.station) || p.station)} / ${esc(p.zone)}</span></div>
          ${canWrite ? `<button class="btn btn-sm" data-plan-delete="${esc(p.id)}">停用</button>` : ""}</div>
        <div class="patrol-metrics"><span>每日 ${esc(p.start)}–${esc(p.end)}${p.end < p.start ? "（次日）" : ""}</span><span>连续停留 ≥ ${p.min_presence_s} s</span></div></div>`).join("") :
        emptyBlock("尚未设置巡更计划", canWrite ? "点「添加计划」，选择工位、区域和时段。" : "请管理员或操作员添加计划。"));
    } catch (e) {
      if (dead) return;
      setHTML(plansEl, errorBlock(e.message));
      plansEl.querySelector("[data-retry]").onclick = loadPlans;
    }
  };
  const loadZones = async () => {
    const version = ++zonesVersion;
    const station = $("#p-station", root).value;
    $("#p-zone", root).innerHTML = "";
    if (!station) return;
    try {
      const cfg = await api.config(station);
      if (dead || version !== zonesVersion) return;
      $("#p-zone", root).innerHTML = cfg.zones.map((z) => `<option value="${esc(z.id)}">${esc(z.id)}</option>`).join("");
      $("#p-source-note", root).textContent = ["sim", "file"].includes(cfg.meta.source)
        ? "此工位为仿真／录像源，不会作为现场巡更到位证据。请使用开启人员感知的 RTSP／摄像头工位。"
        : "记录真实采集帧中的人员稳定计数；请确认工位已开启人员感知。";
    } catch (e) { if (!dead && version === zonesVersion) toast(e.message, "error"); }
  };
  $("#p-query", root).onclick = loadReport;
  $("#p-station", root).onchange = loadZones;
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (!canWrite || !form.reportValidity()) return;
    const button = $("#p-save", root); button.disabled = true;
    try {
      await request("/api/patrol/plans", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: $("#p-name", root).value.trim(), station: $("#p-station", root).value,
          zone: $("#p-zone", root).value, start: $("#p-start", root).value, end: $("#p-end", root).value,
          min_presence_s: +$("#p-min", root).value }) });
      if (dead) return;
      toast("巡更计划已保存"); $("#p-editor", root).open = false;
      await Promise.all([loadPlans(), loadReport()]);
    } catch (err) { if (!dead) toast(err.message, "error"); } finally { button.disabled = false; }
  };
  plansEl.onclick = async (e) => {
    const button = e.target.closest("[data-plan-delete]");
    if (!button || !canWrite || !confirm("停用此每日计划？历史观测仍保留。")) return;
    button.disabled = true;
    try {
      await request(`/api/patrol/plans/${encodeURIComponent(button.dataset.planDelete)}`, { method: "DELETE" });
      if (!dead) { toast("计划已停用"); await Promise.all([loadPlans(), loadReport()]); }
    } catch (err) { if (!dead) toast(err.message, "error"); } finally { button.disabled = false; }
  };
  const init = async () => {
    try {
      const me = await api.me();
      if (dead) return;
      canWrite = ["admin", "operator"].includes(me.role);
      $("#p-editor", root).hidden = !canWrite;
      await Promise.all([loadPlans(), loadReport(), loadZones()]);
    } catch (e) { if (!dead) { setHTML(reportEl, errorBlock(e.message)); $("#p-editor", root).hidden = true; } }
  };
  init();
  return () => { dead = true; reportVersion++; zonesVersion++; };
}

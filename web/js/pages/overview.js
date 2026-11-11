import { api } from "../api.js";
import { icon } from "../icons.js";
import { stackedBars, sparkline } from "../charts.js";
import { store, subscribe, stationStatus } from "../store.js";
import { badge, emptyBlock, errorBlock, STEP_STATUS, stationBadge, violationTag } from "../ui.js";
import { $, debounce, durHtml, esc, fmtTime, int, pct, relTime, setHTML } from "../util.js";

const KPI_DEFS = [
  { key: "cycles", label: "今日周期", unit: "个" },
  { key: "rate", label: "今日合规率", unit: "%" },
  { key: "takt", label: "平均节拍", unit: "" },
  { key: "abnormal", label: "今日异常周期", unit: "个" },
];

const skeletonKpis = () => KPI_DEFS.map(() => `<div class="kpi skeleton-block"></div>`).join("");

function aggregate() {
  let total = 0, pass = 0, dur = 0, takt = [], uncertain = 0;
  for (const m of store.metas) {
    const st = store.states.get(m.id)?.stats;
    if (m.takt_s) takt.push(m.takt_s);
    if (!st) continue;
    total += st.total;
    pass += st.pass;
    dur += (st.avg_duration_s || 0) * st.total;
    uncertain += st.by_result?.UNCERTAIN || 0;
  }
  return { total, pass, avg: total ? dur / total : null, target: takt.length ? takt.reduce((a, b) => a + b) / takt.length : null, uncertain };
}

function kpiHtml(trend) {
  const a = aggregate();
  const abnormal = a.total - a.pass;
  const series = {
    cycles: trend.map((b) => b.total),
    rate: trend.map((b) => (b.total ? b.pass / b.total : null)),
    takt: trend.map((b) => b.avg_duration_s),
    abnormal: trend.map((b) => b.total - b.pass),
  };
  const delta = a.avg != null && a.target ? a.avg - a.target : null;
  const val = {
    cycles: `${int(a.total)}`,
    rate: a.total ? pct(a.pass / a.total) : "—",
    takt: a.avg != null ? a.avg.toFixed(1) : "—",
    abnormal: `${int(abnormal)}`,
  };
  const sub = {
    cycles: `合格 ${int(a.pass)} · 异常 ${int(abnormal)}`,
    rate: a.total ? `待复核 ${int(a.uncertain)} 个` : "暂无数据",
    takt: a.target ? `目标 ${a.target.toFixed(1)} s` + (delta != null ? ` · <span class="${delta > 0 ? "tone-warning-text" : "tone-success-text"}">${delta > 0 ? "+" : ""}${delta.toFixed(1)} s</span>` : "") : "未设置目标",
    abnormal: a.total ? `占比 ${pct(abnormal / a.total)}%` : "暂无数据",
  };
  return KPI_DEFS.map((k, i) => `
    <div class="kpi" style="--i:${i}">
      <div class="kpi-label">${k.label}</div>
      <div class="kpi-value num">${val[k.key]}<span class="unit">${k.key === "takt" ? "s" : k.unit}</span></div>
      <div class="kpi-sub num">${sub[k.key]}</div>
      <div class="kpi-spark">${sparkline(series[k.key])}</div>
    </div>`).join("");
}

function cardShell(m, i) {
  return `<a class="st-card" href="#/live/${encodeURIComponent(m.id)}" data-id="${esc(m.id)}" style="--i:${i}">
    <header class="st-head">
      <div class="ellipsis"><div class="st-name ellipsis">${esc(m.name)}</div><div class="st-sub mono ellipsis">${esc(m.id)}${m.line ? ` · ${esc(m.line)}` : ""}</div></div>
      <span data-r="status"></span>
    </header>
    <div class="st-video">
      <img src="${api.stream(m.id)}" alt="${esc(m.name)} 实时画面" loading="lazy">
      <div class="video-hud"><span class="hud-chip" data-r="cycle"></span><span class="hud-chip num" data-r="timer"></span></div>
    </div>
    <div class="st-steps" data-r="steps"></div>
    <div class="st-now muted ellipsis" data-r="now"></div>
    <footer class="st-foot">
      <div class="takt"><div class="takt-bar"><i data-r="fill"></i></div><span class="takt-text num" data-r="taktText"></span></div>
      <div class="st-rate num" data-r="rate"></div>
    </footer>
  </a>`;
}

function patchCard(card, m) {
  const st = store.states.get(m.id);
  const c = st?.cycle;
  const r = (k) => card.querySelector(`[data-r="${k}"]`);
  setHTML(r("status"), stationBadge(stationStatus(m.id)));
  setHTML(r("cycle"), c ? `周期 #${c.no}` : "等待工件");
  setHTML(r("timer"), c ? durHtml(c.elapsed_s) : "");
  setHTML(r("steps"), (c?.steps ?? []).map((s) => {
    const key = s.early && s.status === "done" ? "early" : s.status;
    return `<i class="seg seg-${key}" title="${esc(s.name)} · ${STEP_STATUS[key].text}"></i>`;
  }).join(""));
  const active = c?.steps.find((s) => s.status === "active");
  const done = c?.steps.filter((s) => s.status === "done").length ?? 0;
  setHTML(r("now"), c ? (active ? `进行中：${esc(active.name)}` : `${done}/${c.steps.length} 步已完成`) : "等待工件到位");
  const takt = m.takt_s;
  const ratio = c && takt ? Math.min(c.elapsed_s / takt, 1.25) : 0;
  const fill = r("fill");
  fill.style.transform = `scaleX(${Math.min(ratio, 1)})`;
  fill.classList.toggle("over", ratio > 1);
  setHTML(r("taktText"), !takt ? "未设置节拍" : c ? `${c.elapsed_s.toFixed(1)} / ${takt} s` : `节拍目标 ${takt} s`);
  const s = st?.stats;
  setHTML(r("rate"), s?.total ? `合规 ${pct(s.pass_rate)}%<span class="unit">· ${int(s.total)} 个</span>` : `<span class="muted">暂无周期</span>`);
}

function alertsHtml() {
  if (!store.alerts.length) return emptyBlock("暂无告警", "出现漏步、错序或超时时，会在这里实时滚动显示。");
  return store.alerts.slice(0, 12).map((a) => {
    const meta = store.metas.find((m) => m.id === a.station);
    const tone = a.severity === "error" ? "danger" : "warning";
    return `<button class="alert-item" data-cycle="${esc(a.cycle_id)}">
      <span class="alert-ico tone-${tone}-text">${icon(a.severity === "error" ? "alarm" : "warn", 16)}</span>
      <span class="alert-main"><span class="alert-line">${violationTag(a.kind)}<span class="ellipsis" title="${esc(a.message)}">${esc(a.message)}</span></span>
      <span class="alert-meta muted ellipsis">${esc(meta?.name ?? a.station)}</span></span>
      <time class="alert-time faint num" title="${a.at ? fmtTime(a.at) : ""}">${a.at ? relTime(a.at) : "—"}</time>
    </button>`;
  }).join("");
}

export function render(root) {
  root.innerHTML = `
    <header class="command-head"><div><span class="command-kicker">STEPEYE / OPERATIONS</span><h2>生产态势<span class="command-live">实时</span></h2><p>规则、视觉与现场事件，同一张运行视图。</p></div><div class="command-fleet" id="fleet-state"></div><a class="btn btn-primary" href="#/sitemap">进入空间态势 ${icon("overview", 14)}</a></header>
    <section class="kpis" id="kpis" aria-label="关键指标">${skeletonKpis()}</section>
    <div class="ov-grid">
      <section class="wall" id="wall" aria-label="工位墙"></section>
      <aside class="ov-side">
        <section class="panel"><header class="panel-head"><h2>近 24 小时周期结果</h2><span class="muted">个 / 小时</span></header>
          <div class="panel-body" id="trend"><div class="skeleton-block" style="height:168px"></div></div>
          <div class="legend"><span><i class="dot dot-success"></i>合格</span><span><i class="dot dot-warning"></i>待复核</span><span><i class="dot dot-danger"></i>不合格</span></div>
        </section>
        <section class="panel grow"><header class="panel-head"><h2>实时告警</h2><span class="muted">最近 12 条</span></header>
          <div class="panel-body alerts" id="alerts"></div></section>
        <section class="panel"><header class="panel-head"><h2>拍照即检 · 告警中心</h2><span class="muted">累计</span></header>
          <div class="panel-body" id="vstats"><div class="skeleton-block" style="height:96px"></div></div></section>
      </aside>
    </div>`;
  const kpis = $("#kpis", root), wall = $("#wall", root), trendEl = $("#trend", root), alertsEl = $("#alerts", root);
  let trend = [], built = "";
  // V8:拍照即检与告警中心统计(数据源 /api/inspection/stats + /api/alerts/stats)
  (async () => {
    try {
      const [inspection, alerts] = await Promise.allSettled([api.inspectionStats(), api.alertStats()]);
      const insp = inspection.status === "fulfilled" ? inspection.value : null;
      const al = alerts.status === "fulfilled" ? alerts.value : null;
      const kv = (l, v) => `<div class="kv"><span class="muted">${l}</span><span class="num">${v}</span></div>`;
      const done = insp?.by_status?.done ?? 0, failed = insp?.by_status?.failed ?? 0;
      setHTML($("#vstats", root), `<div class="kv-grid">
        ${kv("检测任务", insp ? `${done}${failed ? ` <span class="faint">失败 ${failed}</span>` : ""}` : "未启用")}
        ${kv("NG 结论", insp ? `<span class="tone-danger-text">${insp.by_verdict?.NG ?? 0}</span>` : "—")}
        ${kv("待处理告警", al ? `<span class="tone-warning-text">${al.pending}</span>` : "暂不可用")}
        ${kv("已核误报", al ? al.false_alarm : "—")}
      </div>${insp ? "" : '<a class="btn btn-sm" href="#/settings?tab=ai">配置拍照即检服务</a>'}`);
    } catch {
      setHTML($("#vstats", root), '<div class="inline-empty muted">统计暂不可用</div>');
    }
  })();

  const buildWall = () => {
    const sig = store.metas.map((m) => m.id).join("|");
    if (sig === built) return;
    built = sig;
    wall.innerHTML = store.metas.length
      ? store.metas.map(cardShell).join("")
      : emptyBlock("尚未配置工位", "使用 python -m visual serve 配置文件.yaml 启动，工位会出现在这里。");
  };
  const paint = () => {
    buildWall();
    const online = store.metas.filter((m) => stationStatus(m.id) !== "offline").length;
    const risks = store.metas.filter((m) => ["alarm", "warn"].includes(stationStatus(m.id))).length;
    setHTML($("#fleet-state", root), `<span><b class="num">${online}<small> / ${store.metas.length}</small></b>工位在线</span><span class="${risks ? "tone-warning-text" : ""}"><b class="num">${risks}</b>需关注</span>`);
    wall.querySelectorAll(".st-card").forEach((el) => patchCard(el, store.metas.find((m) => m.id === el.dataset.id)));
    setHTML(kpis, kpiHtml(trend));
    setHTML(alertsEl, alertsHtml());
  };
  const loadTrend = debounce(async () => {
    try {
      trend = await api.trend(24);
      setHTML(trendEl, trend.length ? stackedBars(trend) : emptyBlock("近 24 小时暂无周期", "工位完成第一个周期后，这里会显示结果分布。"));
      paint();
    } catch (e) {
      trendEl.innerHTML = errorBlock(e.message);
      trendEl.querySelector("[data-retry]").onclick = loadTrend;
    }
  }, 300);

  alertsEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-cycle]");
    if (b) location.hash = `#/trace?cycle=${encodeURIComponent(b.dataset.cycle)}`;
  });
  paint();
  loadTrend();
  const off = subscribe((type, p) => {
    if (type === "state" || type === "ready" || type === "event") paint();
    if (type === "event" && p.kind === "cycle_end") loadTrend();
  });
  const tick = setInterval(() => setHTML(alertsEl, alertsHtml()), 15000);
  return () => { off(); clearInterval(tick); };
}

import { api } from "../api.js";
import { icon } from "../icons.js";
import { badge, emptyBlock, errorBlock, openDrawer, toast } from "../ui.js";
import { $, esc, fmtTime, setHTML } from "../util.js";

const STATUS = {
  pending: { text: "待处理", icon: "clock", tone: "warning" },
  processing: { text: "处理中", icon: "active", tone: "accent" },
  done: { text: "已处理", icon: "check", tone: "success" },
  false_alarm: { text: "误报", icon: "cross", tone: "muted" },
};
const SOURCE = { sop: "SOP违规", safety: "安全规则", inspection: "拍照即检", anomaly: "外观检测" };
const KIND = { sop_cycle: "SOP 周期", inspection: "拍照即检", anomaly: "外观检测" };
const STATUS_FILTER = [["", "全部"], ["pending", "待处理"], ["processing", "处理中"], ["done", "已处理"], ["false_alarm", "误报"]];
const sevTag = (s) => `<span class="tag tone-${s === "error" ? "danger" : "warning"}">${s === "error" ? "严重" : "警告"}</span>`;
const statusChip = (s) => badge(STATUS[s] ?? STATUS.pending);
const verdict = (v) => v === "FAIL" ? "NG" : v || "UNCERTAIN";

function tableHtml(rows) {
  if (!rows.length) return emptyBlock("没有符合条件的告警", "当前筛选下没有告警，可切换状态或来源。");
  return `<table class="table"><thead><tr><th>时间</th><th>工位/场景</th><th>来源</th><th>级别</th><th>内容</th><th>状态</th><th>处理人</th></tr></thead><tbody>${rows.map((r) => `<tr tabindex="0" data-id="${r.id}">
    <td class="num nowrap">${fmtTime(r.created)}</td><td class="ellipsis" title="${esc(r.station)}">${esc(r.station)}</td><td>${SOURCE[r.source] ?? esc(r.source)}</td><td>${sevTag(r.severity)}</td>
    <td class="ellipsis" title="${esc(r.message)}">${esc(r.message)}</td><td>${statusChip(r.status)}</td><td>${esc(r.handler) || '<span class="faint">—</span>'}</td></tr>`).join("")}</tbody></table>`;
}

function detailHtml(r, canWrite) {
  const flow = !canWrite ? [] : r.status === "pending" ? ["processing", "done", "false_alarm"] : r.status === "processing" ? ["done", "false_alarm"] : [];
  const actions = flow.map((s) => `<button class="btn ${s === "done" ? "btn-primary" : ""}" data-to="${s}">${STATUS[s].text}</button>`).join("")
    || `<div class="inline-empty muted">${canWrite ? `已终态：${STATUS[r.status]?.text || esc(r.status)}` : "当前账号只读；处理告警请使用操作员或管理员账号"}</div>`;
  return `<div class="d-head">${statusChip(r.status)}<span class="muted">${SOURCE[r.source] ?? esc(r.source)}</span></div><div class="kv-grid">
    <div class="kv"><span class="muted">工位/场景</span><span>${esc(r.station)}</span></div><div class="kv"><span class="muted">时间</span><span class="num">${fmtTime(r.created)}</span></div>
    <div class="kv"><span class="muted">关联</span><span class="mono ellipsis">${esc(r.ref_id || "—")}</span></div><div class="kv"><span class="muted">处理人</span><span>${esc(r.handler) || "—"}</span></div></div>
    <h3 class="d-h">内容</h3><div>${esc(r.message)}</div><h3 class="d-h">处理说明</h3><textarea id="a-comment" rows="2" ${canWrite ? "" : "disabled"} placeholder="误报原因或处理措施">${esc(r.comment || "")}</textarea><div class="ez-actions">${actions}</div>`;
}

export function render(root, params = {}) {
  const f = { status: "pending", station: params.station || "" };
  root.innerHTML = `<div class="filters">
    <div class="seg-ctl" role="group" aria-label="按状态筛选">${STATUS_FILTER.map(([v,t]) => `<button class="${v === f.status ? "on" : ""}" data-v="${v}" aria-pressed="${v === f.status}">${t}</button>`).join("")}</div>
    <label class="field"><span>来源</span><select id="al-src"><option value="">全部</option>${Object.entries(SOURCE).map(([v,t]) => `<option value="${v}">${t}</option>`).join("")}</select></label>
    <label class="field"><span>工位/场景</span><input id="al-station" value="${esc(f.station)}" placeholder="全部"></label><button class="btn" id="al-refresh">${icon("refresh", 14)}刷新</button></div>
    <section class="panel table-panel"><div class="table-wrap" id="al-tbl"><div class="skeleton-rows"></div></div><footer class="table-foot muted num" id="al-foot"></footer></section>
    <section class="panel table-panel shadow-panel"><header class="panel-head"><div class="head-left"><h2>影子真值标注</h2><span class="muted">先看证据，再标 PASS / NG；无证据的样本可留待复核</span></div>
      <label class="select-wrap"><span class="sr-only">影子样本筛选</span><select id="sh-filter"><option value="pending">待标注</option><option value="labeled">已标注</option><option value="all">全部样本</option></select></label></header>
      <div class="shadow-summary muted num" id="sh-summary" role="status"></div><div class="table-wrap" id="sh-tbl"><div class="skeleton-rows"></div></div><footer class="table-foot muted" id="sh-foot"></footer></section>`;
  const tbl = $("#al-tbl",root), foot = $("#al-foot",root), shtbl = $("#sh-tbl",root);
  let canWrite = false, dead = false, timer = null, alertRows = [], shadows = [], drawer = null, loadVersion = 0, shadowVersion = 0;
  const paintShadow = () => {
    const filter = $("#sh-filter",root).value;
    const rows = shadows.filter((r) => filter === "pending" ? !r.truth : filter === "labeled" ? !!r.truth : true);
    $("#sh-foot",root).textContent = `${canWrite ? "标注后自动更新复核统计。" : "当前账号只读。"} 列表显示最近 ${shadows.length} 条样本。`;
    if (!rows.length) return setHTML(shtbl, emptyBlock("没有符合条件的影子样本", filter === "pending" ? "使用 serve --shadow 采集样本，或切换“已标注”查看复核结果。" : "切换筛选条件，或等待新样本。"));
    setHTML(shtbl, `<table class="table shadow-table"><thead><tr><th>时间</th><th>工位/场景</th><th>类型</th><th>系统判定</th><th>人工真值</th><th>操作</th></tr></thead><tbody>${rows.map((r) => `<tr data-shadow-row="${r.id}">
      <td class="num">${fmtTime(r.created)}</td><td>${esc(r.station)}</td><td>${KIND[r.kind] || esc(r.kind)}</td><td>${esc(verdict(r.system_verdict))}</td><td>${esc(r.truth || "待标注")}</td>
      <td><div class="shadow-actions"><button class="btn btn-sm" data-shadow-detail="${r.id}">证据 / 详情</button>${canWrite ? `<button class="btn btn-sm" data-truth="${r.id}:PASS">PASS</button><button class="btn btn-sm" data-truth="${r.id}:NG">NG</button>` : ""}</div></td></tr>`).join("")}</tbody></table>`);
  };
  const paintReport = (report) => {
    const total = Object.values(report.kinds || {}).reduce((a,r) => {
      for (const k of ["labeled","unlabeled","miss","false_alarm","uncertain"]) a[k] = (a[k] || 0) + (r[k] || 0);
      return a;
    }, {});
    $("#sh-summary",root).textContent = `已标注 ${total.labeled || 0} · 待标注 ${total.unlabeled || 0} · 漏检 ${total.miss || 0} · 误杀 ${total.false_alarm || 0} · 不确定 ${total.uncertain || 0}。仅代表已复核样本，不是生产准确率。`;
  };
  const loadShadow = async () => {
    const version = ++shadowVersion;
    try {
      const [rows,report] = await Promise.all([api.shadowSamples(false, 500), api.shadowReport()]);
      if (dead || version !== shadowVersion) return;
      shadows = rows; paintShadow(); paintReport(report);
    } catch (e) {
      if (dead || version !== shadowVersion) return;
      setHTML(shtbl, errorBlock(e.message)); $("[data-retry]",shtbl).onclick = loadShadow;
    }
  };
  const markTruth = async (id, truth, button) => {
    if (!canWrite || dead) return;
    button.disabled = true;
    try { await api.shadowTruth(id,truth); if (dead) return; toast(`已标注 ${truth}`); drawer?.close(); drawer = null; await loadShadow(); }
    catch (e) { if (!dead) { toast(e.message,"error"); button.disabled = false; } }
  };
  const openShadow = async (id) => {
    drawer?.close();
    const d = drawer = openDrawer({ title:"影子样本复核", subtitle:`#${id}`, html:'<div class="skeleton-block" style="height:160px"></div>' });
    try {
      const r = await api.shadowSample(id);
      if (dead || drawer !== d) return;
      d.body.innerHTML = `<div class="kv-grid"><div class="kv"><span class="muted">工位/场景</span><span>${esc(r.station)}</span></div><div class="kv"><span class="muted">系统判定</span><span>${esc(verdict(r.system_verdict))}</span></div>
        <div class="kv"><span class="muted">人工真值</span><span>${esc(r.truth || "待标注")}</span></div><div class="kv"><span class="muted">关联记录</span><span class="mono">${esc(r.ref_id || "—")}</span></div></div>
        <h3 class="d-h">判定依据</h3><pre class="shadow-json">${esc(JSON.stringify(r.body || {},null,2))}</pre><h3 class="d-h">证据</h3><div data-shadow-evidence class="shadow-evidence"><span class="muted">正在读取关联证据…</span></div>
        <div class="ez-actions">${canWrite ? `<button class="btn btn-primary" data-truth="${id}:PASS">PASS / 合格</button><button class="btn" data-truth="${id}:NG">NG / 异常</button>` : '<span class="muted">当前账号只读</span>'}</div>`;
      d.body.onclick = (e) => { const b = e.target.closest("[data-truth]"); if (b) { const [sampleId,truth] = b.dataset.truth.split(":"); markTruth(+sampleId,truth,b); } };
      const evidence = $("[data-shadow-evidence]",d.body);
      try {
        let images = [], href = "";
        if (r.kind === "sop_cycle" && r.ref_id) {
          const rec = await api.cycle(r.ref_id);
          images = (rec.frames || []).map((frame) => api.frame(r.ref_id,frame.file.split(/[\\/]/).pop())); href = `#/trace?cycle=${encodeURIComponent(r.ref_id)}`;
        } else if (r.kind === "inspection" && r.ref_id) {
          const rec = await api.inspectionTask(r.ref_id); images = rec.image_urls || []; href = `#/inspection?task=${encodeURIComponent(r.ref_id)}`;
        }
        if (dead || drawer !== d) return;
        evidence.innerHTML = images.length ? `${images.map((src) => `<img src="${esc(src)}" alt="样本 ${id} 关联证据" loading="lazy">`).join("")}${href ? `<a class="btn btn-sm" href="${href}">打开关联记录</a>` : ""}` : '<span class="muted">该样本未保留关联图片，可根据现场记录复核；无法确认时请保持待标注。</span>';
      } catch (e) { if (!dead && drawer === d) evidence.textContent = `关联证据不可用：${e.message}。无法确认时请保持待标注。`; }
    } catch (e) { if (!dead && drawer === d) { d.body.innerHTML = errorBlock(e.message); $("[data-retry]",d.body).onclick = () => openShadow(id); } }
  };
  const load = async () => {
    const version = ++loadVersion;
    try {
      const [rows, stats] = await Promise.all([api.alerts({ status:f.status, source:f.source, limit:500 }), api.alertStats()]);
      if (dead || version !== loadVersion) return;
      alertRows = rows.filter((r) => !f.station || r.station.includes(f.station));
      // 治理计数只在有值时出现,平时不占视线
      const gov = stats.dropped || stats.storm_suppressed
        ? ` · 风暴抑制 ${stats.storm_suppressed || 0} 条 · 预算拦截 ${stats.dropped || 0} 条` : "";
      setHTML(tbl,tableHtml(alertRows)); foot.textContent = `显示最近 ${alertRows.length} 条${f.station ? ` · 工位/场景：${f.station}` : ""}${gov}`;
    } catch (e) { if (!dead && version === loadVersion) { setHTML(tbl,errorBlock(e.message)); $("[data-retry]",tbl).onclick = load; } }
  };
  const openAlert = (id) => {
    const r = alertRows.find((x) => x.id === id); if (!r) return;
    drawer?.close(); const d = drawer = openDrawer({ title:"告警详情",subtitle:`#${id}`,html:detailHtml(r,canWrite) });
    d.body.onclick = async (e) => {
      const b = e.target.closest("[data-to]"); if (!b || !canWrite) return; b.disabled = true;
      try { await api.alertPatch(id,{ status:b.dataset.to,comment:$("#a-comment",d.body).value.trim() }); if (dead) return; toast(`已标记为「${STATUS[b.dataset.to].text}」`); d.close(); drawer = null; await load(); }
      catch (err) { if (!dead) { toast(err.message,"error"); b.disabled = false; } }
    };
  };
  shtbl.onclick = (e) => {
    const detail = e.target.closest("[data-shadow-detail]"); if (detail) return openShadow(+detail.dataset.shadowDetail);
    const b = e.target.closest("[data-truth]"); if (b) { const [id,truth] = b.dataset.truth.split(":"); markTruth(+id,truth,b); }
  };
  $("#sh-filter",root).onchange = paintShadow;
  root.querySelector(".seg-ctl").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; f.status = b.dataset.v; root.querySelectorAll("[data-v]").forEach((x) => { x.classList.toggle("on",x===b); x.setAttribute("aria-pressed",x===b); }); load(); };
  $("#al-src",root).onchange = (e) => { f.source = e.target.value; load(); };
  $("#al-station",root).onchange = (e) => { f.station = e.target.value.trim(); load(); };
  $("#al-refresh",root).onclick = async () => { await Promise.all([load(),loadShadow()]); };
  tbl.onclick = (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) openAlert(+tr.dataset.id); };
  tbl.onkeydown = (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr[data-id]"); if (tr) { e.preventDefault(); openAlert(+tr.dataset.id); } } };
  const init = async () => {
    try { const me = await api.me(); if (dead) return; canWrite = ["admin","operator"].includes(me.role); await Promise.all([load(),loadShadow()]); if (!dead) timer = setInterval(load,5000); }
    catch (e) { if (!dead) { setHTML(tbl,errorBlock(e.message)); $("[data-retry]",tbl).onclick = init; } }
  };
  init();
  return () => { dead = true; loadVersion++; shadowVersion++; clearInterval(timer); drawer?.close(); drawer = null; };
}

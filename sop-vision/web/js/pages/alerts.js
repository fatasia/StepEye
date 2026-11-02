import { api } from "../api.js";
import { icon } from "../icons.js";
import { badge, emptyBlock, errorBlock, openDrawer, resultBadge, toast } from "../ui.js";
import { $, esc, fmtTime, setHTML } from "../util.js";

const STATUS = {
  pending: { text: "待处理", icon: "clock", tone: "warning" },
  processing: { text: "处理中", icon: "active", tone: "accent" },
  done: { text: "已处理", icon: "check", tone: "success" },
  false_alarm: { text: "误报", icon: "cross", tone: "muted" },
};
const SOURCE = { sop: "SOP违规", safety: "安全规则", inspection: "拍照即检" };
const STATUS_FILTER = [["", "全部"], ["pending", "待处理"], ["processing", "处理中"], ["done", "已处理"], ["false_alarm", "误报"]];

const sevTag = (s) => `<span class="tag tone-${s === "error" ? "danger" : "warning"}">${s === "error" ? "严重" : "警告"}</span>`;
const statusChip = (s) => badge(STATUS[s] ?? STATUS.pending);

function tableHtml(rows) {
  if (!rows.length) return emptyBlock("没有符合条件的告警", "全部处理完毕,或等待新的告警产生。");
  const body = rows.map((r) => `<tr tabindex="0" data-id="${r.id}">
      <td class="num nowrap">${fmtTime(r.created)}</td>
      <td class="ellipsis" title="${esc(r.station)}">${esc(r.station)}</td>
      <td>${SOURCE[r.source] ?? esc(r.source)}</td>
      <td>${sevTag(r.severity)}</td>
      <td class="ellipsis" title="${esc(r.message)}">${esc(r.message)}</td>
      <td>${statusChip(r.status)}</td>
      <td>${esc(r.handler) || '<span class="faint">—</span>'}</td></tr>`).join("");
  return `<table class="table"><thead><tr><th>时间</th><th>工位/场景</th><th>来源</th><th>级别</th><th>内容</th><th>状态</th><th>处理人</th></tr></thead><tbody>${body}</tbody></table>`;
}

function detailHtml(r, me) {
  const flow = r.status === "pending" ? ["processing", "done", "false_alarm"]
    : r.status === "processing" ? ["done", "false_alarm"] : [];
  const actions = flow.map((s) => `<button class="btn ${s === "done" ? "btn-primary" : ""}" data-to="${s}">${STATUS[s].text}</button>`).join("")
    || `<div class="inline-empty muted">已终态:${STATUS[r.status].text}${r.handler ? ` · 处理人 ${esc(r.handler)}` : ""}</div>`;
  return `
    <div class="d-head">${statusChip(r.status)}<span class="muted">${SOURCE[r.source] ?? esc(r.source)}</span></div>
    <div class="kv-grid">
      <div class="kv"><span class="muted">工位/场景</span><span>${esc(r.station)}</span></div>
      <div class="kv"><span class="muted">时间</span><span class="num">${fmtTime(r.created)}</span></div>
      <div class="kv"><span class="muted">关联</span><span class="mono ellipsis" title="${esc(r.ref_id ?? "")}">${esc(r.ref_id ?? "—")}</span></div>
      <div class="kv"><span class="muted">处理人</span><span>${esc(r.handler) || "—"}</span></div>
    </div>
    <h3 class="d-h">内容</h3><div>${esc(r.message)}</div>
    <h3 class="d-h">处理说明</h3>
    <textarea id="a-comment" rows="2" placeholder="处理备注(误报原因/处理措施)">${esc(r.comment ?? "")}</textarea>
    <div class="ez-actions">${actions}</div>`;
}

async function openAlert(id, me) {
  const d = openDrawer({ title: "告警详情", subtitle: `#${id}`, html: '<div class="skeleton-block" style="height:160px"></div>' });
  const rows = await api.alerts({});
  const r = rows.find((x) => x.id === id) ?? {};
  d.body.innerHTML = detailHtml(r, me);
  d.body.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-to]");
    if (!b) return;
    try {
      const comment = d.body.querySelector("#a-comment").value.trim();
      await api.alertPatch(id, { status: b.dataset.to, comment });
      toast(`已标记为「${STATUS[b.dataset.to].text}」`, "success");
      d.close?.() ?? d.body.closest(".drawer")?.querySelector("[data-close]")?.click();
      load();
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

let load = async () => {};
export function render(root) {
  const f = { status: "pending" };
  root.innerHTML = `
    <div class="filters">
      <div class="seg-ctl" role="group" aria-label="按状态筛选">${STATUS_FILTER.map(([v, t]) => `<button class="${v === f.status ? "on" : ""}" data-v="${v}" aria-pressed="${v === f.status}">${t}</button>`).join("")}</div>
      <label class="field"><span>来源</span><select id="al-src"><option value="">全部</option>${Object.entries(SOURCE).map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></label>
      <button class="btn" id="al-refresh">${icon("refresh", 14)}刷新</button>
    </div>
    <section class="panel table-panel"><div class="table-wrap" id="al-tbl"><div class="skeleton-rows"></div></div><footer class="table-foot muted num" id="al-foot"></footer></section>`;
  const tbl = $("#al-tbl", root), foot = $("#al-foot", root);
  let me = { username: "" };
  let timer = null;
  load = async () => {
    try {
      me = await api.me();
      const rows = await api.alerts({ ...f, limit: 200 });
      setHTML(tbl, tableHtml(rows));
      foot.textContent = `显示最近 ${rows.length} 条`;
      if (rows.some((r) => r.status === "pending") && !timer) timer = setInterval(load, 5000);
      if (!rows.some((r) => r.status === "pending") && timer) { clearInterval(timer); timer = null; }
    } catch (e) {
      tbl.innerHTML = errorBlock(e.message);
      const rb = tbl.querySelector("[data-retry]");
      if (rb) rb.onclick = load;
    }
  };
  root.querySelector(".seg-ctl").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    f.status = b.dataset.v;
    root.querySelectorAll(".seg-ctl button").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
    load();
  };
  $("#al-src", root).onchange = (e) => { f.source = e.target.value; load(); };
  $("#al-refresh", root).onclick = async () => { await load(); toast("已刷新"); };
  tbl.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) openAlert(+tr.dataset.id, me); });
  tbl.addEventListener("keydown", (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr[data-id]"); if (tr) openAlert(+tr.dataset.id, me); } });
  load();
  return () => { if (timer) clearInterval(timer); };
}

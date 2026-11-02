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
    const vs = r.violations.map((v) => violationTag(v.kind)).join("") || '<span class="faint">—</span>';
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
    const name = f.file.split("/").pop();
    const cap = `${FRAME_TAG[f.tag] ?? f.tag}${f.step ? ` · ${f.step}` : ""}`;
    return `<button class="thumb" data-src="${esc(api.frame(r.cycle_id, name))}" data-cap="${esc(cap)}"><img src="${esc(api.frame(r.cycle_id, name))}" alt="${esc(cap)}" loading="lazy"><span>${esc(cap)}</span></button>`;
  }).join("");
  const viol = r.violations.length
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
    <h3 class="d-h">步骤耗时</h3>${gantt(r.steps, r.duration_s, r.takt_s)}
    <h3 class="d-h">违规 <span class="muted num">${r.violations.length}</span></h3><div class="viol">${viol}</div>
    <h3 class="d-h">证据图 <span class="muted num">${(r.frames ?? []).length}</span></h3>
    ${frames ? `<div class="thumbs">${frames}</div>` : `<div class="inline-empty muted">没有保存证据图</div>`}
    <h3 class="d-h">追溯信息</h3>
    <div class="kv-list">${hashRow("规则指纹", r.rules_hash)}${hashRow("证据哈希", r.sha256)}${hashRow("周期号", r.cycle_id)}</div>`;
}

async function openCycle(id) {
  const d = openDrawer({ title: "周期详情", subtitle: `<span class="mono">${esc(id)}</span>`, html: `<div class="skeleton-block" style="height:220px"></div>` });
  const load = async () => {
    try {
      const rec = await api.cycle(id);
      d.body.innerHTML = detailHtml(rec);
    } catch (e) {
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

export function render(root, params) {
  const f = { station: "", result: "", sn: "" };
  root.innerHTML = `
    <div class="filters">
      <label class="field"><span>工位</span><select id="f-st"><option value="">全部工位</option>${store.metas.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("")}</select></label>
      <div class="seg-ctl" role="group" aria-label="按结果筛选">${RESULTS.map(([v, t]) => `<button class="${v === "" ? "on" : ""}" data-v="${v}" aria-pressed="${v === ""}">${t}</button>`).join("")}</div>
      <label class="field search">${icon("search", 14)}<input id="f-sn" placeholder="按序列号搜索（按 / 聚焦）" autocomplete="off"></label>
      <button class="btn" id="refresh">${icon("refresh", 14)}刷新</button>
    </div>
    <section class="panel table-panel"><div class="table-wrap" id="tbl"><div class="skeleton-rows"></div></div><footer class="table-foot muted num" id="count"></footer></section>`;
  const tbl = $("#tbl", root), count = $("#count", root);
  let seq = 0;
  const load = async () => {
    const my = ++seq;
    try {
      const rows = await api.cycles({ ...f, limit: 100 });
      if (my !== seq) return;
      setHTML(tbl, tableHtml(rows));
      count.textContent = `显示最近 ${rows.length} 条`;
    } catch (e) {
      tbl.innerHTML = errorBlock(e.message);
      tbl.querySelector("[data-retry]").onclick = load;
      count.textContent = "";
    }
  };
  $("#f-st", root).onchange = (e) => { f.station = e.target.value; load(); };
  $("#f-sn", root).oninput = debounce((e) => { f.sn = e.target.value.trim(); load(); }, 300);
  $("#refresh", root).onclick = async () => { await load(); toast("列表已刷新"); };
  root.querySelector(".seg-ctl").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    f.result = b.dataset.v;
    root.querySelectorAll(".seg-ctl button").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
    load();
  };
  tbl.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) openCycle(tr.dataset.id); });
  tbl.addEventListener("keydown", (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr[data-id]"); if (tr) openCycle(tr.dataset.id); } });
  const slash = (e) => { if (e.key === "/" && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); $("#f-sn", root).focus(); } };
  document.addEventListener("keydown", slash);
  load().then(() => { if (params.cycle) openCycle(params.cycle); });
  return () => document.removeEventListener("keydown", slash);
}

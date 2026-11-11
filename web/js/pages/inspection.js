import { api } from "../api.js";
import { icon } from "../icons.js";
import { badge, emptyBlock, errorBlock, openDrawer, openLightbox, resultBadge, toast } from "../ui.js";
import { $, esc, fmtTime, setHTML } from "../util.js";

/* 状态词汇沿用 UI-SPEC 三重编码;任务状态与判定结果分开两列 */
const TASK_STATUS = {
  pending: { text: "等待", icon: "clock", tone: "muted" },
  running: { text: "检测中", icon: "active", tone: "accent" },
  failed: { text: "失败", icon: "warn", tone: "danger" },
};
const STATUS_FILTER = [["", "全部"], ["pending", "等待"], ["running", "检测中"], ["done", "完成"], ["failed", "失败"]];
const statusChip = (s) => (s === "done" ? "" : badge(TASK_STATUS[s] ?? TASK_STATUS.pending));
const confHtml = (c) => (c == null ? '<span class="faint">—</span>' : `<span class="num">${Math.round(c * 100)}%</span>`);

function tableHtml(rows) {
  if (!rows.length) return emptyBlock("还没有检测任务", "选择场景并上传照片,或等待定点相机自动上传。");
  const body = rows.map((r) => `<tr tabindex="0" data-id="${esc(r.id)}">
      <td class="num nowrap">${fmtTime(r.created)}</td>
      <td>${esc(r.scenario_name ?? r.scenario)}</td>
      <td>${r.status === "done" ? resultBadge(r.verdict ?? "UNCERTAIN") : statusChip(r.status)}</td>
      <td class="num">${confHtml(r.confidence)}</td>
      <td class="ellipsis" title="${esc(r.reason)}">${esc(r.reason) || '<span class="faint">—</span>'}</td>
      <td class="mono">${r.images?.length ?? 0}</td></tr>`).join("");
  return `<table class="table"><thead><tr><th>提交时间</th><th>场景</th><th>结果</th><th class="right">置信度</th><th>判定说明</th><th class="right">图片</th></tr></thead><tbody>${body}</tbody></table>`;
}

function detailHtml(r) {
  const verdict = r.status === "done" ? resultBadge(r.verdict ?? "UNCERTAIN") : statusChip(r.status);
  const thumbs = (r.images ?? []).map((n) => {
    const url = api.inspectionImage(r.id, n);
    return `<button class="thumb" data-src="${esc(url)}" data-cap="原图"><img src="${esc(url)}" alt="原图" loading="lazy"><span>原图</span></button>`;
  }).join("");
  return `
    <div class="d-head">${verdict}<span class="muted">${esc(r.scenario_name ?? r.scenario)}</span></div>
    <div class="kv-grid">
      <div class="kv"><span class="muted">任务号</span><span class="mono ellipsis" title="${esc(r.id)}">${esc(r.id)}</span></div>
      <div class="kv"><span class="muted">提交时间</span><span class="num">${fmtTime(r.created)}</span></div>
      <div class="kv"><span class="muted">置信度</span><span class="num">${r.confidence == null ? "—" : Math.round(r.confidence * 100) + "%"}</span></div>
    </div>
    <h3 class="d-h">判定说明</h3><div>${esc(r.reason) || '<span class="faint">无</span>'}</div>
    <h3 class="d-h">原图 <span class="muted num">${(r.images ?? []).length}</span></h3>
    ${thumbs ? `<div class="thumbs">${thumbs}</div>` : '<div class="inline-empty muted">没有图片</div>'}`;
}

async function openTask(id) {
  const d = openDrawer({ title: "检测详情", subtitle: `<span class="mono">${esc(id)}</span>`, html: '<div class="skeleton-block" style="height:180px"></div>' });
  const load = async () => {
    try {
      d.body.innerHTML = detailHtml(await api.inspectionTask(id));
      d.body.querySelectorAll(".thumb").forEach((t) => (t.onclick = () => openLightbox(t.dataset.src, t.dataset.cap)));
    } catch (e) {
      d.body.innerHTML = errorBlock(e.message);
      d.body.querySelector("[data-retry]").onclick = load;
    }
  };
  await load();
}

export function render(root) {
  const f = { status: "" };
  root.innerHTML = `
    <div class="filters">
      <div class="seg-ctl" role="group" aria-label="按状态筛选">${STATUS_FILTER.map(([v, t]) => `<button class="${v === "" ? "on" : ""}" data-v="${v}" aria-pressed="${v === ""}">${t}</button>`).join("")}</div>
      <button class="btn" id="i-refresh">${icon("refresh", 14)}刷新</button>
    </div>
    <section class="panel" id="upload-panel">
      <header class="panel-head"><div class="head-left"><h2>上传检测</h2><span class="muted">巡检拍照 · 首件对照;未启用的组合不可提交</span></div></header>
      <div class="panel-body insp-upload">
        <label class="field"><span>场景</span><select id="i-scenario"></select></label>
        <div class="dropzone" id="i-drop" tabindex="0" role="button" aria-label="选择或拖入图片">
          ${icon("camera", 20)}<span>点击选择或拖入图片(最多 10 张)</span>
        </div>
        <input type="file" id="i-file" accept="image/jpeg,image/png" multiple hidden>
        <span class="muted" id="i-count"></span>
        <button class="btn btn-primary" id="i-submit" disabled>${icon("check", 14)}提交检测</button>
        <div class="inline-empty muted" id="i-note" hidden></div>
      </div>
    </section>
    <section class="panel table-panel"><div class="table-wrap" id="i-tbl"><div class="skeleton-rows"></div></div><footer class="table-foot muted num" id="i-count-foot"></footer></section>`;
  const tbl = $("#i-tbl", root), foot = $("#i-count-foot", root), picker = $("#i-file", root);
  const setupButton=document.createElement('button');setupButton.className='btn';setupButton.textContent='新建检查规则';
  root.querySelector('.filters').append(setupButton);
  setupButton.onclick=()=>{
    const d=openDrawer({title:'新建拍照检查',subtitle:'写清检查什么、什么情况算异常',html:'<form><label class="field"><span>名称</span><input name="name" required maxlength="80"></label><label class="field"><span>检查要求</span><textarea name="instruction" required minlength="3" maxlength="1500" rows="4"></textarea></label><p>先在设置中绑定视觉辅助模型服务。</p><button class="btn btn-primary" type="submit">保存检查规则</button><p data-error role="alert"></p></form>'});
    d.body.classList.add('workflow');d.body.querySelector('form').onsubmit=async e=>{e.preventDefault();const f=e.target;try{const r=await fetch('/api/setup/inspection',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:f.elements.name.value,instruction:f.elements.instruction.value})});const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'无法保存');d.close();loadScenarios();load();}catch(error){f.querySelector('[data-error]').textContent=error.message;}};
  };
  let files = [];
  let timer = null;
  let enabledScenarios = [];

  const load = async () => {
    try {
      const rows = await api.inspectionTasks({ ...f, limit: 100 });
      setHTML(tbl, tableHtml(rows));
      foot.textContent = `显示最近 ${rows.length} 条`;
      const busy = rows.some((r) => r.status === "pending" || r.status === "running");
      if (busy && !timer) timer = setInterval(load, 3000);
      if (!busy && timer) { clearInterval(timer); timer = null; }
    } catch (e) {
      tbl.innerHTML = emptyBlock('拍照即检尚未就绪','先绑定 AI 服务，再创建检查规则。')+'<a class="btn" href="#/settings?tab=ai">配置 AI 服务</a>';
      const retry = tbl.querySelector("[data-retry]");
      if (retry) retry.onclick = load;
      foot.textContent = "";
    }
  };

  const loadScenarios = async () => {
    try {
      enabledScenarios = (await api.inspectionScenarios()).filter((s) => s.enabled);
      const sel = $("#i-scenario", root);
      sel.innerHTML = enabledScenarios.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("") || "<option value=''>无可用场景</option>";
      $("#i-note", root).hidden = enabledScenarios.length > 0;
      if (!enabledScenarios.length) $("#i-note", root).textContent = "没有已启用的场景:默认只启用有评测数据支撑的组合,需在 configs/inspection.yaml 开启。";
      $("#i-submit", root).disabled = !enabledScenarios.length || !files.length;
    } catch (e) {
      $("#i-note", root).hidden = false;
      $("#i-note", root).innerHTML = '<span>尚未配置检查规则。</span> <a href="#/settings?tab=ai">配置 AI 服务</a>，然后点「新建检查规则」。';
    }
  };

  const setFiles = (list) => {
    files = [...list].slice(0, 10);
    $("#i-count", root).textContent = files.length ? `已选 ${files.length} 张` : "";
    $("#i-submit", root).disabled = !files.length || !enabledScenarios.length;
  };

  const submit = async () => {
    const btn = $("#i-submit", root);
    btn.disabled = true;
    try {
      await api.inspectionSubmit($("#i-scenario", root).value, files);
      toast(`已加入队列(${files.length} 张)`, "success");
      setFiles([]);
      picker.value = "";
      load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = !enabledScenarios.length;
    }
  };

  $("#i-drop", root).onclick = () => picker.click();
  $("#i-drop", root).onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") picker.click(); };
  picker.onchange = () => setFiles(picker.files);
  const drop = $("#i-drop", root);
  ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => setFiles(e.dataTransfer.files));
  $("#i-submit", root).onclick = submit;
  $("#i-refresh", root).onclick = async () => { await load(); toast("列表已刷新"); };
  root.querySelector(".seg-ctl").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    f.status = b.dataset.v;
    root.querySelectorAll(".seg-ctl button").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
    load();
  };
  tbl.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) openTask(tr.dataset.id); });
  tbl.addEventListener("keydown", (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr[data-id]"); if (tr) openTask(tr.dataset.id); } });

  loadScenarios();
  load();
  return () => { if (timer) clearInterval(timer); };
}

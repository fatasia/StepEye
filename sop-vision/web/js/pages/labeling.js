import { icon } from "../icons.js";
import { openDrawer, toast } from "../ui.js";
import { $, esc, fmtTime } from "../util.js";

const STATE = { unlabeled: "未标注", suggested: "机器建议", draft: "人工草稿", confirmed: "已确认", background: "空背景" };
const JOB = { queued: "排队中", running: "预标注中", completed: "已完成", partial: "部分完成", failed: "失败", canceled: "已取消" };
const COLORS = ["--accent", "--info", "--success", "--warning"];
const frozen = (asset) => ["confirmed", "background"].includes(asset?.state);
const clone = (value) => JSON.parse(JSON.stringify(value));
const key = (id) => encodeURIComponent(id);
const listOf = (value) => Array.isArray(value) ? value : value?.items || value?.versions || value?.revisions || [];

class LabelError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function request(url, options = {}) {
  let res;
  try { res = await fetch(url, options); }
  catch { throw new LabelError("无法连接标注服务，请检查服务后重试", 0); }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : body.detail ? JSON.stringify(body.detail) : `请求失败（HTTP ${res.status}）`;
    throw new LabelError(detail, res.status);
  }
  if (res.status === 204) return null;
  return res.json();
}
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export function render(root, params = {}) {
  root.innerHTML = `<div class="label-workspace">
    <header class="label-page-head"><div><span class="label-kicker">数据工作流 / 人机协同标注</span><h2>把第一张图，变成可训练的数据。</h2></div><div class="label-head-actions"><label class="field"><span>项目</span><select id="lab-project" aria-label="选择标注项目"><option>正在读取…</option></select></label><button class="btn" id="lab-create" data-write hidden>新建项目</button><button class="btn btn-primary" id="lab-upload" data-write hidden>${icon("camera",14)}导入图片</button></div></header>
    <div class="label-flow" aria-label="标注工作流"><div class="on"><b>01</b><span>导入与类别<small>先定义要找什么</small></span></div><i></i><div data-flow-trial><b>02</b><span>先试两张<small>看建议，再确认真值</small></span></div><i></i><div data-flow-batch><b>03</b><span>批量与确认版<small>确认后才进入导出</small></span></div><span class="label-role" data-role></span></div>
    <div class="label-notice" role="alert" data-notice hidden></div>
    <div class="label-layout"><aside class="label-assets"><header><div><span class="label-kicker">项目图片</span><b data-asset-total>—</b></div><button class="icon-btn" id="lab-refresh-assets" aria-label="刷新图片">${icon("refresh",14)}</button></header>
      <div class="label-assets-filter"><select id="lab-state" aria-label="图片状态"><option value="">全部状态</option>${Object.entries(STATE).map(([v,t]) => `<option value="${v}">${t}</option>`).join("")}<option value="issues">最近任务问题图</option></select><label data-write hidden><input type="checkbox" id="lab-select-page">选择本页未确认图</label></div><div class="label-asset-list" data-asset-list><div class="skeleton-rows"></div></div><footer><button class="icon-btn" id="lab-prev-page" aria-label="上一页">‹</button><span class="num" data-page>—</span><button class="icon-btn" id="lab-next-page" aria-label="下一页">›</button></footer></aside>
      <section class="label-canvas-panel"><header class="label-image-head"><div><b data-image-name>选择一张图片开始</b><span class="num" data-image-meta></span></div><span class="label-state" data-image-state></span></header>
        <div class="label-canvas-tools"><div class="label-tools"><button class="on" data-tool="draw" data-write hidden title="B：框选；Alt：在已有框内新建">${icon("config",14)}框选</button><button data-tool="select" title="V：选择或移动标注">选择</button><span></span><button id="lab-undo" data-write hidden title="撤销当前编辑（Ctrl+Z）">↶ 撤销</button><button id="lab-redo" data-write hidden title="重做（Ctrl+Shift+Z）">↷</button></div><div class="label-tools"><button id="lab-zoom-out" aria-label="缩小画布">−</button><span class="num" data-zoom>适合画布</span><button id="lab-zoom-in" aria-label="放大画布">＋</button><button id="lab-fit">适合</button><button id="lab-history">${icon("clock",13)}历史</button></div></div>
        <div class="label-canvas-viewport" data-canvas-viewport><div class="label-canvas-surface"><div class="label-image-wrap" data-image-wrap hidden><img data-image alt="当前标注图片" draggable="false"><svg data-overlay role="img" aria-label="图片标注画布"></svg></div><div class="label-canvas-empty" data-canvas-empty>${icon("camera",36)}<h3>把注意力，留给这张图。</h3><p>选择项目并导入图片。没有合适模型时，也能直接框选完成标注。</p></div></div></div>
        <footer class="label-canvas-foot"><span data-canvas-status>准备就绪</span><span>B 框选 · V 选择 · Delete 删除 · Ctrl+S 保存</span></footer></section>
      <aside class="label-controls"><section><header class="label-control-head"><span class="label-kicker">标注与复核</span><b class="num" data-target-count>0 个目标</b></header><div class="label-class-palette" data-classes></div><div class="label-target-list" data-targets><span class="label-muted">选择图片后查看目标</span></div><div data-selected-target></div>
        <label class="label-background" data-background-wrap hidden><input type="checkbox" id="lab-background">我已核对：本图没有任何目标</label><div class="label-save-actions" data-write hidden><button class="btn" id="lab-save">保存草稿</button><button class="btn btn-primary" id="lab-confirm">确认标注</button></div><p class="label-help" data-annotation-help>机器建议不是人工真值，确认后才可进入确认版。</p></section>
        <section><header class="label-control-head"><span class="label-kicker">模型预标注</span><button class="label-link" id="lab-jobs">任务记录</button></header><label class="label-form-field"><span>选择引擎</span><select id="lab-engine"></select></label><p class="label-help" data-engine-reason></p><label class="label-form-field label-threshold"><span>置信度阈值</span><input type="number" id="lab-threshold" min=".05" max=".95" step=".05" value=".4"></label><div class="label-auto-actions" data-write hidden><button class="btn" id="lab-trial">先试两张</button><button class="btn" id="lab-batch" disabled>批量预标注</button></div><p class="label-help" data-trial-note>先试两张、逐张确认，再对勾选的图片批量运行。</p><div data-current-job></div></section>
        <section class="label-delivery"><header class="label-control-head"><span class="label-kicker">质量与交付</span></header><div class="label-quality-mini" data-quality-mini>正在读取质量统计…</div><div class="label-delivery-actions"><button class="btn" id="lab-quality">质量检查</button><button class="btn" id="lab-versions">确认版与导出</button></div><p class="label-help">导出来自不可变确认版，不读取未复核的机器建议。</p></section></aside></div></div>`;

  const s = { projects: [], project: null, assets: [], total: 0, offset: 0, asset: null, targets: [], selected: -1, classId: 0, dirty: false, baseline: "[]", undo: [], redo: [], zoom: 1, tool: "draw", engines: [], jobs: [], selectedIds: new Set(), trial: null, trialReady: false, busy: false, assetLoading: false, currentJob: null, finishedJobId: null };
  let dead = false, canWrite = false, loadSeq = 0, assetSeq = 0, drawer = null, poll = null, polling = false, gesture = null, pendingJob = null;
  const viewport = $("[data-canvas-viewport]",root), wrap = $("[data-image-wrap]",root), overlay = $("[data-overlay]",root), image = $("[data-image]",root);
  const editable = () => canWrite && !!s.asset && !frozen(s.asset) && !s.busy && !s.assetLoading;
  const projectPath = (suffix = "") => `/api/label-projects/${key(s.project.id)}${suffix}`;
  const assetPath = (suffix = "") => `/api/label-assets/${key(s.asset.id)}${suffix}`;
  const notice = (message, conflict = false) => {
    const el = $("[data-notice]",root); el.hidden = !message;
    el.innerHTML = message ? `<span>${esc(message)}</span>${conflict ? '<button class="btn btn-sm" data-reload-asset>放弃本地编辑，读取最新</button>' : '<button class="icon-btn" data-clear-notice aria-label="关闭提示">×</button>'}` : "";
    const reload = $("[data-reload-asset]",el); if (reload) reload.onclick = () => { s.dirty = false; loadAsset(s.asset.id); notice(""); };
    const clear = $("[data-clear-notice]",el); if (clear) clear.onclick = () => notice("");
  };
  const failure = (e) => { if (!dead) { if (e.status === 409) notice(`版本冲突：${e.message}。本地框未丢弃，请先核对最新修订。`,true); else toast(e.message,"error"); } };
  const issueMap = () => { const out=new Map(),seen=new Set(); for(const j of s.jobs)for(const i of j.items || [])if(!seen.has(i.asset_id)){seen.add(i.asset_id);if(["failed","no_targets"].includes(i.status))out.set(i.asset_id,i);}return out; };
  const dirtyCheck = () => {
    s.dirty = JSON.stringify(s.targets) !== s.baseline;
    $("[data-canvas-status]",root).textContent = s.dirty ? "有未保存修改" : s.asset ? `修订 r${s.asset.revision} · ${STATE[s.asset.state] || s.asset.state}` : "准备就绪";
    paintActions();
  };
  const pushUndo = (before) => { s.undo.push(before); if (s.undo.length > 50) s.undo.shift(); s.redo = []; };
  const mutate = (fn) => { if (!editable()) return; pushUndo(clone(s.targets)); fn(); dirtyCheck(); paintTargets(); paintCanvas(); };
  const className = (id) => s.project?.classes?.[id] ?? `未知类别 ${id}`;
  const boxStyle = (id) => `--box-color:var(${COLORS[(+id || 0)%COLORS.length]})`;

  function paintCanvas() {
    if (!s.asset) { overlay.innerHTML = ""; return; }
    const h = 1000*s.asset.height/s.asset.width;
    overlay.setAttribute("viewBox",`0 0 1000 ${h}`);
    overlay.classList.toggle("drawing",s.tool === "draw" && editable());
    overlay.innerHTML = s.targets.map((t,i) => {
      const [x0,y0,x1,y1] = t.xyxy_norm, x = x0*1000, y = y0*h, w = (x1-x0)*1000, hh = (y1-y0)*h;
      const selected = s.selected === i, text = `${i+1} · ${className(t.label_id)}`;
      return `<g class="label-box ${selected ? "selected" : ""}" data-box="${i}" style="${boxStyle(t.label_id)}"><rect class="label-bbox" x="${x}" y="${y}" width="${w}" height="${hh}"/><g class="label-box-title" transform="translate(${x},${Math.max(0,y-22)})"><rect width="${Math.max(90,Math.min(250,text.length*12))}" height="22"/><text x="6" y="15">${esc(text)}</text></g>${selected && editable() ? [["nw",x,y],["ne",x+w,y],["sw",x,y+hh],["se",x+w,y+hh]].map(([handle,xx,yy]) => `<rect class="label-handle" data-handle="${handle}" x="${xx-5}" y="${yy-5}" width="10" height="10"/>`).join("") : ""}</g>`;
    }).join("");
    if (gesture?.kind === "draw" && gesture.draft) {
      const [x0,y0,x1,y1] = gesture.draft;
      overlay.innerHTML += `<rect class="label-draft-box" x="${x0*1000}" y="${y0*h}" width="${(x1-x0)*1000}" height="${(y1-y0)*h}"/>`;
    }
  }

  function fitImage() {
    if (!s.asset || wrap.hidden) return;
    const scale = Math.min((viewport.clientWidth-56)/s.asset.width,(viewport.clientHeight-56)/s.asset.height);
    wrap.style.width = `${Math.max(1,s.asset.width*scale*s.zoom)}px`;
    wrap.style.height = `${Math.max(1,s.asset.height*scale*s.zoom)}px`;
    $("[data-zoom]",root).textContent = s.zoom === 1 ? "适合画布" : `${Math.round(s.zoom*100)}%`;
  }

  function paintTargets() {
    $("[data-target-count]",root).textContent = `${s.targets.length} 个目标`;
    $("[data-targets]",root).innerHTML = s.targets.length ? s.targets.map((t,i) => `<button class="label-target-row ${s.selected===i ? "on" : ""}" data-select-box="${i}" style="${boxStyle(t.label_id)}"><i></i><span>${i+1}. ${esc(className(t.label_id))}</span><small class="num">${Number.isFinite(t.confidence) ? (t.confidence*100).toFixed(0)+"%" : "人工"}</small></button>`).join("") : '<div class="label-no-targets">当前没有框。可以手工框选；模型未检出不等于空背景。</div>';
    const target = s.targets[s.selected];
    $("[data-selected-target]",root).innerHTML = target ? `<div class="label-target-editor"><label class="label-form-field"><span>所选目标类别</span><select data-box-class ${editable() ? "" : "disabled"}>${s.project.classes.map((name,i) => `<option value="${i}" ${target.label_id===i ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></label><div class="label-box-coordinates num">${target.xyxy_norm.map((v) => v.toFixed(3)).join(" / ")}</div>${editable() ? '<button class="label-delete" data-delete-box>删除所选框</button>' : ""}</div>` : "";
    const select = $("[data-box-class]",root); if (select) select.onchange = () => mutate(() => { s.targets[s.selected].label_id = +select.value; delete s.targets[s.selected].confidence; });
    const del = $("[data-delete-box]",root); if (del) del.onclick = deleteSelected;
    root.querySelectorAll("[data-select-box]").forEach((b) => b.onclick = () => { s.selected = +b.dataset.selectBox; paintTargets(); paintCanvas(); });
    $("[data-background-wrap]",root).hidden = !editable() || s.targets.length > 0;
    $("[data-annotation-help]",root).textContent = frozen(s.asset) ? "此图已确认，自动任务不会覆盖。如需修订，可从历史恢复为新草稿。" : s.asset?.state === "suggested" && !s.targets.length ? "模型未检出目标，请人工核对；只有明确勾选后才能确认为空背景。" : "机器建议不是人工真值，确认后才可进入确认版。";
    paintActions();
  }

  function paintActions() {
    $("#lab-save",root).disabled = !editable() || !s.dirty;
    $("#lab-confirm",root).disabled = !editable() || (!s.targets.length && !$("#lab-background",root).checked);
    $("#lab-confirm",root).textContent = s.targets.length ? "确认标注" : "确认为空背景";
    $("#lab-undo",root).disabled = !editable() || !s.undo.length;
    $("#lab-redo",root).disabled = !editable() || !s.redo.length;
    $("#lab-history",root).disabled = !s.asset;
    root.querySelectorAll("[data-tool='draw']").forEach((b) => { b.disabled = !editable(); b.title = frozen(s.asset) ? "已确认图片需先从历史恢复修订" : "B：框选；Alt：在已有框内新建"; });
    const engine = s.engines.find((e) => e.id === $("#lab-engine",root).value);
    const compatible = !!engine?.available && s.project?.classes.every((name) => engine.classes.includes(name));
    const running = s.jobs.some((j) => ["queued","running"].includes(j.status));
    const candidates = s.assets.filter((a) => !frozen(a));
    $("#lab-trial",root).disabled = !canWrite || !compatible || running || !candidates.length || s.busy;
    $("#lab-trial",root).textContent=candidates.length===1 ? "先试这一张" : "先试两张";
    $("#lab-trial",root).title = !candidates.length ? "需要未确认图片" : !compatible ? "当前引擎不支持项目类别，可直接人工框选" : running ? "等待当前任务结束" : "先对最多两张未确认图片试跑";
    $("#lab-batch",root).disabled = !canWrite || !compatible || running || !s.trialReady || !s.selectedIds.size || s.busy;
    $("#lab-batch",root).textContent = `批量预标注${s.selectedIds.size ? `（${s.selectedIds.size}）` : ""}`;
    $("#lab-batch",root).title = !s.trialReady ? "先试两张并逐张确认，再运行批量任务" : !s.selectedIds.size ? "先在左侧勾选未确认图片" : "仅运行勾选图片，不改已确认真值";
    $("#lab-upload",root).disabled = !s.project || s.busy;
    $("#lab-jobs",root).disabled = !s.project;
    $("#lab-quality",root).disabled = !s.project;
    $("#lab-versions",root).disabled = !s.project;
  }

  function paintClasses() {
    $("[data-classes]",root).innerHTML = (s.project?.classes || []).map((name,i) => `<button class="label-class ${s.classId===i ? "on" : ""}" data-class="${i}" style="${boxStyle(i)}" title="${esc(name)}"><i></i><span>${esc(name)}</span><small>${i<9 ? i+1 : ""}</small></button>`).join("");
    root.querySelectorAll("[data-class]").forEach((b) => b.onclick = () => { s.classId=+b.dataset.class; paintClasses(); });
  }

  function paintAssets() {
    const issues = issueMap(), state = $("#lab-state",root).value;
    for(const a of s.assets)if(frozen(a))issues.delete(a.id);
    const rows = state === "issues" ? s.assets.filter((a) => issues.has(a.id)) : s.assets;
    $("[data-asset-total]",root).textContent = `${s.total} 张`;
    $("[data-asset-list]",root).innerHTML = rows.length ? rows.map((a) => `<div class="label-asset-row ${s.asset?.id===a.id ? "on" : ""}">${canWrite ? `<input type="checkbox" data-check-asset="${esc(a.id)}" aria-label="选择 ${esc(a.name)}" ${s.selectedIds.has(a.id) ? "checked" : ""} ${frozen(a) ? "disabled" : ""}>` : ""}<button data-open-asset="${esc(a.id)}"><img src="/api/label-assets/${key(a.id)}/image" alt="" loading="lazy"><span><b title="${esc(a.name)}">${esc(a.name)}</b><small class="state-${esc(a.state)}">${STATE[a.state] || esc(a.state)}${issues.has(a.id) ? ` · ${issues.get(a.id).status === "no_targets" ? "未检出" : "任务失败"}` : ""}</small></span></button></div>`).join("") : `<div class="label-assets-empty">${state === "issues" ? "本页没有最近任务问题图" : s.project ? "当前筛选没有图片。导入第一批图片，或切换状态。" : "先创建或选择一个标注项目。"}</div>`;
    root.querySelectorAll("[data-open-asset]").forEach((b) => b.onclick = () => loadAsset(b.dataset.openAsset));
    root.querySelectorAll("[data-check-asset]").forEach((box) => box.onchange = () => { box.checked ? s.selectedIds.add(box.dataset.checkAsset) : s.selectedIds.delete(box.dataset.checkAsset); paintActions(); });
    $("[data-page]",root).textContent = s.total ? `${s.offset+1}–${Math.min(s.offset+100,s.total)} / ${s.total}` : "0 / 0";
    $("#lab-prev-page",root).disabled = s.offset === 0;
    $("#lab-next-page",root).disabled = s.offset+100 >= s.total;
    $("#lab-select-page",root).checked = s.assets.filter((a) => !frozen(a)).length > 0 && s.assets.filter((a) => !frozen(a)).every((a) => s.selectedIds.has(a.id));
  }

  async function loadAssets(chooseFirst = false) {
    if (!s.project || dead) return;
    const projectId = s.project.id, filter = $("#lab-state",root).value;
    const q = new URLSearchParams({limit:"100",offset:String(s.offset)}); if (filter && filter !== "issues") q.set("state",filter);
    const out = await request(projectPath(`/assets?${q}`));
    if (dead || s.project?.id !== projectId) return;
    s.assets = out.items || []; s.total = out.total || 0;
    for (const a of s.assets) if (frozen(a)) s.selectedIds.delete(a.id);
    paintAssets(); paintActions();
    if (chooseFirst && s.assets.length) await loadAsset(s.assets[0].id);
  }

  async function loadAsset(id, force = false) {
    if (s.dirty && !force && !window.confirm("当前图片有未保存的修改。放弃修改并切换图片？")) return;
    const my = ++assetSeq;
    s.assetLoading=true;paintActions();
    $("[data-canvas-status]",root).textContent = "正在读取图片和修订…";
    try {
      const a = await request(`/api/label-assets/${key(id)}/annotations`);
      if (dead || my!==assetSeq || a.project_id!==s.project?.id) return;
      s.asset=a; s.targets=clone(a.targets || []); s.baseline=JSON.stringify(s.targets); s.dirty=false; s.undo=[]; s.redo=[]; s.selected=-1; s.zoom=1;
      $("#lab-background",root).checked=false;
      image.src=`/api/label-assets/${key(a.id)}/image`; image.alt=a.name;
      wrap.hidden=false; $("[data-canvas-empty]",root).hidden=true;
      $("[data-image-name]",root).textContent=a.name; $("[data-image-name]",root).title=a.name;
      $("[data-image-meta]",root).textContent=`${a.width} × ${a.height} px · r${a.revision}${a.synthetic ? " · 合成样本，不能作真实准确率验收" : ""}`;
      $("[data-image-state]",root).textContent=STATE[a.state] || a.state;
      $("[data-image-state]",root).className=`label-state state-${a.state}`;
      fitImage(); paintTargets(); paintCanvas(); paintAssets(); dirtyCheck(); notice("");
    } catch (e) { failure(e); }
    finally { if(!dead&&my===assetSeq){s.assetLoading=false;paintTargets();paintCanvas();} }
  }

  async function saveDraft() {
    if (!editable() || !s.dirty) return;
    s.busy=true; paintActions();
    try { await request(assetPath("/annotations"),json("PUT",{expected_revision:s.asset.revision,targets:s.targets})); s.dirty=false; await loadAsset(s.asset.id,true); await loadAssets(); toast("草稿已保存"); }
    finally { s.busy=false; paintTargets(); paintCanvas(); }
  }

  async function confirmAsset() {
    if (!editable()) return;
    const background=!s.targets.length && $("#lab-background",root).checked;
    if (!s.targets.length && !background) return;
    try {
      if (s.dirty) await saveDraft();
      s.busy=true; paintActions();
      await request(assetPath("/confirm"),json("POST",{expected_revision:s.asset.revision,background}));
      await loadAsset(s.asset.id,true); await loadAssets(); await refreshTrial(); await refreshQuality(); toast(background ? "已确认为空背景" : "标注已确认");
    } catch (e) { failure(e); }
    finally { s.busy=false; paintActions(); paintTargets(); }
  }

  function deleteSelected() { if (s.selected<0) return; mutate(() => { s.targets.splice(s.selected,1); s.selected=-1; }); }
  function undo(redo = false) {
    if (!editable()) return; const from=redo ? s.redo : s.undo, to=redo ? s.undo : s.redo;
    if (!from.length) return; to.push(clone(s.targets)); s.targets=from.pop(); s.selected=-1; dirtyCheck(); paintTargets(); paintCanvas();
  }

  const normalizedPoint = (event) => {
    const p=overlay.createSVGPoint(); p.x=event.clientX; p.y=event.clientY; const matrix=overlay.getScreenCTM(); if (!matrix) return null;
    const pos=p.matrixTransform(matrix.inverse()), h=1000*s.asset.height/s.asset.width;
    return [Math.max(0,Math.min(1,pos.x/1000)),Math.max(0,Math.min(1,pos.y/h))];
  };
  overlay.onpointerdown = (e) => {
    if (!s.asset || e.button!==0) return; const pos=normalizedPoint(e); if (!pos) return;
    const group=e.target.closest("[data-box]"), handle=e.target.closest("[data-handle]");
    if (group && !e.altKey) {
      s.selected=+group.dataset.box; paintTargets(); paintCanvas();
      if (!editable()) return;
      gesture={kind:handle ? "resize" : "move",handle:handle?.dataset.handle,start:pos,before:clone(s.targets),index:s.selected};
    } else if (editable() && s.tool==="draw") { gesture={kind:"draw",start:pos,before:clone(s.targets),draft:[...pos,...pos]}; s.selected=-1; }
    else { s.selected=-1; paintTargets(); paintCanvas(); return; }
    overlay.setPointerCapture(e.pointerId); e.preventDefault();
  };
  overlay.onpointermove = (e) => {
    if (!gesture) return; const pos=normalizedPoint(e); if (!pos) return;
    if (gesture.kind==="draw") gesture.draft=[Math.min(pos[0],gesture.start[0]),Math.min(pos[1],gesture.start[1]),Math.max(pos[0],gesture.start[0]),Math.max(pos[1],gesture.start[1])];
    else {
      const original=gesture.before[gesture.index].xyxy_norm, box=[...original], dx=pos[0]-gesture.start[0], dy=pos[1]-gesture.start[1];
      if (gesture.kind==="move") { const x=Math.max(-original[0],Math.min(1-original[2],dx)), y=Math.max(-original[1],Math.min(1-original[3],dy)); box[0]+=x;box[2]+=x;box[1]+=y;box[3]+=y; }
      else { const ex=Math.min(.002,(original[2]-original[0])/2),ey=Math.min(.002,(original[3]-original[1])/2);if (gesture.handle.includes("w")) box[0]=Math.max(0,Math.min(pos[0],box[2]-ex)); if (gesture.handle.includes("e")) box[2]=Math.min(1,Math.max(pos[0],box[0]+ex)); if (gesture.handle.includes("n")) box[1]=Math.max(0,Math.min(pos[1],box[3]-ey)); if (gesture.handle.includes("s")) box[3]=Math.min(1,Math.max(pos[1],box[1]+ey)); }
      s.targets[gesture.index].xyxy_norm=box; delete s.targets[gesture.index].confidence;
    }
    paintCanvas();
  };
  overlay.onpointerup = () => {
    if (!gesture) return;
    if (gesture.kind==="draw") { const b=gesture.draft; if (b[2]-b[0]>.003 && b[3]-b[1]>.003) { s.targets.push({label_id:s.classId,xyxy_norm:b}); s.selected=s.targets.length-1; } }
    if (JSON.stringify(gesture.before)!==JSON.stringify(s.targets)) pushUndo(gesture.before);
    gesture=null; dirtyCheck(); paintTargets(); paintCanvas();
  };
  overlay.onpointercancel = () => { if (gesture) { s.targets=gesture.before; gesture=null; paintCanvas(); paintTargets(); } };

  function paintEngines() {
    const select=$("#lab-engine",root), old=select.value;
    select.innerHTML=s.engines.map((e) => `<option value="${esc(e.id)}" ${e.available ? "" : "disabled"}>${esc(e.name)}${e.available ? "" : " · 不可用"}</option>`).join("") || '<option value="">暂无可用引擎</option>';
    if (s.engines.some((e) => e.id===old && e.available)) select.value=old;
    else { const compatible=s.engines.find((e) => e.available && s.project?.classes.every((name) => e.classes.includes(name))); if (compatible) select.value=compatible.id; }
    engineReason();
  }
  function engineReason() {
    const e=s.engines.find((value) => value.id===$("#lab-engine",root).value);
    const missing=s.project?.classes.filter((name) => !e?.classes?.includes(name)) || [];
    $("[data-engine-reason]",root).textContent=!e ? "没有可用模型时，可直接在画布手工标注。" : !e.available ? e.reason || "该引擎暂不可用" : missing.length ? `引擎不支持：${missing.join("、")}。可直接手工完成；要使用模型，可新建项目并点击“沿用类别”。` : `支持当前项目全部类别。${e.reason || ""}`;
    paintActions();
  }
  async function refreshTrial() {
    s.trialReady=false;
    if (s.trial?.ids?.length) {
      try {
        const [job,records]=await Promise.all([request(`/api/label-jobs/${key(s.trial.job_id)}`),Promise.all(s.trial.ids.map((id) => request(`/api/label-assets/${key(id)}/annotations`)))]);
        if (dead) return;
        const need=Math.min(2,job.total), belongs=job.project_id===s.project.id && s.trial.ids.every((id)=>(job.items || []).some((i)=>i.asset_id===id));
        s.trialReady=belongs && records.length===need && job.processed>=need && !["queued","running"].includes(job.status) && records.every((a)=>a.project_id===s.project.id && frozen(a));
        $("[data-trial-note]",root).textContent=s.trialReady ? "试跑图已人工确认。勾选图片后可批量预标注。" : `试跑处理 ${job.processed || 0} / ${need} · 人工确认 ${records.filter(frozen).length} / ${need}。完成后可批量运行。`;
      } catch(e) { $("[data-trial-note]",root).textContent=`试跑记录不可核对：${e.message}。请重新试跑。`; }
    } else $("[data-trial-note]",root).textContent="先试两张、逐张确认，再对勾选的图片批量运行。";
    $("[data-flow-trial]",root).classList.toggle("on",!!s.trial);
    $("[data-flow-batch]",root).classList.toggle("on",s.trialReady);
    paintActions();
  }
  function jobHtml(j, cancel = false) {
    const value=Math.max(0,Math.min(1,j.total ? j.processed/j.total : 0));
    return `<div class="label-job"><div><b>${JOB[j.status] || esc(j.status)}</b><span class="num">${j.processed || 0} / ${j.total || 0}</span></div><progress max="1" value="${value}"></progress><p>${j.suggested || 0} 张建议 · ${j.no_targets || 0} 张未检出 · ${j.failed || 0} 张失败 · ${j.skipped || 0} 张跳过</p>${cancel && canWrite && ["queued","running"].includes(j.status) ? `<button class="label-link" data-cancel-job="${esc(j.id)}">取消未完成部分</button>` : ""}</div>`;
  }
  async function refreshJobs() {
    if (!s.project || dead) return;
    const pid=s.project.id, jobs=listOf(await request(`/api/label-jobs?project_id=${key(pid)}`));
    if (dead || s.project?.id!==pid) return;
    const before=s.jobs.find((j) => ["queued","running"].includes(j.status)); s.jobs=jobs;
    s.currentJob=jobs[0] || null;
    $("[data-current-job]",root).innerHTML=s.currentJob ? jobHtml(s.currentJob,true) : "";
    root.querySelectorAll("[data-cancel-job]").forEach((b) => b.onclick=() => cancelJob(b.dataset.cancelJob));
    paintAssets(); paintActions();
    const terminalChanged=s.currentJob && !["queued","running"].includes(s.currentJob.status) && s.finishedJobId!==s.currentJob.id;
    if (terminalChanged || (before && !jobs.some((j) => j.id===before.id && ["queued","running"].includes(j.status)))) {
      if(s.currentJob)s.finishedJobId=s.currentJob.id;
      await loadAssets(); await refreshQuality();
      if (s.asset && !s.dirty && !gesture && !frozen(s.asset)) await loadAsset(s.asset.id,true);
      else if (s.dirty) notice("预标注任务已结束。本地未保存草稿已保留，未自动替换画布。");
      await refreshTrial();
    }
  }
  async function startJob(trial) {
    if (!s.project || !canWrite) return;
    const eligible=s.assets.filter((a) => !frozen(a)), chosen=eligible.filter((a) => s.selectedIds.has(a.id));
    const ids=trial ? (chosen.length>=Math.min(2,eligible.length) ? chosen : eligible).slice(0,2).map((a) => a.id) : [...s.selectedIds].filter((id) => !s.assets.some((a) => a.id===id && frozen(a)));
    if ((trial && !ids.length) || (!trial && (!s.trialReady || !ids.length))) return;
    const threshold=+$("#lab-threshold",root).value;
    if (!Number.isFinite(threshold) || threshold<.05 || threshold>.95) return toast("阈值需要在 0.05 到 0.95 之间","error");
    const body={project_id:s.project.id,asset_ids:ids,engine_id:$("#lab-engine",root).value,threshold};
    const fingerprint=JSON.stringify(body); if (!pendingJob || pendingJob.fingerprint!==fingerprint) pendingJob={fingerprint,key:crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`};
    s.busy=true; paintActions();
    try {
      const j=await request("/api/label-jobs",json("POST",{...body,idempotency_key:pendingJob.key})); pendingJob=null;
      if (trial) { s.trial={ids,job_id:j.id}; sessionStorage.setItem(`stepeye.labelTrial.${s.project.id}`,JSON.stringify(s.trial)); await refreshTrial(); }
      s.selectedIds.clear(); await refreshJobs(); toast(trial ? "两张试跑已提交，请等待完成后逐张复核" : "批量预标注已提交");
    } catch (e) { failure(e); }
    finally { s.busy=false; paintActions(); }
  }
  async function cancelJob(id) { try { await request(`/api/label-jobs/${key(id)}/cancel`,json("POST",{})); await refreshJobs(); toast("已请求取消，已完成结果仍保留"); } catch (e) { failure(e); } }

  async function refreshQuality() {
    if (!s.project) return;
    try {
      const report=await request(projectPath("/quality")); if (dead) return;
      $("[data-quality-mini]",root).innerHTML=`<span>已确认 <b class="num">${report.confirmed_total ?? "—"}</b></span><span>待复核 <b class="num">${report.unconfirmed ?? "—"}</b></span><span>图片总数 <b class="num">${report.total ?? "—"}</b></span><span>缺许可引用 <b class="num">${report.missing_license ?? "—"}</b></span>`;
    } catch (e) { if (!dead) $("[data-quality-mini]",root).textContent=`统计暂不可用：${e.message}`; }
  }
  async function selectProject(id) {
    if (s.dirty && !window.confirm("当前图片有未保存修改，放弃并切换项目？")) { $("#lab-project",root).value=s.project.id; return; }
    const my=++loadSeq; assetSeq++; s.project=s.projects.find((p) => p.id===id); s.asset=null; s.targets=[];s.dirty=false;s.offset=0;s.selectedIds.clear();s.jobs=[];s.selected=-1;s.classId=0;s.undo=[];s.redo=[];s.baseline="[]";s.assetLoading=false;s.finishedJobId=null;
    wrap.hidden=true; $("[data-canvas-empty]",root).hidden=false; overlay.innerHTML="";
    $("[data-image-name]",root).textContent="选择一张图片开始";$("[data-image-meta]",root).textContent="";$("[data-image-state]",root).textContent="";
    try { s.trial=JSON.parse(sessionStorage.getItem(`stepeye.labelTrial.${id}`) || "null"); } catch { s.trial=null; }
    paintClasses(); paintTargets(); paintEngines(); paintActions();
    if (!s.project) { paintAssets(); return; }
    try { await loadAssets(true); if (dead || my!==loadSeq) return; await Promise.all([refreshJobs(),refreshQuality(),refreshTrial()]); }
    catch (e) { failure(e); }
  }
  async function loadProjects(preferred = "") {
    s.projects=listOf(await request("/api/label-projects")); if (dead) return;
    const id=s.projects.some((p) => p.id===preferred) ? preferred : s.projects[0]?.id;
    $("#lab-project",root).innerHTML=s.projects.length ? s.projects.map((p) => `<option value="${esc(p.id)}" ${p.id===id ? "selected" : ""}>${esc(p.name)}</option>`).join("") : '<option value="">暂无标注项目</option>';
    await selectProject(id);
  }

  function newProject() {
    if (!canWrite) return; drawer?.close();
    const templates=s.engines.filter((e)=>e.classes?.length);
    const d=drawer=openDrawer({title:"新建标注项目",html:`<form class="label-dialog-form" data-project-form><label>项目名称<input name="name" required maxlength="100" placeholder="例如：安全帽佩戴检测"></label>
      ${templates.length ? `<label>复用模型类别（可选）<div class="label-model-template"><select name="template_engine">${templates.map((e)=>`<option value="${esc(e.id)}">${esc(e.name)}</option>`).join("")}</select><button class="btn" type="button" data-fill-classes>沿用类别</button></div></label>` : ""}
      <label>类别名称<textarea name="classes" rows="4" required placeholder="每行一个类别，例如：&#10;划伤&#10;凹陷"></textarea></label><details><summary>高级：关联场景与来源许可</summary><div><label>关联场景<input name="scene_id" placeholder="场景 ID（可选）"></label><label>来源许可<input name="license_ref" placeholder="可核对的许可文件、合同或来源链接"></label></div></details><p>类别编号创建后固定。模型不支持新类别时，可直接手工框选。</p><button class="btn btn-primary" type="submit">创建项目</button></form>`});
    const form=$("form",d.body),fill=$("[data-fill-classes]",form);
    if(fill)fill.onclick=()=>{const engine=templates.find((e)=>e.id===form.elements.template_engine.value);if(engine){form.elements.classes.value=engine.classes.join("\n");toast(`已沿用 ${engine.classes.length} 个模型类别`);}};
    form.onsubmit=async(e)=>{e.preventDefault();const classes=form.elements.classes.value.split(/[\n,，]/).map((v)=>v.trim()).filter(Boolean);if(!classes.length||new Set(classes).size!==classes.length)return toast("类别需非空且不能重复","error");const b=$("button[type=submit]",form);b.disabled=true;try{const project=await request("/api/label-projects",json("POST",{name:form.elements.name.value.trim(),classes,scene_id:form.elements.scene_id.value.trim(),license_ref:form.elements.license_ref.value.trim()}));d.close();await loadProjects(project.id);toast("标注项目已创建");}catch(e){failure(e);b.disabled=false;}};
  }
  function upload() {
    if(!canWrite||!s.project)return;drawer?.close();
    const d=drawer=openDrawer({title:"导入项目图片",html:`<form class="label-dialog-form"><label>图片文件<input type="file" name="files" accept="image/png,image/jpeg,image/webp" multiple required></label><label class="label-check"><input type="checkbox" name="synthetic">这些图片是生成式／合成样本</label><p>合成样本会保留来源标记，不进入独立验证集。</p>
      <details><summary>高级：采集分组、光照与来源</summary><div><label>采集分组<input name="group_id" placeholder="例如：相机01-20261103-白班"></label><p>同一来源的相邻帧应使用同组，避免训练／验证泄漏。不填写时按原图哈希分组。</p><label>光照分桶<select name="illumination_bucket"><option value="unknown">待补充</option><option value="day">白天</option><option value="night">夜间</option><option value="lights_on">开灯</option><option value="lights_off">关灯</option><option value="glare">强反光</option><option value="backlight">逆光</option></select></label><label>来源许可<input name="license_ref" value="${esc(s.project.license_ref||"")}" placeholder="许可文件、合同或来源链接"></label></div></details><button class="btn btn-primary" type="submit">导入图片</button></form>`});
    const form=$("form",d.body);
    form.onsubmit=async(e)=>{e.preventDefault();const files=[...form.elements.files.files];if(files.length>100)return toast("单次最多导入100张，请分批上传","error");if(files.reduce((n,f)=>n+f.size,0)>100*1024*1024||files.some((f)=>f.size>20*1024*1024))return toast("每张图片需≤20MB，整批需≤100MB","error");const fd=new FormData();files.forEach((file)=>fd.append("files",file));for(const name of ["group_id","illumination_bucket","license_ref"])fd.append(name,form.elements[name].value.trim());fd.append("synthetic",String(form.elements.synthetic.checked));const b=$("button[type=submit]",form);b.disabled=true;b.textContent="正在导入并校验…";try{const out=await request(projectPath("/assets"),{method:"POST",body:fd});await loadAssets(true);await refreshQuality();const imported=out.items?.length||0,errors=out.errors||[];if(errors.length){d.body.querySelector(".label-upload-result")?.remove();form.insertAdjacentHTML("beforebegin",`<div class="label-upload-result"><b>导入 ${imported} 张，${errors.length} 张未导入</b>${errors.map((r)=>`<div>${esc(r.name)}：${esc(r.message)}</div>`).join("")}</div>`);form.elements.files.value="";b.disabled=false;b.textContent="继续导入";toast(imported?`已导入${imported}张，其余问题请查看列表`:"没有图片导入成功",imported?"info":"error");}else{d.close();toast(`已导入 ${imported} 张图片`);}}catch(e){failure(e);b.disabled=false;b.textContent="导入图片";}};
  }
  async function history() {
    if (!s.asset) return; drawer?.close();const d=drawer=openDrawer({title:"标注修订历史",subtitle:esc(s.asset.name),html:'<div class="skeleton-block" style="height:180px"></div>'});
    try {const revisions=listOf(await request(assetPath("/revisions")));if(dead)return;d.body.innerHTML=`<div class="label-history-list">${revisions.map((r) => {const revision=r.revision ?? r.id;return `<div><span><b class="num">r${esc(revision)}</b><small>${esc(r.producer || r.actor || "修订")} · ${r.created_at ? fmtTime(r.created_at) : ""}</small></span>${canWrite && revision!==s.asset.revision ? `<button class="btn btn-sm" data-restore="${esc(revision)}">恢复为新草稿</button>` : '<span class="label-muted">当前版本</span>'}</div>`;}).join("") || '<p>尚无历史修订</p>'}</div>`;d.body.querySelectorAll("[data-restore]").forEach((b) => b.onclick=async()=>{if(s.dirty&&!window.confirm("本地未保存修改将被放弃，继续恢复历史？"))return;b.disabled=true;try {await request(assetPath("/restore"),json("POST",{expected_revision:s.asset.revision,revision:+b.dataset.restore}));s.dirty=false;await loadAsset(s.asset.id,true);await loadAssets();await Promise.all([refreshQuality(),refreshTrial()]);d.close();toast("已从历史创建修订草稿");}catch(e){failure(e);b.disabled=false;}});}catch(e){d.body.textContent=e.message;}
  }
  async function showJobs() {
    drawer?.close();const d=drawer=openDrawer({title:"预标注任务",html:'<div class="skeleton-block" style="height:180px"></div>'});
    try {await refreshJobs();if(dead)return;d.body.innerHTML=s.jobs.length ? s.jobs.map((j)=>`<section class="label-job-history"><div class="label-muted mono">${esc(j.id)}</div>${jobHtml(j,true)}${(j.items||[]).filter((i)=>["failed","no_targets"].includes(i.status)).map((i)=>`<div class="label-job-issue"><button class="label-link" data-job-asset="${esc(i.asset_id)}">${esc(i.asset_id)} · ${i.status==="no_targets" ? "未检出目标" : "处理失败"}</button><p>${esc(i.error || "需要人工核对，未确认为空背景")}</p></div>`).join("")}</section>`).join("") : '<p class="label-muted">还没有预标注任务，可先试两张。</p>';d.body.querySelectorAll("[data-cancel-job]").forEach((b)=>b.onclick=()=>cancelJob(b.dataset.cancelJob));d.body.querySelectorAll("[data-job-asset]").forEach((b)=>b.onclick=()=>{d.close();loadAsset(b.dataset.jobAsset);});}catch(e){d.body.textContent=e.message;}
  }
  async function quality() {
    if(!s.project)return;drawer?.close();const d=drawer=openDrawer({title:"项目质量检查",html:'<div class="skeleton-block" style="height:180px"></div>'});
    try {
      const r=await request(projectPath("/quality"));if(dead)return;
      d.body.innerHTML=`<p class="label-help">这里只统计数据与来源完整性，不是模型准确率评测。${r.ready_for_export ? "已有人工确认数据，可创建数据版本。" : "需先人工确认图片，才能创建数据版本。"}</p>
        <div class="label-quality-grid">${[["图片总数",r.total],["已确认（含背景）",r.confirmed_total],["待复核",r.unconfirmed],["合成样本",r.synthetic],["缺许可引用",r.missing_license],["重复原图哈希组",r.duplicate_sha_groups]].map(([name,value])=>`<div>${name}<b class="num">${value??"—"}</b></div>`).join("")}</div>
        ${(r.warnings||[]).length ? `<ul class="label-quality-warnings">${r.warnings.map((v)=>`<li>${esc(v)}</li>`).join("")}</ul>` : ""}
        <h3 class="d-h">人工确认类别分布</h3><table class="label-quality-classes"><thead><tr><th>类别</th><th>图片</th><th>目标框</th></tr></thead><tbody>${(r.class_counts||[]).map((v)=>`<tr><td>${esc(v.label)}</td><td class="num">${v.images}</td><td class="num">${v.targets}</td></tr>`).join("")}</tbody></table>
        <h3 class="d-h">光照覆盖</h3><div class="label-history-list">${Object.entries(r.illumination_buckets||{}).map(([name,n])=>`<div><span>${esc(({day:"白天",night:"夜间",lights_on:"开灯",lights_off:"关灯",glare:"强反光",backlight:"逆光",unknown:"待补充"})[name]||name)}</span><b class="num">${n} 张</b></div>`).join("")||'<p class="label-muted">尚无图片</p>'}</div><details><summary class="label-link">查看完整检查数据</summary><pre class="label-report-json">${esc(JSON.stringify(r,null,2))}</pre></details>`;
    }catch(e){d.body.textContent=e.message;}
  }
  async function versions() {
    if(!s.project)return;drawer?.close();const d=drawer=openDrawer({title:"确认版与数据导出",html:'<div class="skeleton-block" style="height:180px"></div>'});
    const load=async()=>{
      try {
        const rows=listOf(await request(projectPath("/versions")));if(dead)return;
        d.body.innerHTML=`<p class="label-help">确认版固定已确认标签、来源分组与切分。后续草稿或自动任务不改变已有 ZIP；机器建议不进入导出。</p>
          ${canWrite ? '<form class="label-version-form"><label>验证集占比<input type="number" name="val_ratio" value=".2" min=".05" max=".5" step=".05"></label><label>切分种子<input type="number" name="seed" value="42" min="0" step="1"></label><button class="btn btn-primary" type="submit">创建不可变确认版</button></form>' : ""}
          <div class="label-history-list">${rows.map((v)=>`<div><span><b title="${esc(v.id)}">${esc(v.id)}</b><small>${v.created_at ? fmtTime(v.created_at) : ""} · ${v.asset_count} 张 · ${v.group_count} 个来源组</small><small>训练 ${v.train_count} / 验证 ${v.val_count} · 种子 ${v.seed}</small>${v.val_count===0 ? '<small class="label-version-warning">仅训练集：没有独立验证集，不能据此宣称模型已验证</small>' : ""}${(v.warnings||[]).map((warning)=>`<small class="label-version-warning">${esc(warning)}</small>`).join("")}<small class="mono" title="${esc(v.manifest_sha256 || v.sha256)}">清单哈希 ${esc((v.manifest_sha256 || v.sha256 || "").slice(0,16))}…</small></span><a class="btn btn-sm" href="${projectPath(`/versions/${key(v.id)}/export.zip`)}" download>导出 ZIP</a></div>`).join("") || '<p class="label-muted">尚未创建确认版。先在画布中确认图片，再创建数据版本。</p>'}</div>`;
        const form=$("form",d.body);if(form)form.onsubmit=async(e)=>{e.preventDefault();const b=$("button",form);b.disabled=true;b.textContent="正在固定确认数据…";try {await request(projectPath("/versions"),json("POST",{seed:+form.elements.seed.value,val_ratio:+form.elements.val_ratio.value}));await load();toast("不可变确认版已创建");}catch(e){failure(e);b.disabled=false;b.textContent="创建不可变确认版";}};
      }catch(e){d.body.textContent=e.message;}
    };await load();
  }

  $("#lab-project",root).onchange=(e)=>selectProject(e.target.value);
  $("#lab-create",root).onclick=newProject;$("#lab-upload",root).onclick=upload;
  $("#lab-state",root).onchange=()=>{s.offset=0;loadAssets().catch(failure);};
  $("#lab-refresh-assets",root).onclick=()=>loadAssets().catch(failure);
  $("#lab-prev-page",root).onclick=()=>{s.offset=Math.max(0,s.offset-100);loadAssets().catch(failure);};
  $("#lab-next-page",root).onclick=()=>{s.offset+=100;loadAssets().catch(failure);};
  $("#lab-select-page",root).onchange=(e)=>{for(const a of s.assets)if(!frozen(a))e.target.checked?s.selectedIds.add(a.id):s.selectedIds.delete(a.id);paintAssets();paintActions();};
  $("#lab-background",root).onchange=paintActions;
  $("#lab-save",root).onclick=()=>saveDraft().catch(failure);$("#lab-confirm",root).onclick=confirmAsset;
  $("#lab-undo",root).onclick=()=>undo();$("#lab-redo",root).onclick=()=>undo(true);
  root.querySelectorAll("[data-tool]").forEach((b)=>b.onclick=()=>{s.tool=b.dataset.tool;root.querySelectorAll("[data-tool]").forEach((n)=>n.classList.toggle("on",n===b));paintCanvas();});
  $("#lab-fit",root).onclick=()=>{s.zoom=1;fitImage();};$("#lab-zoom-in",root).onclick=()=>{s.zoom=Math.min(4,s.zoom*1.25);fitImage();};$("#lab-zoom-out",root).onclick=()=>{s.zoom=Math.max(.5,s.zoom/1.25);fitImage();};
  $("#lab-history",root).onclick=history;$("#lab-jobs",root).onclick=showJobs;$("#lab-quality",root).onclick=quality;$("#lab-versions",root).onclick=versions;
  $("#lab-engine",root).onchange=engineReason;$("#lab-trial",root).onclick=()=>startJob(true);$("#lab-batch",root).onclick=()=>startJob(false);
  image.onerror=()=>notice("图片读取失败。请刷新图片列表，或检查原始文件是否仍可访问。");
  const keyboard=(e)=>{if(dead || drawer?.el?.classList.contains("in") || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName))return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="s"){e.preventDefault();saveDraft().catch(failure);}else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="z"){e.preventDefault();undo(e.shiftKey);}else if(["Delete","Backspace"].includes(e.key)&&s.selected>=0){e.preventDefault();deleteSelected();}else if(e.key.toLowerCase()==="b"&&editable()){$("[data-tool=draw]",root).click();}else if(e.key.toLowerCase()==="v"){$("[data-tool=select]",root).click();}else if(/^[1-9]$/.test(e.key)&&+e.key<=s.project?.classes.length){s.classId=+e.key-1;paintClasses();}};
  document.addEventListener("keydown",keyboard);
  const unload=(e)=>{if(s.dirty){e.preventDefault();e.returnValue="";}};window.addEventListener("beforeunload",unload);
  const resize=new ResizeObserver(fitImage);resize.observe(viewport);
  const init=async()=>{try {const me=await request("/api/auth/me");if(dead)return;canWrite=["admin","operator"].includes(me.role);root.querySelectorAll("[data-write]").forEach((n)=>n.hidden=!canWrite);$("[data-role]",root).textContent=canWrite ? "人工确认后交付" : "只读模式";s.engines=listOf(await request("/api/label-engines"));await loadProjects(params.id || params.project || "");if(!dead)poll=setInterval(async()=>{if(polling||!s.project)return;polling=true;try {await refreshJobs();}catch(e){if(!dead)$("[data-current-job]",root).textContent=`任务同步失败：${e.message}`;}finally{polling=false;}},1800);}catch(e){notice(e.message);$("[data-asset-list]",root).innerHTML='<div class="label-assets-empty">标注服务暂不可用。请刷新页面重试。</div>';}};
  init();
  return()=>{dead=true;loadSeq++;assetSeq++;clearInterval(poll);drawer?.close();resize.disconnect();document.removeEventListener("keydown",keyboard);window.removeEventListener("beforeunload",unload);};
}

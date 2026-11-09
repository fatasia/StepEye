import { openReferenceCreator } from "./action-references.js";
import { api } from "../api.js";
import { icon } from "../icons.js";
import { emptyBlock, errorBlock, openLightbox, toast } from "../ui.js";
import { $, esc, setHTML } from "../util.js";

/** 时间轴标注页:上传视频 → 区间表(步骤/起止秒)→ 校验抽帧 → 画廊。矩形交互铁律:表格编辑,不做花哨拖拽。 */
export function render(root) {
  let referenceDrawer = null;
  root.innerHTML = `
    <section class="panel">
      <header class="panel-head"><div class="head-left"><h2>上传作业视频</h2><span class="muted">一段完整作业录像;在时间轴上按步骤分段,系统自动校验与抽帧</span></div></header>
      <div class="panel-body insp-upload">
        <div class="dropzone" id="t-drop" tabindex="0" role="button" aria-label="选择视频">${icon("camera", 20)}<span>选择视频(mp4/avi)</span></div>
        <input type="file" id="t-file" accept="video/mp4,video/x-msvideo" hidden>
        <label class="field" style="flex:1 1 200px"><span>项目名</span><input id="t-name" placeholder="如:东门岗作业示教" autocomplete="off"></label>
        <button class="btn btn-primary" id="t-go" disabled>${icon("check", 14)}创建标注项目</button>
      </div>
    </section>
    <section class="panel" id="t-editor" hidden>
      <header class="panel-head"><div class="head-left"><h2 data-r="title"></h2><span class="muted">时长 <span class="num" data-r="dur"></span>s · 区间需互不重叠且不越界</span></div>
        <button class="btn" id="t-add">${icon("check", 14)}加区间</button></header>
      <div class="panel-body">
        <div id="t-rows"></div>
        <div class="ez-actions"><button class="btn btn-primary" id="t-save">${icon("check", 14)}校验并抽帧</button>
          <a class="btn" id="t-video" target="_blank" hidden>打开视频</a></div>
        <div class="inline-empty muted" id="t-result" hidden></div>
      </div>
    </section>
    <section class="panel table-panel">
      <header class="panel-head"><div class="head-left"><h2>抽帧产物</h2><span class="muted">进入回归 manifest(标签=步骤 id)</span></div>
        <button class="btn" id="t-refresh">${icon("refresh", 14)}刷新</button></header>
      <div class="thumbs" id="t-gallery" style="padding:var(--s-3)"><div class="skeleton-rows"></div></div>
    </section>`;
  let file = null, current = null;

  const gallery = async () => {
    try {
      const rows = await api.timelineList();
      const latest = rows[0];
      if (!latest) { setHTML($("#t-gallery", root), emptyBlock("还没有抽帧产物", "上传视频并保存区间后,这里会显示抽出的样本帧。")); return; }
      const frames = latest.frames ?? [];
      setHTML($("#t-gallery", root), frames.map((frame) => `<button class="thumb" data-src="${esc(frame.url)}">
        <img src="${esc(frame.url)}" loading="lazy" onerror="this.parentElement.style.display='none'"><span>${esc(frame.label || frame.file)}</span></button>`).join("")
        || emptyBlock("暂无已登记抽帧", "保存区间并抽帧后，显示后端返回的真实产物。"));
      root.querySelectorAll("#t-gallery .thumb").forEach((t) => (t.onclick = () => openLightbox(t.dataset.src, t.dataset.cap || "")));
    } catch (e) {
      setHTML($("#t-gallery", root), errorBlock(e.message));
    }
  };

  const openEditor = async (rec) => {
    current = rec;
    $("#t-editor", root).hidden = false;
    $('[data-r="title"]', root).textContent = rec.name;
    $('[data-r="dur"]', root).textContent = rec.duration_s.toFixed(1);
    $("#t-video", root).href = api.timelineVideo(rec.id);
    $("#t-video", root).hidden = false;
    paintRows();
    gallery();
  };

  const paintRows = () => {
    const ivs = current.intervals ?? [];
    setHTML($("#t-rows", root), ivs.map((iv, i) => `<div class="ez-row" data-i="${i}">
      <input value="${esc(iv.step)}" data-f="step" style="max-width:120px" aria-label="步骤id">
      <input type="number" step="0.1" value="${iv.start}" data-f="start" aria-label="开始秒" style="width:90px">
      <span class="muted">→</span>
      <input type="number" step="0.1" value="${iv.end}" data-f="end" aria-label="结束秒" style="width:90px">
      <button class="btn btn-sm" data-reference="${i}">动作卡</button><button class="icon-btn" data-del="${i}" aria-label="删除">${icon("cross", 14)}</button></div>`).join("")
      || '<div class="inline-empty muted">还没有区间:点「加区间」,按 秒 填步骤起止</div>');
    $("#t-rows", root).onchange = (e) => {
      const inp = e.target.closest("[data-f]");
      if (!inp) return;
      const i = +inp.closest("[data-i]").dataset.i, f = inp.dataset.f;
      current.intervals[i][f] = f === "step" ? inp.value.trim() : +inp.value;
    };
    $("#t-rows", root).onclick = (e) => {
      const reference = e.target.closest("[data-reference]");
      if (reference) {
        referenceDrawer?.close();
        referenceDrawer = openReferenceCreator(current.id, Number(reference.dataset.reference));
        return;
      }
      const del = e.target.closest("[data-del]");
      if (del) { current.intervals.splice(+del.dataset.del, 1); paintRows(); }
    };
  };

  $("#t-drop", root).onclick = () => $("#t-file", root).click();
  $("#t-drop", root).onkeydown = (e) => { if (e.key === "Enter") $("#t-file", root).click(); };
  $("#t-file", root).onchange = (e) => {
    file = e.target.files[0] ?? null;
    $("#t-drop", root).querySelector("span").textContent = file ? file.name : "选择视频(mp4/avi)";
    $("#t-go", root).disabled = !file || !$("#t-name", root).value.trim();
  };
  $("#t-name", root).oninput = (e) => { $("#t-go", root).disabled = !file || !e.target.value.trim(); };
  $("#t-go", root).onclick = async () => {
    $("#t-go", root).disabled = true;
    try {
      const rec = await api.timelineCreate($("#t-name", root).value.trim(), file);
      toast("已创建,时长 " + rec.duration_s.toFixed(1) + "s", "success");
      await openEditor(rec);
    } catch (e) { toast(e.message, "error"); } finally { $("#t-go", root).disabled = false; }
  };
  $("#t-add", root).onclick = () => {
    current.intervals = current.intervals ?? [];
    const last = current.intervals[current.intervals.length - 1];
    const s = last ? Math.min(+last.end, current.duration_s) : 0;
    current.intervals.push({ step: `S${current.intervals.length + 1}`, start: s, end: Math.min(s + 2, current.duration_s) });
    paintRows();
  };
  $("#t-save", root).onclick = async () => {
    try {
      const out = await api.timelineIntervals(current.id, current.intervals);
      const el = $("#t-result", root);
      el.hidden = false;
      el.innerHTML = `校验通过,抽出 <b>${out.extracted}</b> 帧 → 回归 manifest(标签=步骤 id)`;
      toast(`抽出 ${out.extracted} 帧`, "success");
      gallery();
    } catch (e) { toast(e.message, "error"); }
  };
  $("#t-refresh", root).onclick = gallery;
  gallery();
  return () => { referenceDrawer?.close(); };
}

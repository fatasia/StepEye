import { api } from "../api.js";
import { icon } from "../icons.js";
import { emptyBlock, errorBlock, openLightbox, toast } from "../ui.js";
import { $, esc, setHTML } from "../util.js";

/** 样本库:上传参考图 → 选引擎批量生成 → 标注继承;manifest 画廊 + 诚实提示。 */
export function render(root) {
  root.innerHTML = `
    <section class="panel" style="margin-bottom:var(--s-3)"><header class="panel-head"><div class="head-left"><h2>图片标注工作台</h2><span class="muted">导入图片 → 先试两张 → 人工确认 → 导出可训练数据</span></div><a class="btn btn-primary" href="#/labeling">打开标注工作台</a></header></section>
    <section class="panel">
      <header class="panel-head"><div class="head-left"><h2>样本生成</h2><span class="muted">参考图 + 描述 → 批量带标注样本;标注继承自所选标签</span></div></header>
      <div class="panel-body insp-upload">
        <div class="dropzone" id="s-drop" tabindex="0" role="button" aria-label="选择参考图">${icon("camera", 20)}<span>选择参考图</span></div>
        <input type="file" id="s-file" accept="image/jpeg,image/png" hidden>
        <label class="field"><span>标签</span><select id="s-label"><option>OK</option><option>NG</option></select></label>
        <div class="seg-ctl" role="group" aria-label="引擎"><button class="on" data-v="augment">增强合成(秒级)</button><button data-v="cogview">生成式(慢)</button></div>
        <label class="field"><span>数量</span><input id="s-n" type="number" value="10" min="1" max="100" style="width:72px"></label>
        <input id="s-desc" class="field" style="flex:1 1 220px" placeholder="描述,如:表面出现刮痕(生成式用)" autocomplete="off">
        <button class="btn btn-primary" id="s-go" disabled>${icon("check", 14)}生成样本</button>
        <div class="inline-empty muted" id="s-note" hidden></div>
      </div>
    </section>
    <section class="panel table-panel">
      <header class="panel-head"><div class="head-left"><h2>样本清单</h2><span class="muted">最近生成的样本(含来源与变换参数;生成式样本仅冷启动/调试用,不进验收集)</span></div>
        <button class="btn" id="s-refresh">${icon("refresh", 14)}刷新</button></header>
      <div class="thumbs" id="s-gallery" style="padding:var(--s-3)"><div class="skeleton-rows"></div></div>
    </section>`;
  let refFile = null, engine = "augment";
  const gallery = $("#s-gallery", root), note = $("#s-note", root), go = $("#s-go", root);

  const loadManifest = async () => {
    try {
      const rows = (await api.sampleManifest(60)).filter((r) => r.file);
      if (!rows.length) { gallery.innerHTML = emptyBlock("还没有样本", "上传参考图生成第一批。"); return; }
      gallery.innerHTML = rows.map((r) => `<button class="thumb" data-src="${esc(api.sampleImage(r.file))}" data-cap="${esc(r.label)}·${esc(r.engine)}">
        <img src="${esc(api.sampleImage(r.file))}" alt="${esc(r.label)}" loading="lazy"><span>${esc(r.label)} · ${esc(r.engine)}</span></button>`).join("");
      gallery.querySelectorAll(".thumb").forEach((t) => (t.onclick = () => openLightbox(t.dataset.src, t.dataset.cap)));
    } catch (e) {
      gallery.innerHTML = errorBlock(e.message);
      const rb = gallery.querySelector("[data-retry]");
      if (rb) rb.onclick = loadManifest;
    }
  };
  const submit = async () => {
    go.disabled = true;
    try {
      const n = Math.max(1, Math.min(engine === "cogview" ? 8 : 100, +$("#s-n", root).value || 10));
      const out = await api.sampleGenerate(refFile, $("#s-label", root).value, engine, n, $("#s-desc", root).value);
      toast(`已生成 ${out.generated}/${out.rows.length} 张,标注继承「${$("#s-label", root).value}」`, "success");
      refFile = null; $("#s-file", root).value = ""; go.disabled = true;
      loadManifest();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      go.disabled = !refFile;
    }
  };
  $("#s-drop", root).onclick = () => $("#s-file", root).click();
  $("#s-drop", root).onkeydown = (e) => { if (e.key === "Enter") $("#s-file", root).click(); };
  $("#s-file", root).onchange = (e) => {
    refFile = e.target.files[0] ?? null;
    $("#s-drop", root).querySelector("span").textContent = refFile ? refFile.name : "选择参考图";
    go.disabled = !refFile;
  };
  root.querySelector(".seg-ctl").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    engine = b.dataset.v;
    root.querySelectorAll(".seg-ctl button").forEach((x) => x.classList.toggle("on", x === b));
    note.hidden = engine !== "cogview";
    note.textContent = "生成式引擎每张 5~15 秒,单批 ≤8 张;样本与真实产线有差距,仅用于冷启动/提示词调试。";
  };
  go.onclick = submit;
  $("#s-refresh", root).onclick = loadManifest;
  loadManifest();
  return () => {};
}

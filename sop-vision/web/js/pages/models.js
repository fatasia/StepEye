import { renderSegmentationResult } from "./model-segmentation-result.js";
import { openQualityTools } from "./quality-tools.js";
import { icon } from "../icons.js";
import { $, esc } from "../util.js";

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(typeof body.detail === "string" ? body.detail : `请求失败（HTTP ${response.status}）`);
  return body;
}

export function render(root) {
  root.innerHTML = `<div class="models-workspace">
    <header class="models-head"><div><span class="models-kicker">视觉应用 / 从现有模型开始</span><h2>试效果、建项目，在一个入口开始。</h2><p>选择模型 → 试一张 → 核对结果。检测可建标注项目，分类看概率，分割看区域。</p></div><div><button class="btn" data-quality-tools>评测与质量工具</button> <button class="btn" data-model-refresh>${icon("refresh", 14)}刷新模型</button></div></header>
    <div class="models-layout"><aside class="models-list" aria-label="从现有模型开始" data-model-list><p>正在读取模型…</p></aside>
      <section class="models-detail"><header data-model-heading></header>
        <div class="models-alert" role="alert" data-model-error hidden></div>
        <div class="models-actions" data-model-actions hidden><label class="btn" data-model-file-label>${icon("camera", 14)}选择图片<input data-model-file type="file" accept="image/png,image/jpeg,image/webp" hidden></label><button class="btn btn-primary" data-model-trial disabled>试一张</button><span data-model-file-name>图片仅用于本次试跑，不存入项目</span></div>
        <div class="models-preview" data-model-preview><div class="models-empty" data-model-empty>${icon("camera", 40)}<h3>用一张真实图片看看效果</h3><p>检测显示目标框，分类显示概率，分割显示掩膜。</p></div><div class="models-image" data-model-image hidden><img alt="试跑图片" data-model-img><svg data-model-overlay role="img" aria-label="检测结果"></svg></div></div>
        <div class="models-result" data-model-result aria-live="polite">还没有试跑结果</div>
        <section class="models-next" data-model-next hidden><div><h3>开始你的标注项目</h3><p>模型与类别自动带入。导入图片或已有 YOLO ZIP，复核后导出确认版。</p></div><button class="btn btn-primary" data-model-project>用于新项目标注</button><div class="models-existing" data-model-existing hidden><label>我的样本项目 · 补充数据<select data-model-existing-select aria-label="已有同类别项目"></select></label><button class="btn" data-model-open-project>打开项目</button><p>补充样本不会自动训练或替换当前模型。</p></div></section>
        <details class="models-metadata"><summary>模型信息与试跑边界</summary><div data-model-metadata></div><p>单图试跑不代表准确率或持续实时吞吐。试跑使用 CPU，任务结束即释放进程。生产效果需用目标场景独立数据评测。</p></details>
      </section></div></div>`;
  // 路由离开后忽略异步结果，并释放浏览器持有的本地图片 URL。
  let dead = false,
    models = [],
    selected = null,
    projects = [],
    canWrite = false,
    busy = false,
    file = null,
    objectUrl = null;
  const error = $("[data-model-error]", root),
    picker = $("[data-model-file]", root);
  const showError = (message) => {
    error.textContent = message;
    error.hidden = !message;
  };
  const releaseImage = () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  };
  function actions() {
    const allowed = !!selected?.available && canWrite;
    $("[data-model-actions]", root).hidden = !canWrite;
    $("[data-model-next]", root).hidden = !canWrite || ["classify", "segment"].includes(selected?.task);
    picker.disabled = !allowed || busy;
    $("[data-model-file-label]", root).classList.toggle("disabled", picker.disabled);
    $("[data-model-trial]", root).disabled = !allowed || busy || !file;
    $("[data-model-project]", root).disabled = !selected || ["classify", "segment"].includes(selected.task) || !canWrite || busy;
    $("[data-model-refresh]", root).disabled = busy;
    root.querySelectorAll("[data-model-id]").forEach((el) => (el.disabled = busy));
  }
  function select(id) {
    selected = models.find((m) => m.id === id);
    if (!selected) return;
    file = null;
    picker.value = "";
    releaseImage();
    showError("");
    $("[data-model-image]", root).hidden = true;
    $("[data-model-empty]", root).hidden = false;
    $("[data-model-overlay]", root).innerHTML = "";
    $("[data-model-file-name]", root).textContent = "图片仅用于本次试跑，不存入项目";
    $("[data-model-result]", root).textContent = "还没有试跑结果";
    root.querySelectorAll("[data-model-id]").forEach((el) => {
      const on = el.dataset.modelId === id;
      el.classList.toggle("on", on);
      el.setAttribute("aria-pressed", String(on));
    });
    $("[data-model-heading]", root).innerHTML =
      `<div class="models-detail-title"><h3>${esc(selected.name)}</h3><span class="models-status ${selected.available ? "available" : "unavailable"}">${selected.available ? "可试跑" : "不可用"}</span></div><p>${selected.available ? (selected.usage === "evaluation_only" ? "仅供评估 · 许可待审核，尚未通过生产验收。" : "已登记的本机视觉模型，场景效果待独立验证。") : esc(selected.reason || "模型尚未准备好")}</p><div class="models-classes">${selected.classes.map((c) => `<span>${esc(c)}</span>`).join("")}</div>`;
    $("[data-model-metadata]", root).innerHTML =
      `<dl><dt>模型 ID</dt><dd>${esc(selected.id)}</dd><dt>任务 / 运行时</dt><dd>${esc(selected.task || "detect")} / ${esc(selected.runtime || "onnx")}</dd><dt>使用范围</dt><dd>${selected.usage === "evaluation_only" ? "仅供评估，商用许可待审核" : "按已登记模型许可使用"}</dd><dt>许可记录</dt><dd>${esc(selected.license_status || "未登记")}</dd><dt>生产评测</dt><dd>尚未验收</dd><dt>类别数</dt><dd>${selected.classes.length}</dd></dl>`;
    const compatible = projects.filter(
      (p) =>
        p.classes.length === selected.classes.length && p.classes.every((c) => selected.classes.includes(c)),
    );
    $("[data-model-existing]", root).hidden = !compatible.length;
    $("[data-model-existing-select]", root).innerHTML = compatible
      .map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`)
      .join("");
    actions();
  }
  // 目录查询不启动推理进程；只有显式试跑才向后端提交图片。
  async function load() {
    showError("");
    busy = true;
    actions();
    try {
      const [me, rows, existing] = await Promise.all([
        request("/api/auth/me"),
        request("/api/models"),
        request("/api/label-projects"),
      ]);
      if (dead) return;
      canWrite = ["admin", "operator"].includes(me.role);
      models = rows;
      projects = existing;
      $("[data-model-list]", root).innerHTML = models.length
        ? models
            .map(
              (m) =>
                `<button data-model-id="${esc(m.id)}" aria-pressed="false"><span class="models-kicker">${m.available ? "本机已登记" : "待准备"}</span><b>${esc(m.name)}</b><span class="models-list-meta">${m.task === "segment" ? "区域分割" : m.task === "classify" ? "整图分类" : "目标检测"} · ${m.classes.length} 个类别 · ${esc(m.runtime || "ONNX")}</span><span class="models-status ${m.available ? "available" : "unavailable"}">${m.available ? "可试跑" : "不可用"}</span></button>`,
            )
            .join("")
        : '<div class="models-no-model"><h3>还没有可选模型</h3><p>管理员登记本机模型后会出现在这里。没有模型也可以先手工标注。</p><a class="btn" href="#/labeling">打开标注工作台</a></div>';
      root
        .querySelectorAll("[data-model-id]")
        .forEach((el) => (el.onclick = () => select(el.dataset.modelId)));
      if (models.length)
        select(
          models.some((m) => m.id === selected?.id)
            ? selected.id
            : (models.find((m) => m.available) || models[0]).id,
        );
      else {
        selected = null;
        $("[data-model-heading]", root).textContent = "尚无模型";
      }
    } catch (e) {
      if (!dead) showError(`模型读取失败：${e.message}`);
    } finally {
      busy = false;
      if (!dead) actions();
    }
  }
  // 换图先撤销旧 URL 和旧框，避免把上一张的结果覆盖到新图片。
  picker.onchange = () => {
    file = picker.files[0] || null;
    showError("");
    releaseImage();
    $("[data-model-overlay]", root).innerHTML = "";
    $("[data-model-result]", root).textContent = "图片已选择，点击试一张开始";
    if (file && file.size > 20 * 1024 * 1024) {
      file = null;
      picker.value = "";
      showError("单张图片不能超过20MB");
    }
    $("[data-model-file-name]", root).textContent = file?.name || "图片仅用于本次试跑，不存入项目";
    $("[data-model-image]", root).hidden = !file;
    $("[data-model-empty]", root).hidden = !!file;
    if (file) {
      objectUrl = URL.createObjectURL(file);
      $("[data-model-img]", root).src = objectUrl;
    }
    actions();
  };
  $("[data-model-trial]", root).onclick = async () => {
    if (!file || !selected?.available || busy || !canWrite) return;
    busy = true;
    actions();
    showError("");
    const button = $("[data-model-trial]", root);
    button.textContent = "正在推理…";
    $("[data-model-img]", root).src = objectUrl;
    $("[data-model-overlay]", root).innerHTML = "";
    $("[data-model-result]", root).textContent = "正在启动本机推理进程…";
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await request(`/api/models/${encodeURIComponent(selected.id)}/trial`, {
        method: "POST",
        body: form,
      });
      if (dead) return;
      const svg = $("[data-model-overlay]", root);
      if (result.task === "segment") {
        renderSegmentationResult(root, result, objectUrl);
        return;
      }
      if (result.task === "classify") {
        // Classification has no boxes and cannot create a detection-label project.
        svg.innerHTML = "";
        $("[data-model-result]", root).innerHTML =
          `<b>分类预测：${esc(result.predicted_label)} · ${(Number(result.confidence) * 100).toFixed(1)}%</b><span>本次端到端耗时 ${Number(result.elapsed_ms).toFixed(0)} ms</span>
          <div style="width:100%;display:grid;gap:8px" data-class-probabilities>${result.probabilities.map(p => `<div style="display:grid;grid-template-columns:minmax(80px,1fr) 2fr 65px;gap:12px;align-items:center"><span>${esc(p.label)}</span><progress value="${Math.max(0, Math.min(1, Number(p.score)))}" max="1" style="width:100%;accent-color:var(--accent)"></progress><span>${(Number(p.score) * 100).toFixed(1)}%</span></div>`).join("")}</div>
          <small>概率是模型输出；预测 OK 不代表已验收或产品合格，请结合适用场景复核。</small>`;
        return;
      }
      svg.setAttribute("viewBox", `0 0 ${result.width} ${result.height}`);
      const scale = Math.max(12, result.width / 55);
      svg.innerHTML = result.targets
        .map((t) => {
          const [x0, y0, x1, y1] = t.xyxy_norm;
          const x = x0 * result.width,
            y = y0 * result.height;
          return `<g><rect x="${x}" y="${y}" width="${(x1 - x0) * result.width}" height="${(y1 - y0) * result.height}"/><text x="${x + 4}" y="${Math.max(scale, y - 5)}" font-size="${scale}">${esc(result.classes[t.label_id] || "目标")} ${Math.round(t.confidence * 100)}%</text></g>`;
        })
        .join("");
      $("[data-model-result]", root).innerHTML =
        `<b>${result.targets.length ? `检出 ${result.targets.length} 个目标` : "未检出目标"}</b><span>本次端到端耗时 ${Number(result.elapsed_ms).toFixed(0)} ms · CPU</span><small>${result.targets.length ? "请核对漏检、误检与框的位置。" : "未检出不代表图片没有目标，请核对类别与拍摄条件。"}</small>`;
    } catch (e) {
      if (!dead) {
        showError(e.message);
        $("[data-model-result]", root).textContent = "本次检测未完成";
      }
    } finally {
      busy = false;
      if (!dead) {
        button.textContent = "试一张";
        actions();
      }
    }
  };
  $("[data-model-project]", root).onclick = async () => {
    if (!selected || ["classify", "segment"].includes(selected.task) || busy || !canWrite) return;
    busy = true;
    actions();
    showError("");
    try {
      const project = await request(`/api/models/${encodeURIComponent(selected.id)}/project`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!dead)
        location.hash = `#/labeling?project=${encodeURIComponent(project.id)}&engine=${encodeURIComponent(selected.id)}`;
    } catch (e) {
      if (!dead) showError(e.message);
    } finally {
      busy = false;
      if (!dead) actions();
    }
  };
  $("[data-model-open-project]", root).onclick = () => {
    const id = $("[data-model-existing-select]", root).value;
    if (id && !["classify", "segment"].includes(selected?.task))
      location.hash = `#/labeling?project=${encodeURIComponent(id)}&engine=${encodeURIComponent(selected.id)}`;
  };
  $("[data-quality-tools]", root).onclick = () => openQualityTools({ canWrite });
  $("[data-model-refresh]", root).onclick = load;
  load();
  return () => {
    dead = true;
    releaseImage();
  };
}

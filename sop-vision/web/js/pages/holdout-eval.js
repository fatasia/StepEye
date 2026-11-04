import { openDrawer } from "../ui.js";
import { $, esc, fmtTime } from "../util.js";

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(typeof body.detail === "string" ? body.detail : `请求失败（HTTP ${response.status}）`);
  return body;
}

const number = (v) => (v == null ? "—" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 4 }));
const pct = (v) => (v == null ? "—" : (Number(v) * 100).toFixed(1) + "%");

function download(value, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 固定模型 × 冻结数据版本的留出评测；同一实现也提供 CLI。发起前需要确认版数据集。 */
export function openHoldoutEvaluation({ canWrite }) {
  let closed = false, options = null, busy = false, last = null;
  const drawer = openDrawer({
    title: "固定模型评测",
    subtitle: "绑定模型与冻结数据版本 SHA → 确认版验证集逐图 FP/FN",
    html: `<style>.holdout label{display:block;margin:16px 0 8px}.holdout select,.holdout input{max-width:100%;width:100%;box-sizing:border-box;padding:9px;background:transparent;color:inherit;border:1px solid #364348;border-radius:5px}.holdout .he-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}.holdout .he-grid div{padding:14px;border:1px solid #364348;border-radius:6px}.holdout .he-grid strong{display:block;font-size:22px;margin-top:8px}.holdout table{width:100%;border-collapse:collapse;font-size:12px;margin:10px 0}.holdout th,.holdout td{border-bottom:1px solid #2a3438;padding:6px 8px;text-align:left}.holdout .he-error{color:#ff9890}.holdout .he-actions{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.holdout p{line-height:1.7}.holdout pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}</style>
      <div class="holdout">
        <p>用已登记模型对本项目<strong>确认版冻结数据集</strong>的 val 分组做单工作点评测：模型与数据 ZIP 都按 SHA 绑定，报告含逐图 FP/FN。这是导出数据上的软件协议验证，不代表独立未见数据或现场验收，也不产生发布许可。</p>
        <div data-he-write ${canWrite ? "" : "hidden"}>
          <label>检测模型<select data-he-model aria-label="检测模型"></select></label>
          <label>样本项目<select data-he-project aria-label="样本项目"></select></label>
          <label>冻结数据版本（val 分组）<select data-he-version aria-label="数据版本"></select></label>
          <p data-he-meta class="muted"></p>
          <label>输出适配器<select data-he-adapter><option value="yolo_raw">yolo_raw（原始布局）</option><option value="yolo_e2e">yolo_e2e（端到端）</option><option value="kairos_rtdetr">Kairos RT-DETR</option><option value="glue_yolov4">胶路 YOLOv4-Tiny</option></select></label>
          <div class="he-grid2" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <label>置信度阈值<input data-he-conf type="number" step="0.01" min="0.01" max="1" value="0.25"></label>
            <label>IoU 阈值<input data-he-iou type="number" step="0.01" min="0.01" max="1" value="0.5"></label>
          </div>
          <div class="he-actions"><button class="btn btn-primary" data-he-run disabled>开始评测</button><span class="muted">确认版数据 ≤500 张有效图；评测在本机 CPU 运行，可能持续数分钟。</span></div>
        </div>
        <p data-he-error class="he-error" role="alert"></p>
        <section data-he-result aria-live="polite">尚未发起评测。</section>
        <button class="btn" data-he-export hidden>导出完整 JSON</button>
        <details open><summary>历史报告</summary><div data-he-history>正在读取…</div></details>
      </div>`,
    onClose: () => { closed = true; },
  });
  const body = drawer.body, error = $("[data-he-error]", body), result = $("[data-he-result]", body);
  const showError = (e) => { if (!closed) error.textContent = e?.message || ""; };

  function sync() {
    const ready = !!$("[data-he-version]", body)?.value && !busy;
    const run = $("[data-he-run]", body);
    if (run) {
      run.disabled = !ready;
      run.textContent = busy ? "正在评测…" : "开始评测";
    }
  }

  function render(record) {
    last = record;
    const ev = record.evaluation, s = ev.summary, classes = ev.classes || {};
    $("[data-he-result]", body).innerHTML = `
      <h3>${esc(record.engine_name || "评测报告")} × ${esc(record.dataset_version_id || record.version_id)}</h3>
      <div class="he-grid">
        <div>验证帧<strong>${number(ev.frames)}</strong></div>
        <div>Precision<strong>${pct(s.precision)}</strong></div>
        <div>Recall<strong>${pct(s.recall)}</strong></div>
        <div>F1<strong>${s.f1 == null ? "—" : Number(s.f1).toFixed(3)}</strong></div>
        <div>误检框 FP<strong>${number(s.fp)}</strong></div>
        <div>漏检框 FN<strong>${number(s.fn)}</strong></div>
      </div>
      <p class="muted">误检帧 ${number(ev.false_positive_frames)} · 漏检帧 ${number(ev.missed_frames)} · 工作点 IoU ${ev.iou_threshold} / 置信度 ${ev.confidence_threshold} · 耗时 ${number(record.elapsed_ms)} ms · ${esc(record.adapter)}</p>
      ${ev.average_precision ? `<p class="muted">AP@0.5 ${pct(ev.average_precision.ap50 ?? ev.average_precision["AP@0.5"])} · AP@0.5:0.95 ${pct(ev.average_precision.ap50_95 ?? ev.average_precision["AP@0.5:0.95"])}（导出 val 上计算，非独立未见数据）</p>` : ""}
      ${Object.keys(classes).length ? `<table><thead><tr><th>类别</th><th>TP</th><th>FP</th><th>FN</th><th>Precision</th><th>Recall</th></tr></thead><tbody>${Object.entries(classes).map(([name, c]) => `<tr><td>${esc(name)}</td><td>${c.tp}</td><td>${c.fp}</td><td>${c.fn}</td><td>${pct(c.precision)}</td><td>${pct(c.recall)}</td></tr>`).join("")}</tbody></table>` : ""}
      <p class="muted">排除（合成/未确认）${number((record.excluded || []).length)} 张；完整逐图 FP/FN 明细见导出 JSON。</p>
      <details><summary>逐图 FP/FN（前 80 行）</summary><table><thead><tr><th>图片</th><th>误检框</th><th>漏检框</th><th>低置信忽略</th></tr></thead><tbody>${(ev.details || []).slice(0, 80).map((d) => `<tr><td>${esc(d.id)}</td><td>${d.false_positives.length}</td><td>${d.missed.length}</td><td>${d.ignored_low_confidence}</td></tr>`).join("")}</tbody></table><pre>逐图数据来源与排除原因已随报告记录（sources / excluded）。</pre></details>
      <details><summary>边界声明</summary><pre>${esc(JSON.stringify({ scope: record.scope, model_training_overlap: record.model_training_overlap, model_sha256: record.model_sha256, dataset_sha256: record.dataset_sha256, manifest_sha256: record.manifest_sha256, publication: record.publication }, null, 2))}</pre></details>`;
    $("[data-he-export]", body).hidden = false;
  }

  async function loadOptions() {
    showError("");
    try {
      options = await request("/api/evaluations/holdout/options");
      if (closed) return;
      const models = $("[data-he-model]", body), projects = $("[data-he-project]", body), versions = $("[data-he-version]", body);
      models.innerHTML = options.models.length
        ? options.models.map((m) => `<option value="${esc(m.id)}" ${m.available ? "" : "disabled"}>${esc(m.name)}${m.available ? "" : "（不可用）"}</option>`).join("")
        : `<option value="">没有已登记检测模型</option>`;
      projects.innerHTML = options.projects.length
        ? options.projects.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")
        : `<option value="">没有含 val 分组的项目——先到样本库导出确认版数据集</option>`;
      selectAdapter();
      fillVersions();
      sync();
    } catch (e) {
      if (!closed) showError(e);
    }
  }

  // Registered model metadata chooses the layout; users need not know output tensors.
  function selectAdapter() {
    const selected = (options?.models || []).find(m => m.id === $("[data-he-model]", body).value);
    $("[data-he-adapter]", body).value = selected?.adapter || "yolo_raw";
  }
  $("[data-he-model]", body).onchange = selectAdapter;

  function fillVersions() {
    const projects = $("[data-he-project]", body), versions = $("[data-he-version]", body), meta = $("[data-he-meta]", body);
    const project = (options?.projects || []).find((p) => p.id === projects.value);
    versions.innerHTML = project
      ? project.versions.map((v) => `<option value="${esc(v.id)}">版本 ${esc(v.id.slice(0, 16))}… · val ${v.val_count} 图 · ${fmtTime(v.created_at)}</option>`).join("")
      : `<option value="">请先导出确认版数据集</option>`;
    meta.textContent = project
      ? `类别：${project.classes.map(esc).join("、")} · 训练 ${project.versions[0].train_count} / 验证 ${project.versions[0].val_count} 图 · 组隔离 ${project.versions[0].group_count} 组`
      : "没有可用数据版本时无法发起评测。";
    sync();
  }

  $("[data-he-project]", body).onchange = fillVersions;
  $("[data-he-version]", body).onchange = sync;

  $("[data-he-run]", body).onclick = async () => {
    if (busy) return;
    const version = $("[data-he-version]", body).value;
    const conf = Number($("[data-he-conf]", body).value), iou = Number($("[data-he-iou]", body).value);
    if (!version) return showError({ message: "请选择冻结数据版本" });
    if (![conf, iou].every((v) => Number.isFinite(v) && v > 0 && v <= 1)) return showError({ message: "阈值须大于 0 且不超过 1" });
    busy = true;
    sync();
    showError("");
    result.textContent = "正在运行评测（模型快照 → 数据校验 → 逐图推理）…";
    try {
      const record = await request("/api/evaluations/holdout", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          engine_id: $("[data-he-model]", body).value, project_id: $("[data-he-project]", body).value,
          version_id: version, adapter: $("[data-he-adapter]", body).value, confidence: conf, iou,
        }),
      });
      if (!closed) { render(record); await loadHistory(); }
    } catch (e) {
      if (!closed) { showError(e); result.textContent = "本次评测未完成。"; }
    } finally {
      busy = false;
      if (!closed) sync();
    }
  };

  async function loadHistory() {
    try {
      const rows = await request("/api/evaluations/holdout");
      if (closed) return;
      $("[data-he-history]", body).innerHTML = rows.items.length
        ? rows.items.map((r) => `<button class="btn" style="display:block;width:100%;text-align:left;margin:8px 0" data-he-record="${esc(r.id)}">${esc(r.engine_name || r.engine_id)} × ${esc(r.dataset_version_id || "")} · ${fmtTime(r.created_at * 1000)} · P ${pct(r.summary?.precision)} / R ${pct(r.summary?.recall)}</button>`).join("")
        : "还没有历史报告——上方发起一次评测后会保存在这里。";
      body.querySelectorAll("[data-he-record]").forEach((button) => {
        button.onclick = async () => {
          try { render(await request(`/api/evaluations/holdout/${encodeURIComponent(button.dataset.heRecord)}`)); }
          catch (e) { showError(e); }
        };
      });
    } catch (e) {
      if (!closed) showError(e);
    }
  }

  $("[data-he-export]", body).onclick = () => last && download(last, `${last.id}.json`);
  loadOptions();
  loadHistory();
  return drawer;
}

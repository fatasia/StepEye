import { openPinInspection } from "./pin-inspection.js";
import { $, esc, fmtTime } from "../util.js";
import { openDrawer } from "../ui.js";
import { openEdgeMeasurement } from "./edge-measurement.js";
import { openEquipmentResults } from "./equipment-results.js";

const types = { spc: "SPC / 过程能力", pareto: "缺陷柏拉图", grr: "GR&R 测量系统", agreement: "复判一致性",
  detection: "检测框评测", calibration: "平面标定", barcode: "条码 / QR / DataMatrix", ocr: "OCR 文字识别（实验）" };
const hints = { spc: "CSV 表头 value；按采集顺序排列。", pareto: "CSV 表头 defect,count；每行一个缺陷类别和次数。",
  grr: "CSV 表头 part,operator,trial,value；每个零件、测量者、轮次均需有一次记录。",
  agreement: "CSV 表头 part,operator,trial,label；可追加 reference 参考标签列。",
  detection: "JSON 包含 classes、frames；每帧包含 id、reviewed、truth、predictions，坐标为归一化 xyxy。",
  calibration: "JSON 包含 scope_id、reference、image_size、plane、unit、points、validation_points、tolerance。物理值来自已知尺度，不由图片猜测。",
  barcode: "上传条码图片，只解码不打开链接、不执行识别内容。",
  ocr: "上传文字图片。当前OCR仍有字符错识，结果需复核，不能直接当产品SN或质量合格依据。" };
const number = (v) => v == null ? "—" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 4 });
async function request(url, options = {}) {
  const response = await fetch(url, options), body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "输入不符合接口要求，请检查字段与数值");
  return body;
}
function download(value, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function controlChart(data) {
  if (!data?.points?.length) return "";
  // Display every point for short series; reports retain all rows for longer imports.
  const points = data.points.slice(0, 300), limits = data.limits;
  const values = [...points.map(p => p.value), ...(limits ? [limits.lcl, limits.ucl] : [])];
  const low = Math.min(...values), span = Math.max(...values) - low || 1;
  const x = (i) => 12 + i * 436 / Math.max(1, points.length - 1), y = v => 146 - (v - low) / span * 126;
  const lines = limits ? [limits.lcl, limits.center, limits.ucl].map(v => `<line x1="12" y1="${y(v)}" x2="448" y2="${y(v)}" stroke="currentColor" opacity=".3" stroke-dasharray="4 4"/>`).join("") : "";
  return `<figure><figcaption>I 控制图 · 前 ${points.length} 点</figcaption><svg viewBox="0 0 460 164" role="img" aria-label="测量序列与控制界限">${lines}<polyline fill="none" stroke="var(--accent,#40cccc)" stroke-width="2" points="${points.map((p,i)=>`${x(i)},${y(p.value)}`).join(" ")}"/>${points.filter(p=>p.outside).map(p=>`<circle cx="${x(points.indexOf(p))}" cy="${y(p.value)}" r="3" fill="#ee6b66"/>`).join("")}</svg></figure>`;
}

/** Import once, compute once. Opening this drawer does not start model workers. */
export function openQualityTools({ canWrite }) {
  let closed = false, last = null, selectedCalibration = null;
  const drawer = openDrawer({ title: "评测、质量与标定", subtitle: "导入数据 → 分析 → 导出报告",
    html: `<style>.quality-tools [data-qt-pins][hidden]{display:none!important}.quality-tools label{display:block;margin:16px 0 8px}.quality-tools select,.quality-tools input{max-width:100%;padding:9px;background:transparent;color:inherit;border:1px solid #364348;border-radius:5px}.quality-tools .qt-actions{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.quality-tools .qt-metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px}.quality-tools .qt-metrics div{padding:15px;border:1px solid #364348;border-radius:6px}.quality-tools strong{display:block;font-size:24px;margin-top:8px}.quality-tools figure{margin:22px 0}.quality-tools pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}.quality-tools p{line-height:1.7}.quality-tools .qt-record{display:block;width:100%;text-align:left;margin:8px 0}.quality-tools .qt-error{color:#ff9890}</style>
      <div class="quality-tools"><button class="btn" data-qt-pins ${canWrite ? "" : "hidden"}>连接器针脚检查</button><button class="btn" data-qt-equipment>设备质检结果与复判</button><label>分析任务<select data-qt-kind>${Object.entries(types).map(([k,v])=>`<option value="${k}">${v}</option>`).join("")}</select></label>
      <p data-qt-hint></p><div data-qt-write ${canWrite ? "" : "hidden"}><label>数据文件<input data-qt-file type="file" accept=".csv,.json" /></label>
      <details data-qt-options><summary>规格限（可选）</summary><label>下限 <input data-qt-lsl type="number" step="any" /></label><label>上限 <input data-qt-usl type="number" step="any" /></label></details>
      <div class="qt-actions"><button class="btn btn-primary" data-qt-run>导入并分析</button></div></div>
      <p data-qt-error class="qt-error" role="alert"></p><section data-qt-result aria-live="polite">尚未导入数据。</section>
      <button class="btn" data-qt-export hidden>导出完整 JSON</button>
      <section data-qt-measure hidden><button class="btn btn-primary" data-qt-edge>框选边缘测量</button><h3>平面内两点测量</h3><p>输入当前标定画面中的两点像素坐标：x1,y1,x2,y2。</p><input data-qt-points placeholder="100,100,200,100" aria-label="两点像素坐标"/><button class="btn" data-qt-measure-run>测量</button><p data-qt-distance></p></section>
      <details open><summary>已保存报告与标定</summary><div data-qt-history>正在读取…</div></details></div>`,
    onClose: () => { closed = true; } });
  const body = drawer.body, kind = $("[data-qt-kind]", body), error = $("[data-qt-error]", body);
  $("[data-qt-equipment]",body).onclick = () => { drawer.close(); openEquipmentResults({ canWrite }); };
  $("[data-qt-edge]",body).onclick = () => {
    if (selectedCalibration?.validated && canWrite) {
      const calibration = selectedCalibration;
      drawer.close();
      openEdgeMeasurement(calibration);
    }
  };
  $("[data-qt-pins]",body).onclick = () => {
    if (canWrite) { drawer.close(); openPinInspection(); }
  };
  const showError = e => { if (!closed) error.textContent = e?.message || ""; };
  function render(report) {
    last = report; selectedCalibration = report.matrix ? report : null;
    const result = report.result || report;
    let metrics = [], chart = "";
    if (result.capability) {
      metrics = [["样本数",result.n],["Cpk",result.capability.cpk],["Ppk",result.capability.ppk],["控制图信号",result.signals.length]];
      chart = controlChart(result.individuals);
    } else if (result.total_defects != null) {
      metrics = [["缺陷次数", result.total_defects],["缺陷类别",result.items.length]];
      chart = result.items.slice(0,10).map(p=>`<p>${esc(p.defect)} · ${p.count}<progress value="${p.count}" max="${Math.max(1,result.total_defects)}" style="display:block;width:100%"></progress></p>`).join("");
    } else if (["barcode","ocr"].includes(report.kind)) {
      metrics = [["识别结果数",report.items.length],["本次耗时 ms",report.elapsed_ms]];
      chart = report.items.length ? report.items.map(item=>`<p><b>${esc(item.format)}</b><br>${esc(item.text)}</p>`).join("") : "<p>未识别到内容。</p>";
    } else if (result.summary) metrics = [["Precision",result.summary.precision],["Recall",result.summary.recall],["误检框",result.summary.fp],["漏检框",result.summary.fn]];
    else if (report.matrix) metrics = [["标定版本",report.version],[`最大验证误差 (${report.unit})`,report.validation_max]];
    else if (result.components) metrics = [["GR&R 标准差",result.components.grr.sd],["GR&R 研究变差 %",result.components.grr.percent_study_variation]];
    else if (result.between_operator_per_trial != null) metrics = [["人员间一致率",result.between_operator_per_trial],["参考标签覆盖率",result.reference_coverage]];
    $("[data-qt-result]", body).innerHTML = `<h3>${esc(report.title || types[report.kind] || "分析结果")}</h3><div class="qt-metrics">${metrics.map(([k,v])=>`<div>${esc(k)}<strong>${number(v)}</strong></div>`).join("")}</div>${chart}
      ${report.matrix ? `<p>${report.validated ? "独立验证点通过，可在覆盖范围内测量。" : "验证未通过，禁止测量。"}</p>` : ""}
      ${(result.warnings || []).map(s=>`<p>${esc(s)}</p>`).join("")}
      <details><summary>指标与方法详情</summary><pre data-qt-json></pre></details>`;
    $("[data-qt-json]", body).textContent = JSON.stringify(result, null, 2);
    $("[data-qt-export]", body).hidden = false;
    $("[data-qt-measure]", body).hidden = !selectedCalibration?.validated || !canWrite;
  }
  async function history() {
    const isCalibration = kind.value === "calibration";
    if (["detection","barcode","ocr"].includes(kind.value)) { $("[data-qt-history]",body).textContent="此类试测暂不存历史，请导出本次结果。"; return; }
    try {
      const response = await request(isCalibration ? "/api/calibrations" : `/api/quality/analyses?kind=${kind.value}`);
      if (closed) return;
      const rows = isCalibration ? response : response.items;
      $("[data-qt-history]",body).innerHTML = rows.length ? rows.map(r=>`<button class="btn qt-record" data-qt-record="${esc(r.id)}">${esc(r.title || r.scope_id || types[r.kind])} · ${esc(isCalibration ? `版本 ${r.version}` : fmtTime(r.created_at * 1000))}</button>`).join("") : "还没有保存记录。";
      body.querySelectorAll("[data-qt-record]").forEach(button=>button.onclick=async()=>{
        try { const r = await request(`${isCalibration ? "/api/calibrations" : "/api/quality/analyses"}/${encodeURIComponent(button.dataset.qtRecord)}`); if (!closed) render(r); } catch(e) { showError(e); }
      });
    } catch(e) { showError(e); }
  }
  kind.onchange = () => {
    $("[data-qt-hint]",body).textContent = hints[kind.value] + (["spc","pareto","grr","agreement"].includes(kind.value) ? " 也可导入同字段 JSON 数组或含 rows 的分析对象。" : "");
    $("[data-qt-options]",body).hidden = !["spc","grr"].includes(kind.value);
    $("[data-qt-file]",body).accept = ["barcode","ocr"].includes(kind.value) ? "image/png,image/jpeg,image/webp,image/bmp" : ".csv,.json";
    showError(null); history();
  };
  $("[data-qt-run]",body).onclick = async () => {
    const button = $("[data-qt-run]",body); button.disabled = true; kind.disabled = true; showError(null);
    try {
      const file = $("[data-qt-file]",body).files[0];
      if (["barcode","ocr"].includes(kind.value)) {
        if (!file || file.size>10*1024*1024) throw new Error("请选择不超过10MB的图片");
        const form = new FormData(); form.append("file",file); form.append("kind",kind.value);
        const report = await request("/api/readers/read",{method:"POST",body:form});
        if(!closed) render(report);
        return;
      }
      if (!file || file.size > 2*1024*1024) throw new Error("请选择不超过2MB的数据文件");
      const mode = kind.value, text = await file.text();
      let url = "/api/quality/analyses", payload;
      if (["calibration","detection"].includes(mode)) {
        payload = JSON.parse(text); url = mode === "calibration" ? "/api/calibrations" : "/api/evaluations/detection";
      } else {
        const options = {};
        if (["spc","grr"].includes(mode)) for (const key of ["lsl","usl"]) {
          const value = $(`[data-qt-${key}]`,body).value;
          if (value !== "") options[key] = Number(value);
        }
        if (file.name.toLowerCase().endsWith(".json")) {
          const input = JSON.parse(text);
          if (Array.isArray(input)) payload = { kind: mode, rows: input, title: file.name, options };
          else {
            if (!input || !Array.isArray(input.rows) || (input.kind && input.kind !== mode)) throw new Error("JSON需要rows数组，分析类型须与当前选择一致");
            payload = { ...input, kind: mode, title: input.title || file.name, options: { ...input.options, ...options } };
          }
        } else payload = { kind: mode, csv_text: text, title: file.name, options };
      }
      const report = await request(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
      if (!closed) { render(report); await history(); }
    } catch(e) { showError(e); } finally { if (!closed) { button.disabled=false; kind.disabled=false; } }
  };
  $("[data-qt-measure-run]",body).onclick = async () => {
    try {
      const text = $("[data-qt-points]",body).value.split(",");
      if(text.some(s=>!s.trim())) throw new Error("坐标不能为空");
      const p = text.map(s=>Number(s.trim()));
      if (p.length!==4 || p.some(v=>!Number.isFinite(v))) throw new Error("请输入四个有效坐标，使用英文逗号分隔");
      const c=selectedCalibration;
      const value=await request(`/api/calibrations/${encodeURIComponent(c.id)}/measure`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({scope_id:c.scope_id,reference:c.reference,image_size:c.image_size,points:[p.slice(0,2),p.slice(2)]})});
      if(!closed) $("[data-qt-distance]",body).textContent=`平面距离 ${number(value.distance)} ${value.unit} · 标定版本 ${value.version}`;
    }catch(e){showError(e);}
  };
  $("[data-qt-export]",body).onclick=()=>download(last,`${last.id || "evaluation"}.json`);
  kind.onchange();
  return drawer;
}

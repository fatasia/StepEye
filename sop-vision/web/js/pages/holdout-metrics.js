import { esc } from "../util.js";
const pct = value => value == null ? "—" : `${(value * 100).toFixed(1)}%`;

/** New reports store AP at the root; retain compatibility with older nested reports. */
export function renderEvaluationMetrics(record) {
  const ap = record.average_precision || record.evaluation?.average_precision;
  const coco = record.coco_bbox;
  const summary = ap ? `<p class="muted">AP@0.5 ${pct(ap.ap50)} · AP@0.5:0.95 ${pct(ap.ap50_95)}</p>` : "";
  if (coco?.status !== "available") return summary;
  return `${summary}<details data-he-coco><summary>COCO 完整指标</summary>
    <table><thead><tr><th>指标</th><th>结果</th></tr></thead><tbody>${Object.entries(coco.metrics).map(([key, value]) => `<tr><td>${esc(key)}</td><td>${pct(value)}</td></tr>`).join("")}</tbody></table>
    <p class="muted">按原图像素面积分组；无对应真值显示 —。预测分数下限 ${esc(record.prediction_protocol?.score_floor ?? "未知")}，每图最多 100 框；仅代表本次验证集。</p></details>`;
}

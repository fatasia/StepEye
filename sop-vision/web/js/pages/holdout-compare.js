import { esc, fmtTime } from "../util.js";

const pct = (value) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const delta = (value) => value == null ? "—" : `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)} pp`;
const statusText = { observed_drop: "观测下降", insufficient_data: "样本不足", no_observed_drop: "未观察到超阈值下降" };

async function request(url, options) {
  const response = await fetch(url, options);
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "对比失败");
  return value;
}

/** Embed in the existing report detail. No polling or additional inference. */
export function mountHoldoutCompare(host, current, { canWrite }) {
  if (!canWrite) { host.replaceChildren(); return; }
  host.innerHTML = `<details data-ac-section><summary>与基准对比</summary>
    <p class="muted">同一模型，比较已复核样本的 Precision / Recall；默认每类至少 20 个样本，下降 10 个百分点提示。</p>
    <label>基准报告<select data-ac-base aria-label="基准报告"><option value="">正在读取…</option></select></label>
    <div class="he-actions"><button class="btn" data-ac-run disabled>比较</button><button class="btn" data-ac-export hidden>导出对比</button></div>
    <p data-ac-error role="alert" class="he-error"></p><div data-ac-result aria-live="polite"></div></details>`;
  const find = (selector) => host.querySelector(selector);
  let loaded = false, last = null;
  find("[data-ac-section]").ontoggle = async () => {
    if (loaded || !find("[data-ac-section]").open) return;
    loaded = true;
    try {
      const data = await request("/api/evaluations/holdout?limit=100");
      if (!host.isConnected) return;
      const candidates = data.items.filter((r) => r.id !== current.id && r.model_sha256 === current.model_sha256 && r.adapter === current.adapter);
      find("[data-ac-base]").innerHTML = candidates.length
        ? `<option value="">选择基准报告</option>${candidates.map((r) => `<option value="${esc(r.id)}">${esc(r.dataset_version_id || r.version_id || r.id)} · ${esc(fmtTime(r.created_at * 1000))}</option>`).join("")}`
        : `<option value="">近 100 份报告中没有同模型基准</option>`;
    } catch (error) {
      if (host.isConnected) { find("[data-ac-error]").textContent = error.message; loaded = false; }
    }
  };
  find("[data-ac-base]").onchange = () => {
    last = null;
    find("[data-ac-result]").textContent = "";
    find("[data-ac-export]").hidden = true;
    find("[data-ac-run]").disabled = !find("[data-ac-base]").value;
  };
  find("[data-ac-run]").onclick = async () => {
    const button = find("[data-ac-run]");
    button.disabled = true;
    find("[data-ac-base]").disabled = true;
    find("[data-ac-error]").textContent = "";
    find("[data-ac-export]").hidden = true;
    find("[data-ac-result]").textContent = "正在比较…";
    last = null;
    try {
      const report = await request("/api/evaluations/holdout/compare", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baseline_id: find("[data-ac-base]").value, current_id: current.id }),
      });
      if (!host.isConnected) return;
      last = report;
      find("[data-ac-result]").innerHTML = `<h4>${esc(statusText[report.status])}</h4>
        <p class="muted">基准 ${report.baseline_frames} 图 → 当前 ${report.current_frames} 图 · ${report.dataset_relation === "same_dataset" ? "同一数据版本" : "不同数据版本"}</p>
        <div style="overflow-x:auto"><table><thead><tr><th>类别</th><th>Precision</th><th>变化</th><th>Recall</th><th>变化</th><th>结果</th></tr></thead><tbody>
        ${Object.entries(report.classes).map(([name, row]) => `<tr><td>${esc(name)}</td><td>${pct(row.baseline.precision)} → ${pct(row.current.precision)}</td><td>${delta(row.metrics.precision.delta)}${row.metrics.precision.sufficient ? "" : " *"}</td><td>${pct(row.baseline.recall)} → ${pct(row.current.recall)}</td><td>${delta(row.metrics.recall.delta)}${row.metrics.recall.sufficient ? "" : " *"}</td><td>${esc(statusText[row.status])}</td></tr>`).join("")}</tbody></table></div>
        <p class="muted">* 样本不足，不据此判定下降。数据难度、标注和采集变化尚未分离；不触发自动回滚。${report.prediction_protocol_changed ? "两份报告的预测导出协议不同。" : ""}</p>`;
      find("[data-ac-export]").hidden = false;
    } catch (error) {
      if (host.isConnected) { find("[data-ac-result]").textContent = ""; find("[data-ac-error]").textContent = error.message; }
    } finally {
      if (host.isConnected) {
        button.disabled = !find("[data-ac-base]").value;
        find("[data-ac-base]").disabled = false;
      }
    }
  };
  find("[data-ac-export]").onclick = () => {
    if (!last) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(last, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = `${last.current_id}-comparison.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
}

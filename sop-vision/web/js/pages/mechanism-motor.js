import { esc, fmtTime } from "../util.js";
import { openDrawer } from "../ui.js";

export function motorReport(report) {
  const result = report.result;
  return `<header class="ml-report-head"><div><span class="ml-kicker">${esc(fmtTime(report.created_at * 1000))}</span><h3>${esc(report.title || "电机故障分类")}</h3></div><span class="ml-state">研究候选</span></header>
    <p class="ml-help">MCC5-THU · 振动与三相电流的 114 维工程特征 · 不提供剩余寿命</p>
    <div style="overflow-x:auto"><table style="width:100%;text-align:left"><thead><tr><th>记录</th><th>有效窗口</th><th>候选类别</th></tr></thead><tbody>${result.records.map(row => `<tr><td style="padding:16px 8px 16px 0">${esc(row.id)}</td><td>${row.active_windows}</td><td><strong>${esc(row.label || "无有效窗口")}</strong></td></tr>`).join("")}</tbody></table></div>
    <p class="ml-help">整条记录的窗口概率均值分类，非单窗口告警。跨转速验证未通过，不能直接迁移为现场诊断。</p>
    <details><summary>来源与追溯</summary><p>复用 weihu 电机 RF 候选；${esc(result.historical_protocol)}，24 类。</p><p>输入声明：原版特征顺序、完整记录；平台未独立验证传感器来源。</p><p class="ml-hash">模型 SHA：${esc(result.model_sha256)}</p><p class="ml-hash">输入 SHA：${esc(report.input_sha256)}</p><p>CPU 计算 ${Number(result.elapsed_ms).toFixed(1)} ms；签名 ${result.signature?.verified ? "已验证" : "未要求"}。</p></details>
    <a class="btn" href="/api/mechanism/analyses/${encodeURIComponent(report.id)}/export" download>导出完整报告 JSON</a>`;
}

export function openMotorAnalysis({ onSaved } = {}) {
  let closed = false, busy = false;
  const controller = new AbortController();
  const drawer = openDrawer({ title: "电机故障分类", subtitle: "导入工程特征 → CPU 分析 → 保存报告",
    onClose: () => { closed = true; controller.abort(); }, html: `<form class="motor-form">
      <p>导入 MCC5-THU 原版 114 维特征 JSON。单次最多 8 条完整记录，每条最多 64 个有效窗口；不接受任意原始振动 CSV。</p>
      <label style="display:block;margin:24px 0">特征文件<input style="display:block;margin-top:12px" name="file" type="file" accept=".json,application/json" required></label>
      <details><summary>适用范围</summary><p>24 类电机与轴承研究候选。特征提取定义与顺序须匹配；转速外推未通过。空窗口记录显示不支持，不返回健康结论。</p></details>
      <div style="margin:24px 0"><button class="btn btn-primary" type="submit">分析并保存</button></div>
      <p data-motor-message role="status"></p></form>` });
  const form = drawer.body.querySelector("form"), message = form.querySelector("[data-motor-message]");
  form.onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    const file = form.elements.file.files[0];
    if (!file || file.size > 2 * 1024 * 1024) { message.textContent = "请选择不超过 2 MB 的 JSON。"; return; }
    busy = true;
    form.querySelector("button").disabled = true;
    message.textContent = "正在分析…";
    try {
      const body = JSON.parse(await file.text());
      if (closed) return;
      const response = await fetch("/api/mechanism/motor/analyses", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
      const report = await response.json();
      if (!response.ok) throw new Error(typeof report.detail === "string" ? report.detail : "请检查特征格式、顺序与记录大小。");
      if (!closed) { drawer.close(); onSaved?.(report); }
    } catch (error) {
      if (!closed && error.name !== "AbortError") message.textContent = error instanceof SyntaxError ? "JSON 格式无效。" : error.message;
    } finally {
      busy = false;
      if (!closed) form.querySelector("button").disabled = false;
    }
  };
  return drawer;
}

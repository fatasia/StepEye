import { esc, fmtTime } from "../util.js";
import { openDrawer } from "../ui.js";

const labels = {normal: "正常", backlash: "间隙", lack_lubrication: "缺润滑", spalling: "剥落"};

export function actuatorClassifierReport(report) {
  const result = report.result;
  return `<header class="ml-report-head"><div><span class="ml-kicker">${esc(fmtTime(report.created_at * 1000))}</span><h3>${esc(report.title || "执行器故障分类")}</h3></div><span class="ml-state">研究候选</span></header>
    <div class="ml-metrics"><div><span>完整记录</span><strong>${result.records.length}</strong></div><div><span>待复核</span><strong>${result.records.filter(r=>r.status==='review').length}</strong></div><div><span>CPU 用时</span><strong>${Number(result.elapsed_ms).toFixed(1)} <small>ms</small></strong></div></div>
    <div style="overflow-x:auto"><table style="width:100%;text-align:left"><thead><tr><th>记录</th><th>候选结果</th><th>最高概率</th></tr></thead><tbody>${result.records.map(row => `<tr><td style="padding:16px 8px 16px 0">${esc(row.id)}</td><td><strong>${esc(row.label ? labels[row.label] || row.label : "待复核")}</strong></td><td>${(Math.max(...row.probabilities)*100).toFixed(1)}%</td></tr>`).join("")}</tbody></table></div>
    <p class="ml-help">80 秒完整记录 · 已知六种负载 / 轨迹 · 不提供剩余寿命</p>
    <details><summary>候选分布与追溯</summary>${result.records.map(row=>`<p>${esc(row.id)}：${row.probabilities.map((p,i)=>`${esc(labels[result.classes[i]] || result.classes[i])} ${(p*100).toFixed(1)}%`).join(' · ')}；候选集：${esc(row.candidate_set.map(v=>labels[v]||v).join('、') || '空')}</p>`).join('')}<p>复用 weihu Cranfield 三种子集成。新设备与跨工况未经验证；空候选集或多个候选转人工复核。</p><p class="ml-hash">模型 SHA：${esc(result.model_sha256)}</p><p class="ml-hash">输入 SHA：${esc(report.input_sha256)}</p><p>签名 ${result.signature?.verified ? "已验证" : "未要求"}。</p></details>
    <a class="btn" href="/api/mechanism/analyses/${encodeURIComponent(report.id)}/export" download>导出完整报告 JSON</a>`;
}

export function openActuatorClassifierAnalysis({ onSaved } = {}) {
  let closed = false, busy = false;
  const controller = new AbortController();
  const drawer = openDrawer({ title: "执行器故障分类", subtitle: "导入完整信号 → CPU 分析 → 保存报告",
    onClose: () => { closed = true; controller.abort(); }, html: `<form class="actuator-classifier-form">
      <p>导入 25 Hz、80 秒的执行器信号 JSON，最多 8 条记录。</p>
      <label style="display:block;margin:24px 0">信号文件<input style="display:block;margin-top:12px" name="file" type="file" accept=".json,application/json" required></label>
      <details><summary>适用范围</summary><p>每条 2000 × 3：指令位置 mm、跟随误差 mm、电流 A。负载为 20 / 40 / neg40，轨迹为 sin / trap；不重采样，不截断。正常、间隙、缺润滑、剥落四类；新设备需单独验证。</p></details>
      <div style="margin:24px 0"><button class="btn btn-primary" type="submit">分析并保存</button></div>
      <p data-actuator-classifier-message role="status"></p></form>` });
  const form = drawer.body.querySelector("form"), message = form.querySelector("[data-actuator-classifier-message]");
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
      const response = await fetch("/api/mechanism/actuator-classifier/analyses", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
      const report = await response.json();
      if (!response.ok) throw new Error(typeof report.detail === "string" ? report.detail : "请检查信号格式、采样率和完整记录长度。");
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

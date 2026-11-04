import { esc, fmtTime } from '../util.js';
import { openDrawer } from '../ui.js';

const number = value => Number.isFinite(value)
  ? value.toLocaleString('zh-CN', {maximumFractionDigits:4}) : '—';

/** Residuals compare matched contexts; no threshold or lifetime is inferred here. */
export function actuatorReport(report) {
  const result = report.result || {};
  const reference = result.reference || {};
  const rows = Array.isArray(result.results) ? result.results : [];
  return `<header class="ml-report-head"><div><span class="ml-kicker">${esc(fmtTime(report.created_at*1000))}</span><h3>${esc(report.title || '执行器残差分析')}</h3></div><span class="ml-state">参考残差证据</span></header>
    <p class="ml-help">同工况参考对比 · 无异常阈值 · 不估算寿命。</p>
    <div class="ml-metrics"><div><span>健康参考窗口</span><strong>${number(reference.windows)}</strong></div><div><span>参考误差 RMS（mm）</span><strong>${number(reference.rms?.[0])}</strong></div><div><span>参考电流 RMS（A）</span><strong>${number(reference.rms?.[1])}</strong></div></div>
    ${rows.length ? `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;text-align:left"><thead><tr><th>评测窗口 / 工况</th><th>误差残差 RMS（mm）</th><th>电流残差 RMS（A）</th><th>标准化 RMS<br>误差 / 电流</th></tr></thead><tbody>${rows.map(row=>`<tr><td style="padding:16px 12px 16px 0">${esc(row.id)}${row.status === 'unknown' ? '<br><strong>超出参考范围 · 未计算</strong>' : ''}<br><small>${esc(row.context)} · ${esc(row.source_group)}</small></td><td>${number(row.error_residual_rms_mm)}</td><td>${number(row.current_residual_rms_A)}</td><td>${number(row.standardized_rms?.[0])} / ${number(row.standardized_rms?.[1])}</td></tr>`).join('')}</tbody></table></div>` : '<p class="ml-empty">当前报告没有有效评测窗口。</p>'}
    ${rows.some(row=>row.status === 'unknown') ? '<p class="ml-help">部分窗口的指令位置超出参考范围，未生成残差。请补充覆盖对应运动范围的健康参考。</p>' : ''}<p>健康参考由上传者确认；残差需结合负载与现场阈值复核。</p>
    <details><summary>参考来源与适用范围</summary><p>参考来源：${esc(reference.source || '未提供')}</p><p>${esc(typeof result.scope === 'string' ? result.scope : JSON.stringify(result.scope || {}))}</p><p>固定 25 Hz，每窗 400 点；指令位置与跟随误差单位 mm，电流单位 A。参考与评测必须是同一工况、不同来源组。</p><p class="ml-hash">输入 SHA-256：${esc(report.input_sha256 || '未提供')}</p></details>
    <a class="btn" href="/api/mechanism/analyses/${encodeURIComponent(report.id)}/export" download>导出完整报告 JSON</a>`;
}

export function openActuatorAnalysis({onSaved} = {}) {
  let closed = false;
  let busy = false;
  const controller = new AbortController();
  // Closing discards late responses and cancels the browser request, not server state.
  const drawer = openDrawer({title:'执行器残差分析', subtitle:'健康参考窗 + 同工况评测窗 → 残差报告',
    onClose:()=>{closed=true;controller.abort();}, html:`
    <style>.actuator-form{font-size:13px;line-height:1.7}.actuator-form label{display:block;margin:18px 0}.actuator-form input{display:block;box-sizing:border-box;width:100%;padding:10px;margin-top:7px;background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);border-radius:6px;font:inherit}.actuator-form input::file-selector-button{background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);padding:5px 9px;border-radius:4px;margin-right:10px}.actuator-form [role=alert]{color:#ef958b}.actuator-form .actuator-actions{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.actuator-form details{margin:18px 0}</style>
    <form class="actuator-form"><p>导入包含明确健康参考来源的 JSON。参考 1–10 窗，评测 1–20 窗；每窗 400 行 × 3 通道，25 Hz。只比较同工况，不计算寿命。</p>
    <label>参考与评测 JSON<input name="file" type="file" accept=".json,application/json" required></label>
    <details><summary>格式与参考要求</summary><p>通道顺序：command_position_mm、following_error_mm、current_A。每窗包含 id、source_group、context、values；参考与评测来源组不能重叠，context 必须相同。</p><p>reference_source 填写实际健康参考依据；仅在已经确认参考窗健康时将 reference_confirmed_healthy 设为 true。页面不自动确认、不拼造参考值。</p></details>
    <div class="actuator-actions"><button class="btn" type="button" data-actuator-template>下载空结构模板</button><button class="btn btn-primary" type="submit">分析并保存报告</button></div><p data-actuator-error role="alert"></p><p data-actuator-status role="status"></p></form>`});
  const form = drawer.body.querySelector('form');
  const error = form.querySelector('[data-actuator-error]');
  const status = form.querySelector('[data-actuator-status]');
  form.querySelector('[data-actuator-template]').onclick = ()=>{
    const emptyWindow = ()=>({id:'',source_group:'',context:'',values:[]});
    const template = {title:'',unit:'mm,A',sample_rate_hz:25,
      channels:['command_position_mm','following_error_mm','current_A'],
      reference_confirmed_healthy:false,reference_source:'',
      reference_windows:[emptyWindow()],evaluation_windows:[emptyWindow()]};
    const url = URL.createObjectURL(new Blob([JSON.stringify(template,null,2)], {type:'application/json'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'actuator-reference-empty.json';
    link.click();
    // Release after the browser has started its download.
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  form.onsubmit = async event=>{
    event.preventDefault();
    if (busy) return;
    const file = form.elements.file.files[0];
    if (!file || file.size > 2*1024*1024) {
      error.textContent = '请选择不超过 2 MB 的 JSON 文件。';
      return;
    }
    busy = true;
    error.textContent = '';
    status.textContent = '正在校验参考窗并计算残差…';
    form.querySelectorAll('button,input').forEach(control=>control.disabled=true);
    try {
      const body = JSON.parse(await file.text());
      if (closed) return;
      if (!body || Array.isArray(body) || typeof body !== 'object') throw new Error('JSON 顶层必须是参考与评测对象。');
      const response = await fetch('/api/mechanism/actuator/analyses', {method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
      const report = await response.json();
      if (!response.ok) throw new Error(typeof report.detail === 'string'
        ? report.detail : '参考声明、工况、来源组或 400×3 窗口格式不符合要求。');
      if (!closed) {
        drawer.close();
        onSaved?.(report);
      }
    } catch (exception) {
      if (!closed && exception.name !== 'AbortError') {
        error.textContent = exception instanceof SyntaxError ? 'JSON 格式无效，请检查文件。' : exception.message;
        status.textContent = '';
      }
    } finally {
      busy = false;
      if (!closed) form.querySelectorAll('button,input').forEach(control=>control.disabled=false);
    }
  };
  return drawer;
}


import { esc, fmtTime } from '../util.js';
import { openDrawer } from '../ui.js';

const number = value => Number.isFinite(value)
  ? value.toLocaleString('zh-CN', {maximumFractionDigits:2}) : '—';
const channels = ['setting1','setting2','setting3',
  ...Array.from({length:21}, (_,i)=>`sensor${i+1}`)];

/** RUL is scoped to FD001 cycles, never converted to plant hours or approval. */
export function rulReport(report) {
  const result = report.result || {};
  const rows = Array.isArray(result.results) ? result.results : [];
  const signature = result.signature || {};
  return `<header class="ml-report-head"><div><span class="ml-kicker">${esc(fmtTime(report.created_at*1000))}</span><h3>${esc(report.title || 'FD001 剩余寿命评估')}</h3></div><span class="ml-state">专域研究评估</span></header>
    <p class="ml-help">${signature.verified === true ? '签名已验证' : '未签名研究资产'} · FD001 仿真发动机专域 · 单位：周期 · 不适用于通用电机寿命。</p>
    ${rows.length ? `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;text-align:left"><thead><tr><th>机组</th><th>窗口结束周期</th><th>预测剩余周期</th><th>估计区间（周期）</th></tr></thead><tbody>${rows.map(row=>`<tr><td style="padding:16px 8px 16px 0">${esc(row.unit_id)}</td><td>${number(row.end_cycle)}</td><td><strong>${number(row.rul_cycles)}</strong></td><td>${number(row.interval_cycles?.[0])} – ${number(row.interval_cycles?.[1])}</td></tr>`).join('')}</tbody></table></div>` : '<p class="ml-empty">当前报告没有有效寿命结果。</p>'}
    <p>研究资产 · 区间仅作模型估计，不用于维护放行。</p>
    <details><summary>适用范围、签名与追溯</summary><dl class="ml-facts"><div><dt>适用数据域</dt><dd>${esc(result.dataset || 'NASA-CMAPSS-FD001')}</dd></div><div><dt>签名验证</dt><dd>${signature.verified === true ? '已验证' : '未验证 · 研究资产'}</dd></div><div><dt>计算耗时</dt><dd>${number(result.elapsed_ms)} ms</dd></div><div><dt>用途</dt><dd>仅评估 · 非生产寿命承诺</dd></div></dl>
    <p>${esc(typeof result.scope === 'string' ? result.scope : JSON.stringify(result.scope || {}))}</p><p>区间方法：${esc(result.interval_method || '未提供')}</p><p class="ml-hash">模型 SHA-256：${esc(result.model_sha256 || '未提供')}</p><p class="ml-hash">输入 SHA-256：${esc(report.input_sha256 || '未提供')}</p><p>签名状态：${esc(signature.status || '未提供')}</p></details>
    <a class="btn" href="/api/mechanism/analyses/${encodeURIComponent(report.id)}/export" download>导出完整报告 JSON</a>`;
}

export function openRULAnalysis({onSaved} = {}) {
  let closed = false;
  let busy = false;
  const controller = new AbortController();
  // Close cancels only the browser request; the server owns its bounded job lifecycle.
  const drawer = openDrawer({title:'FD001 剩余寿命评估', subtitle:'导入专域窗口 → 按需计算 → 保存报告',
    onClose:()=>{closed=true;controller.abort();}, html:`
    <style>.rul-form{font-size:13px;line-height:1.7}.rul-form label{display:block;margin:18px 0}.rul-form input{display:block;box-sizing:border-box;width:100%;padding:10px;margin-top:7px;background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);border-radius:6px;font:inherit}.rul-form input::file-selector-button{background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);padding:5px 9px;border-radius:4px;margin-right:10px}.rul-form [role=alert]{color:#ef958b}.rul-form .rul-actions{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.rul-form details{margin:18px 0}</style>
    <form class="rul-form"><p>仅接受 NASA C-MAPSS FD001 数据域：每个窗口连续 30 周期、24 个通道，单次 1–8 个窗口。不要上传任意电机波形或将周期换算成小时。</p>
    <label>窗口 JSON<input name="file" type="file" accept=".json,application/json" required></label>
    <details><summary>输入格式与范围</summary><p>通道顺序固定为 setting1–3、sensor1–21；values 每行一个周期，原始数值由后端模型预处理。unit_id 和 end_cycle 来自原数据。模板不包含信号样本，需填入来源明确的连续窗口。</p><p>结果是已训练模型的专域寿命估计，不是通用电机诊断，也不是线性趋势外推。</p></details>
    <div class="rul-actions"><button class="btn" type="button" data-rul-template>下载空结构模板</button><button class="btn btn-primary" type="submit">分析并保存报告</button></div><p data-rul-error role="alert"></p><p data-rul-status role="status"></p></form>`});
  const form = drawer.body.querySelector('form');
  const error = form.querySelector('[data-rul-error]');
  const status = form.querySelector('[data-rul-status]');
  form.querySelector('[data-rul-template]').onclick = ()=>{
    const template = {title:'',dataset:'NASA-CMAPSS-FD001',unit:'cycle',channels,
      windows:[{unit_id:'',end_cycle:null,values:[]}]};
    const url = URL.createObjectURL(new Blob([JSON.stringify(template,null,2)], {type:'application/json'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'fd001-rul-empty.json';
    link.click();
    // Keep the object URL alive until the browser has begun the download.
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
    status.textContent = '正在计算专域寿命估计并保存报告…';
    form.querySelectorAll('button,input').forEach(control=>control.disabled=true);
    try {
      const body = JSON.parse(await file.text());
      if (closed) return;
      if (!body || Array.isArray(body) || typeof body !== 'object') throw new Error('JSON 顶层必须是窗口对象。');
      const response = await fetch('/api/mechanism/rul/analyses', {method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
      const report = await response.json();
      if (!response.ok) throw new Error(typeof report.detail === 'string'
        ? report.detail : '输入字段、维度或数值不符合要求，请检查 30×24 窗口与通道顺序。');
      // A late response must not update a page whose drawer has been closed.
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

import { esc, fmtTime } from '../util.js';
import { openDrawer } from '../ui.js';

const number = value => Number.isFinite(value)
  ? value.toLocaleString('zh-CN', {maximumFractionDigits:2}) : '—';

/** FEMTO 相似性退化 RUL：趋势级研究评估，分钟单位，不是精密寿命模型。 */
export function degradationReport(report) {
  const result = report.result || {};
  const rows = Array.isArray(result.windows) ? result.windows : [];
  return `<header class="ml-report-head"><div><span class="ml-kicker">${esc(fmtTime(report.created_at*1000))}</span><h3>${esc(report.title || '退化趋势寿命评估')}</h3></div><span class="ml-state">相似性研究评估</span></header>
    <p class="ml-help">FEMTO 轴承全寿命试验域 · 单位：分钟 · 趋势级相似性方法，非精密 RUL，不用于维护放行。</p>
    ${rows.length ? `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;text-align:left"><thead><tr><th>观测单元</th><th>相似性 RUL（分钟）</th><th>匹配参考试验</th><th>匹配距离</th><th>线性外推对照</th><th>观测点数</th></tr></thead><tbody>${rows.map(row=>`<tr><td style="padding:16px 8px 16px 0">${esc(row.unit_id)}</td><td><strong>${number(row.rul_min)}</strong></td><td>${esc(row.matched_bearing)}</td><td>${number(row.match_distance)}</td><td>${row.extrapolation ? `${number(row.extrapolation.rul_min)}${row.capped ? '（已截断）' : ''}` : '斜率非正（突发型不适用）'}</td><td>${number(row.points)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="ml-empty">当前报告没有有效寿命结果。</p>'}
    <details><summary>适用范围与追溯</summary><dl class="ml-facts"><div><dt>适用数据域</dt><dd>${esc(result.dataset || 'FEMTO-PRONOSTIA')}</dd></div><div><dt>单位</dt><dd>${esc(result.unit || 'minute')}</dd></div><div><dt>用途</dt><dd>仅评估 · 趋势级健康评分参考</dd></div></dl>
    <p>${esc(typeof result.scope === 'string' ? result.scope : JSON.stringify(result.scope || {}))}</p><p>阈值不确定性：失效阈值跨试验 HI 跨度约 4.7 倍，单阈值迁移性弱。</p><p class="ml-hash">输入 SHA-256：${esc(report.input_sha256 || '未提供')}</p></details>
    <a class="btn" href="/api/mechanism/analyses/${encodeURIComponent(report.id)}/export" download>导出完整报告 JSON</a>`;
}

export function openDegradationAnalysis({onSaved} = {}) {
  let closed = false;
  let busy = false;
  const controller = new AbortController();
  const drawer = openDrawer({title:'退化趋势寿命评估', subtitle:'导入 RMS 观测窗 → 相似性匹配 → 保存报告',
    onClose:()=>{closed=true;controller.abort();}, html:`
    <style>.deg-form{font-size:13px;line-height:1.7}.deg-form label{display:block;margin:18px 0}.deg-form input{display:block;box-sizing:border-box;width:100%;padding:10px;margin-top:7px;background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);border-radius:6px;font:inherit}.deg-form input::file-selector-button{background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);padding:5px 9px;border-radius:4px;margin-right:10px}.deg-form [role=alert]{color:#ef958b}.deg-form .deg-actions{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.deg-form details{margin:18px 0}</style>
    <form class="deg-form"><p>仅接受 FEMTO-PRONOSTIA 采样协议：每分钟 1 点的双通道逐点最大 RMS 序列（5–600 点）+ 观测起点基线 RMS（前 3 分钟中位数）。单次 1–8 个窗口。</p>
    <label>窗口 JSON<input name="file" type="file" accept=".json,application/json" required></label>
    <details><summary>输入格式与范围</summary><p>dataset 固定为 FEMTO-PRONOSTIA-LearningSet；windows[].rms_values 为逐分钟正数序列，baseline_rms 为观测起点基线。结果是 FEMTO 训练试验的相似性趋势估计，不是通用电机寿命，也不是精密 RUL。</p><p>线性外推对照对突发型失效（健康指标全程平坦、末期跳变）原理性失效，仅供对照。</p></details>
    <div class="deg-actions"><button class="btn" type="button" data-deg-template>下载空结构模板</button><button class="btn btn-primary" type="submit">分析并保存报告</button></div><p data-deg-error role="alert"></p><p data-deg-status role="status"></p></form>`});
  const form = drawer.body.querySelector('form');
  const error = form.querySelector('[data-deg-error]');
  const status = form.querySelector('[data-deg-status]');
  form.querySelector('[data-deg-template]').onclick = ()=>{
    const template = {title:'',dataset:'FEMTO-PRONOSTIA-LearningSet',unit:'minute',
      windows:[{unit_id:'',baseline_rms:null,rms_values:[]}]};
    const url = URL.createObjectURL(new Blob([JSON.stringify(template,null,2)], {type:'application/json'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'femto-degradation-empty.json';
    link.click();
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
    status.textContent = '正在计算相似性退化估计并保存报告…';
    form.querySelectorAll('button,input').forEach(control=>control.disabled=true);
    try {
      const body = JSON.parse(await file.text());
      const response = await fetch('/api/mechanism/degradation/analyses', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(body), signal: controller.signal});
      const data = await response.json().catch(()=>({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`);
      status.textContent = '报告已保存。';
      onSaved?.(data);
      setTimeout(()=>{ if (!closed) drawer.close(); }, 400);
    } catch (err) {
      if (!closed) { error.textContent = err.message || '分析失败'; status.textContent = ''; }
    } finally {
      busy = false;
      if (!closed) form.querySelectorAll('button,input').forEach(control=>control.disabled=false);
    }
  };
}

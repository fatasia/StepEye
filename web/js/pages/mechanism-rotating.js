import { esc, fmtTime } from '../util.js';
import { openDrawer } from '../ui.js';

const names = { inner_ring:'内圈', outer_ring:'外圈', rolling_element:'滚动体', cage:'保持架' };
const num = value => Number(value).toLocaleString('zh-CN', {maximumFractionDigits:3});

/** Evidence scores are spectral contrast, never health probabilities. */
export function rotatingReport(report) {
  const r = report.result, f = r.frequency_hz || [], power = r.envelope_psd || [];
  const maxF = Math.max(...f, 1), maxP = Math.max(...power, 1e-30);
  const x = v => 45 + v / maxF * 820, y = v => 260 - v / maxP * 220;
  const spectrum = f.map((v,i) => `${x(v)},${y(power[i])}`).join(' ');
  return `<header class="ml-report-head"><div><span class="ml-kicker">${esc(fmtTime(report.created_at*1000))}</span><h3>${esc(report.title || '轴承包络谱')}</h3></div><span class="ml-state">${r.status === 'unknown' ? '信号不足' : '频谱证据'}</span></header>
    <div class="ml-metrics"><div><span>加速度 RMS (${esc(report.unit)})</span><strong>${num(r.rms)}</strong></div><div><span>波峰因数</span><strong>${num(r.crest_factor)}</strong></div><div><span>转速 RPM</span><strong>${num(r.rpm)}</strong></div></div>
    <section class="ml-plot"><svg viewBox="0 0 920 300" role="img" aria-label="轴承包络功率谱"><line x1="45" y1="260" x2="865" y2="260" stroke="currentColor" opacity=".2"/><polyline points="${spectrum}" fill="none" stroke="var(--accent,#3ec6c1)" stroke-width="1.5"/><text x="45" y="285">0 Hz</text><text x="865" y="285" text-anchor="end">${num(maxF)} Hz</text></svg></section>
    <p class="ml-help">谱峰与已知轴承阶次的对应证据，不是故障概率或剩余寿命。需结合转速稳定性、载荷和传感器安装位置复核。</p>
    <div class="ml-facts">${(r.candidates || []).map(c => `<div><dt>${esc(names[c.candidate] || c.candidate)} · ${num(c.frequency_hz)} Hz</dt><dd>谱峰对比 ${num(c.evidence_score)}<small> · 阶次 ${num(c.order)}</small></dd></div>`).join('')}</div>
    <details><summary>采集参数与谐波证据</summary><p>${num(r.samples)} 点 · ${num(r.duration_s)} 秒 · ${num(r.sample_rate_hz)} Hz；带通 ${r.band_hz.map(num).join('–')} Hz。</p>${(r.candidates || []).map(c => `<p>${esc(names[c.candidate] || c.candidate)}：${c.peaks.map(p => `${num(p.harmonic)}次 / 预期${num(p.expected_hz)} / 实测${num(p.observed_hz)} Hz`).join('；')}</p>`).join('')}<p class="ml-hash">输入 SHA-256：${esc(report.input_sha256)}</p><p>算法来源：weihu / MIT。</p></details>
    <a class="btn" href="/api/mechanism/analyses/${encodeURIComponent(report.id)}/export" download>导出完整报告 JSON</a>`;
}

export function openRotatingAnalysis({ onSaved }) {
  let closed = false, busy = false;
  const controller = new AbortController();
  const drawer = openDrawer({title:'轴承振动分析', subtitle:'上传波形 → 配置采集参数 → 查看包络谱', onClose:()=>{closed=true;controller.abort();}, html:`
    <style>.rotating-form label{display:block;margin:16px 0}.rotating-form input,.rotating-form select{display:block;box-sizing:border-box;width:100%;padding:9px;margin-top:6px;background:var(--surface,#172027);color:inherit;border:1px solid var(--border,#364348);border-radius:5px}.rotating-form p{line-height:1.7}.rotating-form [role=alert]{color:#ff9890}.rotating-form details{margin:20px 0}</style>
    <form class="rotating-form" data-rotating-form><p>上传恒定转速下的1–10秒加速度波形。CSV 表头 time_s,acceleration，时间单位为秒；最多8MB、20万点。</p>
    <label>波形 CSV<input name="file" type="file" accept=".csv,text/csv" required></label>
    <label>采样率 Hz<input name="sample_rate_hz" type="number" min="100" max="200000" step="any" required></label>
    <label>转速 RPM<input name="rpm" type="number" min="0.001" max="60000" step="any" required></label>
    <label>加速度单位<select name="unit"><option value="g">g</option><option value="m/s2">m/s²</option></select></label>
    <label>带通下限 Hz<input name="band_low" type="number" min="0.001" step="any" required></label>
    <label>带通上限 Hz<input name="band_high" type="number" min="0.001" step="any" required></label>
    <details open><summary>轴承特征阶次（至少一项）</summary><p>填写轴承厂家给出的故障频率与转频之比，不知道的留空。</p>${Object.entries(names).map(([key,label])=>`<label>${label}<input name="${key}" type="number" min="0.001" max="100" step="any"></label>`).join('')}</details>
    <p data-rotating-error role="alert"></p><button class="btn btn-primary" type="submit">分析并保存报告</button></form>`});
  const form = drawer.body.querySelector('form'), error = drawer.body.querySelector('[data-rotating-error]');
  form.onsubmit = async event => {
    event.preventDefault(); if (busy) return;
    const file = form.elements.file.files[0];
    if (!file || file.size > 8*1024*1024) {error.textContent='请选择8MB以内的CSV。';return;}
    busy=true;error.textContent='';form.querySelector('button').disabled=true;
    try {
      const orders = Object.fromEntries(Object.keys(names).filter(k=>form.elements[k].value!=='').map(k=>[k,Number(form.elements[k].value)]));
      if (!Object.keys(orders).length) throw new Error('请填写至少一个已知轴承特征阶次。');
      const payload = {csv_text:await file.text(), title:file.name, unit:form.elements.unit.value, orders,
        sample_rate_hz:Number(form.elements.sample_rate_hz.value), rpm:Number(form.elements.rpm.value),
        band_hz:[Number(form.elements.band_low.value),Number(form.elements.band_high.value)]};
      if (closed) return;
      const response = await fetch('/api/mechanism/rotating/analyses',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});
      const result=await response.json();
      if (!response.ok) throw new Error(typeof result.detail==='string'?result.detail:'采样参数或波形格式不符合要求。');
      if (!closed) {drawer.close();onSaved(result);}
    } catch(e) {if(!closed&&e.name!=='AbortError')error.textContent=e.message;}
    finally {busy=false;if(!closed)form.querySelector('button').disabled=false;}
  };
  return drawer;
}

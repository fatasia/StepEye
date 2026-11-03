import { openRotatingAnalysis, rotatingReport } from "./mechanism-rotating.js";
import { esc, fmtTime } from '../util.js';

const states = { unknown:'参考不足', within_reference:'未见持续超限', review:'偏离参考 · 待复核', evidence_only:'频谱证据' };
const features = { std:'标准差', mean:'均值', diff_rms:'差分均方根', p95_dev:'95% 偏离' };
const number = value => value == null ? '—' : Number(value).toLocaleString('zh-CN',{maximumFractionDigits:3});

function chart(result) {
  const series = result.series || [];
  if (!series.length) return '<p class="ml-empty">参考段缺少可用变化尺度，无法计算偏离曲线。请检查参考期和采样数据。</p>';
  const forecast = result.forecast || [];
  const all = [...series, ...forecast];
  const minX = series[0].cycle;
  const maxX = all[all.length-1].cycle;
  const maxY = Math.max(result.threshold, ...all.map(p=>p.deviation), 1) * 1.1;
  const x = v => 52+(v-minX)/Math.max(1,maxX-minX)*824;
  const y = v => 278-v/maxY*240;
  const points = values=>values.map(p=>`${x(p.cycle)},${y(p.deviation)}`).join(' ');
  const referenceEnd = series.filter(p=>p.reference).at(-1)?.cycle ?? minX;
  return `<svg viewBox="0 0 920 326" role="img" aria-label="周期偏离曲线、阈值与短期线性外推">
    <rect x="52" y="38" width="${x(referenceEnd)-52}" height="240" fill="currentColor" opacity=".04"/>
    ${[0,.25,.5,.75,1].map(p=>`<line x1="52" y1="${y(p*maxY)}" x2="876" y2="${y(p*maxY)}" stroke="currentColor" opacity=".12"/><text x="42" y="${y(p*maxY)+4}" text-anchor="end">${number(p*maxY)}</text>`).join('')}
    <line x1="52" y1="${y(result.threshold)}" x2="876" y2="${y(result.threshold)}" stroke="#e6b453" stroke-dasharray="5 5"/>
    <polyline points="${points(series)}" fill="none" stroke="var(--accent,#3ec6c1)" stroke-width="2.5"/>
    ${forecast.length?`<polyline points="${points([series.at(-1),...forecast])}" fill="none" stroke="#b49aed" stroke-width="2" stroke-dasharray="6 5"/>`:''}
    <text x="52" y="305">周期 ${minX}</text><text x="876" y="305" text-anchor="end">${maxX}</text><text x="62" y="54">参考期</text></svg>`;
}

export function render(root) {
  let dead = false;
  let canWrite = false;
  let busy = false;
  let offset = 0;
  let selection = 0;
  const controller = new AbortController();
  if (!document.querySelector('[data-mechanism-css]')) {
    const css=document.createElement('link');css.rel='stylesheet';css.href='/css/mechanism.css';css.dataset.mechanismCss='';document.head.append(css);
  }
  root.innerHTML=`<div class="mechanism-page"><header class="ml-head"><div><span class="ml-kicker">设备信号 / 周期分析</span><h2>机理实验室</h2><p>查看周期偏离、短期趋势与轴承振动证据。</p></div><span class="ml-chip">按需计算 · 不常驻模型</span></header>
    <p data-ml-message role="status"></p><div class="ml-layout"><aside><section class="ml-import" data-ml-upload hidden><button class="btn" type="button" data-ml-rotating>轴承振动分析</button><h3>导入周期数据</h3>
    <form data-ml-form><label>CSV 文件<input name="file" type="file" accept=".csv,text/csv" required></label><p class="ml-help">UTF-8 表头 cycle,value。周期连续、等长且按顺序排列；至少 24 周期，每周期 4 点，最多 2 MB。</p>
    <label>报告名称<input name="title" maxlength="200" placeholder="默认使用文件名"></label><label>采样值单位<input name="unit" maxlength="40" placeholder="例如 A、°C、mm/s"></label>
    <details><summary>高级参数</summary><label>参考期比例<input name="reference_fraction" type="number" min="0.01" max="0.5" step="0.01" value="0.2" required></label><label>偏离阈值<input name="threshold" type="number" min="0.01" max="100" step="any" value="6" required></label><label>连续超限周期<input name="hold" type="number" min="1" max="100" step="1" value="3" required></label><label>外推周期<input name="horizon" type="number" min="1" max="20" step="1" value="5" required></label></details>
    <p class="ml-help">起始参考期应为同工况的正常数据，且至少使用前 20 周期；工况变化也会产生偏离。</p><button class="btn btn-primary" type="submit">生成分析报告</button></form></section>
    <section class="ml-history"><div class="ml-section-head"><h3>历史报告</h3><button class="btn" data-ml-refresh>刷新</button></div><div data-ml-history>正在读取…</div><div class="ml-pages"><button class="btn" data-ml-prev disabled>上一页</button><span data-ml-page></span><button class="btn" data-ml-next disabled>下一页</button></div></section></aside>
    <main class="ml-report" data-ml-report><div class="ml-empty"><h3>先看数据，再谈趋势。</h3><p>上传周期 CSV，或选择一份历史报告。</p><p>这里只分析相对参考的变化，不提供故障根因或剩余寿命结论。</p></div></main></div></div>`;
  const find=s=>root.querySelector(s);
  const message=text=>{if(!dead)find('[data-ml-message]').textContent=text;};
  async function request(url, options={}) {
    const response=await fetch(url,{...options,signal:controller.signal});
    const data=await response.json();
    if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:`请求失败（${response.status}），请检查参数范围`);
    return data;
  }
  function show(report) {
    if(report.kind==='bearing_envelope_evidence'){find('[data-ml-report]').innerHTML=rotatingReport(report);return;}
    const r=report.result;
    const last=r.series?.at(-1);
    find('[data-ml-report]').innerHTML=`<header class="ml-report-head"><div><span class="ml-kicker">${esc(fmtTime(report.created_at*1000))}</span><h3>${esc(report.title||'周期分析')}</h3></div><span class="ml-state ${r.status==='review'?'ml-warn':''}">${esc(states[r.status]||r.status)}</span></header>
      <div class="ml-metrics"><div><span>最新偏离指数</span><strong>${number(last?.deviation)}</strong></div><div><span>首次持续超限周期</span><strong>${number(r.first_persistent_crossing_cycle)}</strong></div><div><span>近期斜率 / 周期</span><strong>${number(r.trend?.slope_per_cycle)}</strong></div></div>
      <section class="ml-plot">${chart(r)}<div class="ml-legend"><span>━ 实测偏离</span><span>┄ 阈值 ${number(r.threshold)}</span><span>┄ 短期线性外推</span></div></section>
      <p class="ml-help">偏离指数无量纲；线性外推不是寿命预测，也不代表置信区间。未持续超限不等于设备健康认证。</p>
      <dl class="ml-facts"><div><dt>周期 / 每周期采样点</dt><dd>${number(r.cycles)} / ${number(r.samples_per_cycle)}</dd></div><div><dt>参考期 / 连续确认</dt><dd>${number(r.reference_count)} / ${number(r.hold)} 周期</dd></div><div><dt>有效特征</dt><dd>${esc(r.active_features.map(v=>features[v]||v).join('、')||'无')}</dd></div><div><dt>外推拟合误差</dt><dd>${number(r.trend?.fit_rmse)}</dd></div></dl>
      <details><summary>方法与输入信息</summary><p>采样单位：${esc(report.unit||'未指定')}；无有效变化尺度的特征：${esc(r.inactive_features.map(v=>features[v]||v).join('、')||'无')}。</p><p>方法：固定参考稳健标准化 + 周期偏离；短期趋势仅基于最后 ${number(r.trend?.window_cycles)} 个非参考周期。</p><p class="ml-hash">输入 SHA-256：${esc(report.input_sha256)}</p><p>算法来源：weihu / MIT。</p></details>
      <a class="btn" href="/api/mechanism/analyses/${encodeURIComponent(report.id)}/export" download>导出完整报告 JSON</a>`;
  }
  async function load() {
    const data=await request(`/api/mechanism/analyses?limit=10&offset=${offset}`);
    if(dead)return;
    find('[data-ml-history]').innerHTML=data.items.length?data.items.map(r=>`<button class="ml-history-item" data-ml-id="${esc(r.id)}"><b>${esc(r.title||'未命名分析')}</b><span>${esc(states[r.status]||r.status)} · ${esc(fmtTime(r.created_at*1000))}</span></button>`).join(''):'<p class="ml-help">还没有分析报告。</p>';
    find('[data-ml-prev]').disabled=offset===0;
    find('[data-ml-next]').disabled=offset+10>=data.total;
    find('[data-ml-page]').textContent=`${data.total} 份`;
    root.querySelectorAll('[data-ml-id]').forEach(button=>button.onclick=async()=>{
      const token=++selection;
      try {
        const report=await request(`/api/mechanism/analyses/${encodeURIComponent(button.dataset.mlId)}`);
        if(!dead&&token===selection){show(report);message('');}
      }catch(e){if(e.name!=='AbortError')message(e.message);}
    });
  }
  find('[data-ml-rotating]').onclick=()=>{
    if(canWrite)openRotatingAnalysis({onSaved:report=>{
      if(dead)return;selection++;show(report);offset=0;refresh();message('频谱证据已保存。');
    }});
  };
  const refresh=()=>load().catch(e=>{if(e.name!=='AbortError')message(e.message);});
  find('[data-ml-refresh]').onclick=refresh;
  find('[data-ml-prev]').onclick=()=>{offset=Math.max(0,offset-10);refresh();};
  find('[data-ml-next]').onclick=()=>{offset+=10;refresh();};
  find('[data-ml-form]').onsubmit=async e=>{
    e.preventDefault();
    if(!canWrite||busy)return;
    const form=e.currentTarget;
    const file=form.elements.file.files[0];
    if(!file||file.size>2*1024*1024){message('请选择不超过 2 MB 的 CSV 文件');return;}
    busy=true;
    const button=form.querySelector('[type=submit]');button.disabled=true;
    const token=++selection;
    message('正在分析并保存报告…');
    try {
      const text=await file.text();
      if(dead)return;
      const payload={csv_text:text,title:form.elements.title.value.trim()||file.name,unit:form.elements.unit.value.trim()};
      for(const key of ['reference_fraction','threshold','hold','horizon'])payload[key]=Number(form.elements[key].value);
      const report=await request('/api/mechanism/analyses',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(dead)return;
      if(token===selection)show(report);
      offset=0;await load();message('报告已保存。请结合工况复核偏离原因。');
    }catch(error){if(error.name!=='AbortError')message(error.message);}
    finally{busy=false;if(!dead)button.disabled=false;}
  };
  Promise.all([request('/api/auth/me'),request('/api/mechanism/capabilities')]).then(async([me])=>{
    if(dead)return;
    if(!['viewer','operator','admin'].includes(me.role))throw new Error('无法确认访问权限');
    canWrite=['operator','admin'].includes(me.role);
    find('[data-ml-upload]').hidden=!canWrite;
    await load();
  }).catch(e=>{if(e.name!=='AbortError')message(e.message);});
  // Leaving the route cancels pending reads/posts and ignores their late UI responses.
  return()=>{dead=true;selection++;controller.abort();};
}

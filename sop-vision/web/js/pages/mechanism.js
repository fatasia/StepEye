import { openCycleAnalysis } from "./mechanism-cycle.js";
import { openActuatorAnalysis, actuatorReport } from "./mechanism-actuator.js";
import { openRULAnalysis, rulReport } from "./mechanism-rul.js";
import { openValveAnalysis, valveReport } from "./mechanism-valve.js";
import { openRotatingAnalysis, rotatingReport } from "./mechanism-rotating.js";
import { esc, fmtTime } from '../util.js';

const states = { unknown:'参考不足', within_reference:'未见持续超限', review:'偏离参考 · 待复核', evidence_only:'分析证据', shadow_evidence:'影子证据', unsupported:'证据不足' };
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
  let activeDrawer = null;
  let selectedId = null;
  let offset = 0;
  let selection = 0;
  const controller = new AbortController();
  if (!document.querySelector('[data-mechanism-css]')) {
    const css=document.createElement('link');css.rel='stylesheet';css.href='/css/mechanism.css';css.dataset.mechanismCss='';document.head.append(css);
  }
  const modes = [
    ['cycle','周期趋势','周期 CSV','相对参考期的偏离与变化','01'],
    ['rotating','轴承振动','振动波形','包络谱与故障阶次证据','02'],
    ['valve','阀门信号','双通道压力','命令时标与压力联合分析','03'],
    ['actuator','执行器','参考 + 评测窗','同工况运动残差','04'],
    ['rul','专域寿命','FD001 窗口','航空发动机仿真数据专域','05']
  ];
  root.innerHTML=`<div class="mechanism-page"><header class="ml-head"><div><span class="ml-kicker">SIGNAL ANALYSIS</span><h2>机理实验室</h2><p>从设备信号，到可追溯的分析证据。</p></div><span class="ml-chip"><i></i>按需分析</span></header>
    <section class="ml-launcher" data-ml-upload hidden><button class="btn btn-primary" data-ml-start aria-expanded="false">＋ 新建分析</button><div class="ml-mode-grid" data-ml-modes hidden>${modes.map(([key,title,format,description,index])=>`<button class="ml-mode" data-ml-${key}><span class="ml-mode-top"><span>${index}</span><span>↗</span></span><strong>${title}</strong><span class="ml-mode-description">${description}</span><small>${format}</small></button>`).join('')}</div></section>
    <p data-ml-message role="status"></p><div class="ml-layout"><aside><section class="ml-history"><div class="ml-section-head"><h3>分析记录</h3><button class="btn" data-ml-refresh>刷新</button></div><div data-ml-history>正在读取…</div><div class="ml-pages"><button class="btn" data-ml-prev disabled>上一页</button><span data-ml-page></span><button class="btn" data-ml-next disabled>下一页</button></div></section></aside>
    <main class="ml-report" data-ml-report><div class="ml-empty"><span class="ml-empty-symbol" aria-hidden="true">⌁</span><h3>让信号说明问题</h3><p>开始一次分析，或从左侧选择已有报告。</p><span class="ml-empty-note">周期趋势 / 轴承振动 / 阀门 / 执行器 / 专域寿命</span></div></main></div></div>`;
  const find=s=>root.querySelector(s);
  const message=text=>{if(!dead)find('[data-ml-message]').textContent=text;};
  async function request(url, options={}) {
    const response=await fetch(url,{...options,signal:controller.signal});
    const data=await response.json();
    if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:`请求失败（${response.status}），请检查参数范围`);
    return data;
  }
  function show(report) {
    selectedId = report.id;
    root.querySelectorAll("[data-ml-id]").forEach(button=>button.classList.toggle("is-active",button.dataset.mlId===selectedId));
    if(report.kind==='actuator_motion_residual'){find('[data-ml-report]').innerHTML=actuatorReport(report);return;}
    if(report.kind==='cmapss_rul'){find('[data-ml-report]').innerHTML=rulReport(report);return;}
    if(report.kind==='valve_fusion_shadow'){find('[data-ml-report]').innerHTML=valveReport(report);return;}
    if(report.kind==='bearing_envelope_evidence'){find('[data-ml-report]').innerHTML=rotatingReport(report);return;}
    const r=report.result;
    const last=r.series?.at(-1);
    find('[data-ml-report]').innerHTML=`<header class="ml-report-head"><div><span class="ml-kicker">${esc(fmtTime(report.created_at*1000))}</span><h3>${esc(report.title||'周期分析')}</h3></div><span class="ml-state ${r.status==='review'?'ml-warn':''}">${esc(states[r.status]||r.status)}</span></header>
      <div class="ml-metrics"><div><span>最新偏离指数</span><strong>${number(last?.deviation)}</strong></div><div><span>首次持续超限周期</span><strong>${number(r.first_persistent_crossing_cycle)}</strong></div><div><span>近期斜率 / 周期</span><strong>${number(r.trend?.slope_per_cycle)}</strong></div></div>
      <section class="ml-plot">${chart(r)}<div class="ml-legend"><span>━ 实测偏离</span><span>┄ 阈值 ${number(r.threshold)}</span><span>┄ 短期线性外推</span></div></section>
      <p class="ml-help">偏离指数无量纲 · 虚线为短期外推，非寿命预测。</p>
      <details><summary>分析参数与追溯</summary><dl class="ml-facts"><div><dt>周期 / 每周期采样点</dt><dd>${number(r.cycles)} / ${number(r.samples_per_cycle)}</dd></div><div><dt>参考期 / 连续确认</dt><dd>${number(r.reference_count)} / ${number(r.hold)} 周期</dd></div><div><dt>有效特征</dt><dd>${esc(r.active_features.map(v=>features[v]||v).join('、')||'无')}</dd></div><div><dt>外推拟合误差</dt><dd>${number(r.trend?.fit_rmse)}</dd></div></dl>
      <p>采样单位：${esc(report.unit||'未指定')}；无有效变化尺度的特征：${esc(r.inactive_features.map(v=>features[v]||v).join('、')||'无')}。</p><p>方法：固定参考稳健标准化 + 周期偏离；短期趋势仅基于最后 ${number(r.trend?.window_cycles)} 个非参考周期。</p><p class="ml-hash">输入 SHA-256：${esc(report.input_sha256)}</p><p>算法来源：weihu / MIT。</p></details>
      <a class="btn" href="/api/mechanism/analyses/${encodeURIComponent(report.id)}/export" download>导出完整报告 JSON</a>`;
  }
  async function load() {
    const data=await request(`/api/mechanism/analyses?limit=10&offset=${offset}`);
    if(dead)return;
    find('[data-ml-history]').innerHTML=data.items.length?data.items.map(r=>`<button class="ml-history-item ${r.id===selectedId?'is-active':''}" data-ml-id="${esc(r.id)}"><b>${esc(r.title||'未命名分析')}</b><span>${esc(states[r.status]||r.status)} · ${esc(fmtTime(r.created_at*1000))}</span></button>`).join(''):'<p class="ml-help">还没有分析报告。</p>';
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
  const saved = report=>{
    if(dead)return;
    selection++;
    show(report);
    offset=0;
    refresh();
    message('报告已保存');
  };
  const openers={cycle:openCycleAnalysis,rotating:openRotatingAnalysis,valve:openValveAnalysis,rul:openRULAnalysis,actuator:openActuatorAnalysis};
  find("[data-ml-start]").onclick=()=>{
    const modes=find("[data-ml-modes]");
    modes.hidden=!modes.hidden;
    find("[data-ml-start]").setAttribute("aria-expanded",String(!modes.hidden));
  };
  for(const [key,open] of Object.entries(openers)) {
    find(`[data-ml-${key}]`).onclick=()=>{
      if(canWrite) {
        find("[data-ml-modes]").hidden=true;
        find("[data-ml-start]").setAttribute("aria-expanded","false");
        activeDrawer=open({onSaved:saved});
      }
    };
  }
  const refresh=()=>load().catch(e=>{if(e.name!=='AbortError')message(e.message);});
  find('[data-ml-refresh]').onclick=refresh;
  find('[data-ml-prev]').onclick=()=>{offset=Math.max(0,offset-10);refresh();};
  find('[data-ml-next]').onclick=()=>{offset+=10;refresh();};
  Promise.all([request('/api/auth/me'),request('/api/mechanism/capabilities')]).then(async([me])=>{
    if(dead)return;
    if(!['viewer','operator','admin'].includes(me.role))throw new Error('无法确认访问权限');
    canWrite=['operator','admin'].includes(me.role);
    find('[data-ml-upload]').hidden=!canWrite;
    await load();
  }).catch(e=>{if(e.name!=='AbortError')message(e.message);});
  // Leaving the route cancels pending reads/posts and ignores their late UI responses.
  return()=>{dead=true;selection++;controller.abort();activeDrawer?.close();};
}

import { esc, fmtTime } from '../util.js';
import { openDrawer } from '../ui.js';

const num = value => Number.isFinite(value) ? Number(value).toLocaleString('zh-CN',{maximumFractionDigits:4}) : '—';
const reasons = {
  missing_pressure_clock:'缺少压力采集时钟标识',
  no_independent_command_edge:'缺少独立阀门命令边沿证据',
  clock_id_mismatch:'命令与压力采集时钟标识不一致',
  missing_channel_timestamps:'缺少通道采样时间戳',
  timestamp_gap_or_order:'采样时间存在缺点、逆序或间隔不符合 100 Hz',
  timestamp_grid_drift:'采样时间累计漂移超出容差',
  command_window_offset:'窗口起点不满足命令后 8 秒的允许偏差',
  channel_skew:'通道采样时间差超出容差'
};

/** An unsupported input has no score. Shadow scores are not release decisions. */
export function valveReport(report) {
  const result=report.result;
  const supported=result.status==='shadow_evidence';
  const quality=result.quality||{};
  const offsets=quality.offset_samples||{};
  const offsetText=Object.entries(offsets).map(([channel,value])=>`${channel}：${num(value)}`).join(' / ')||'尚无有效命令时标';
  const missing=(quality.reasons||[]).map(reason=>{
    const code=typeof reason==='string'?reason:reason.code;
    const description=typeof reason==='object'?(reason.message||reason.detail):null;
    return `<li>${esc(description||reasons[code]||code||'证据未满足')}${reason.channel?` · ${esc(reason.channel)}`:''}<small> ${esc(code||'')}</small></li>`;
  }).join('');
  return `<header class="ml-report-head"><div><span class="ml-kicker">${esc(fmtTime(report.created_at*1000))}</span><h3>${esc(report.title||'阀门双证据分析')}</h3></div><span class="ml-state">${supported?'仅影子证据':'证据不足 · 不支持分析'}</span></header>
    <p class="ml-help">校验的是上传时标声明，不是设备来源认证。本报告不触发告警、控制、SOP 或产品放行。</p>
    ${supported?`<div class="ml-metrics"><div><span>历史域评分（非健康概率）</span><strong>${num(result.score)}</strong></div><div><span>历史参考阈值</span><strong>${num(result.historical_threshold)}</strong></div><div><span>阈值比较</span><strong style="font-size:20px">${result.threshold_exceeded?'超过历史阈值':'未超过历史阈值'}</strong></div></div><p>仅用于既有参考包适用域内的影子评估；未超阈值不等于设备健康或检验合格。</p>`:
      `<section class="ml-empty" style="padding:24px;text-align:left"><h3>补齐实际采集证据后再分析</h3><ul>${missing||'<li>当前输入未满足模型证据要求，请查看完整报告。</li>'}</ul><p>没有生成评分，不以缺少数据推断正常。</p></section>`}
    <dl class="ml-facts"><div><dt>验证范围</dt><dd>${result.validation_scope==='replay_fixture'?'参考包回放数据':result.validation_scope==='uploaded_timestamp_claims'?'上传时间戳声明':esc(result.validation_scope||'尚未满足')}</dd></div><div><dt>距命令后 8 秒窗口的偏移（采样点）</dt><dd>${esc(offsetText)}</dd></div><div><dt>最大通道时间差（采样点）</dt><dd>${num(quality.max_channel_skew_samples)}</dd></div><div><dt>使用状态</dt><dd>仅评估 · 非放行</dd></div></dl>
    <details><summary>适用范围与追溯</summary><p>当前入口使用 PS1 / PS2 压力通道、bar 单位、100 Hz 与 400 点输入；窗口从命令后 8 秒开始，长度 4 秒；命令偏移与通道时间差容差均为 1 个采样点。命令边沿和各通道时标必须来自实际采集；参考包回放须显式声明。不能把不明来源的信号补成“同步”数据。</p><p class="ml-hash">输入 SHA-256：${esc(report.input_sha256||'未提供')}</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(quality,null,2))}</pre></details>
    <a class="btn" href="/api/mechanism/analyses/${encodeURIComponent(report.id)}/export" download>导出完整报告 JSON</a>`;
}

export function openValveAnalysis({onSaved}) {
  let closed=false;
  let busy=false;
  const controller=new AbortController();
  const drawer=openDrawer({title:'阀门双证据分析',subtitle:'导入采集 JSON → 检查证据 → 影子报告',onClose:()=>{closed=true;controller.abort();},html:`
    <style>.valve-form{font-size:13px;line-height:1.7}.valve-form label{display:block;margin:16px 0}.valve-form input{display:block;width:100%;box-sizing:border-box;margin-top:7px;padding:9px;border:1px solid var(--border,#34434b);border-radius:6px;background:var(--panel,#172027);color:inherit;font:inherit}.valve-form input::file-selector-button{background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);padding:5px 9px;margin-right:10px;border-radius:4px}.valve-form [role=alert]{color:#ef958b}.valve-form .vf-actions{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.valve-form details{margin:16px 0}</style>
    <form class="valve-form"><p>上传完整的压力采样 JSON，包括真实通道时标、时钟来源与命令边沿。页面原样提交，不自动补同步时间或设备来源。</p><label>采集 JSON<input name="file" type="file" accept=".json,application/json" required></label>
    <details><summary>格式说明</summary><p>channels 为 PS1/PS2，units 为 bar/bar；values 为 400 行双通道值。sample_rate_hz、clock_id、timestamps_s 与 command_edge 应来自采集系统，未知项保持为空。</p><p>下载的是空结构模板，不是示例采集数据。填入真实信号和时间证据后上传；缺少证据的报告显示“不支持分析”。</p></details>
    <div class="vf-actions"><button class="btn" type="button" data-valve-template>下载空结构模板</button><button class="btn btn-primary" type="submit">分析并保存报告</button></div><p data-valve-error role="alert"></p><p data-valve-status role="status"></p></form>`});
  const form=drawer.body.querySelector('form');
  const error=form.querySelector('[data-valve-error]');
  const message=form.querySelector('[data-valve-status]');
  form.querySelector('[data-valve-template]').onclick=()=>{
    const template={title:'',channels:['PS1','PS2'],units:['bar','bar'],sample_rate_hz:null,
      values:[],clock_id:null,timestamps_s:null,command_edge:null};
    const url=URL.createObjectURL(new Blob([JSON.stringify(template,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download='valve-acquisition-empty.json';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  form.onsubmit=async event=>{
    event.preventDefault();if(busy)return;
    const file=form.elements.file.files[0];
    if(!file||file.size>2*1024*1024){error.textContent='请选择不超过 2 MB 的 JSON 文件。';return;}
    busy=true;error.textContent='';message.textContent='正在校验证据并保存报告…';
    form.querySelectorAll('button,input').forEach(input=>input.disabled=true);
    try{
      const data=JSON.parse(await file.text());
      if(closed)return;
      if(!data||Array.isArray(data)||typeof data!=='object')throw new Error('JSON 顶层必须是采集对象。');
      const response=await fetch('/api/mechanism/valve/analyses',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(data),signal:controller.signal});
      const report=await response.json();
      if(!response.ok)throw new Error(typeof report.detail==='string'?report.detail:'采集字段或数值格式不符合要求，请核对原始 JSON。');
      if(!closed){drawer.close();onSaved(report);}
    }catch(e){if(!closed&&e.name!=='AbortError'){error.textContent=e instanceof SyntaxError?'JSON 格式无效，请检查文件。':e.message;message.textContent='';}}
    finally{busy=false;if(!closed)form.querySelectorAll('button,input').forEach(input=>input.disabled=false);}
  };
  return drawer;
}

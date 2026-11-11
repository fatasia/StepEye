import { openDrawer } from '../ui.js';
import { esc } from '../util.js';

export async function openStepEditor(station) {
  const path = `/api/stations/${encodeURIComponent(station)}/steps-editor`;
  const response = await fetch(path), data = await response.json();
  if (!response.ok) throw new Error(data.detail || '无法读取步骤');
  if (!data.cycle) throw new Error('此工位没有作业周期；请从我的应用创建 SOP 防错任务');
  const d = openDrawer({title:'编辑作业步骤', subtitle:'设置步骤、顺序和完成条件',html:'<div data-editor></div>'});
  d.body.classList.add('workflow');
  let steps = structuredClone(data.steps), cycle = structuredClone(data.cycle);
  const linear=data.steps.every((s,i)=>s.after==null || (i===0?s.after.length===0:s.after.length===1&&s.after[0]===data.steps[i-1].id));
  const root=d.body.querySelector('[data-editor]');
  const zones = (selected) => data.zones.map(z=>`<option ${z.id===selected?'selected':''} value="${esc(z.id)}">${esc(z.id)}</option>`).join('');
  const modeOf=s=>s.evidence?.all?.length===1 && ['hand_dwell','obj_state'].includes(Object.keys(s.evidence.all[0])[0]) ? Object.keys(s.evidence.all[0])[0] : 'advanced';
  function paint() {
    root.innerHTML=`<p>从上到下执行。复杂组合与循环保留原配置，可只修改名称和超时。</p><div data-step-list>${steps.map((s,i)=>{
      const mode=modeOf(s), cond=s.evidence?.all?.[0]?.[mode]||{};
      return `<fieldset data-step="${i}"><legend>第 ${i+1} 步</legend><label class="field"><span>名称</span><input data-name value="${esc(s.name||s.id)}"></label>
      <label class="field"><span>超时（秒）</span><input data-timeout type="number" min="0.1" step="0.1" value="${s.timeout_s||''}"></label>
      ${mode==='advanced'||s.foreach?'<p>此步包含高级条件，保持原完成逻辑。</p>':`<label class="field"><span>完成条件</span><select data-mode><option value="hand_dwell" ${mode==='hand_dwell'?'selected':''}>手进入区域并停留</option><option value="obj_state" ${mode==='obj_state'?'selected':''}>区域达到指定状态</option></select></label><label class="field"><span>区域</span><select data-zone>${zones(cond.zone)}</select></label><label class="field"><span>状态名称（区域状态时使用）</span><input data-state value="${esc(cond.state||'present')}"></label><label class="field"><span>保持时间（毫秒）</span><input data-ms type="number" min="1" value="${cond.min_ms||300}"></label>`}
      <button class="btn" data-up="${i}" ${i===0||!linear?'disabled':''}>上移</button> <button class="btn" data-down="${i}" ${i===steps.length-1||!linear?'disabled':''}>下移</button> <button class="btn" data-remove="${i}" ${!linear?'disabled':''}>删除</button></fieldset>`;
    }).join('')}</div><button class="btn" data-add>添加步骤</button>
    <details><summary>周期与节拍</summary><label class="field"><span>节拍目标（秒）</span><input data-takt type="number" min="0.1" value="${cycle.takt_s||''}"></label>
    ${['start','end'].map(k=>cycle[k]?.obj_state?`<label class="field"><span>${k==='start'?'开始':'结束'}周期的区域</span><select data-cycle-zone="${k}">${zones(cycle[k].obj_state.zone)}</select></label><label class="field"><span>区域状态</span><input data-cycle-state="${k}" value="${esc(cycle[k].obj_state.state)}"></label>`:'<p>当前周期使用高级条件，保持原起止逻辑。</p>').join('')}
    <p>配置后到「结果核验」采集对应参考图。</p></details>
    <p data-error role="alert"></p><button class="btn btn-primary" data-save>保存并应用</button>`;
    root.querySelectorAll('input,select').forEach(x=>x.onchange=collect);
    root.querySelector('[data-add]').onclick=()=>{collect();steps.push({id:'S-'+crypto.randomUUID().slice(0,8),name:'新步骤',evidence:{all:[{hand_dwell:{zone:data.zones[0]?.id,min_ms:300}}]}});paint();};
    root.querySelectorAll('[data-up],[data-down],[data-remove]').forEach(b=>b.onclick=()=>{collect();let i=Number(b.dataset.up??b.dataset.down??b.dataset.remove); if(b.dataset.remove!==undefined)steps.splice(i,1);else{let j=i+(b.dataset.up!==undefined?-1:1);[steps[i],steps[j]]=[steps[j],steps[i]];} paint();});
    root.querySelector('[data-save]').onclick=async()=>{
      collect(); const error=root.querySelector('[data-error]'), button=root.querySelector('[data-save]');button.disabled=true;
      try {
        if(!steps.length)throw new Error('至少保留一个步骤');
        // 保留显式的并行/循环关系；普通串行步骤按当前排列更新前驱。
        if(linear)steps.forEach((s,i)=>{s.after=i?[steps[i-1].id]:[];});
        const r=await fetch(path,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({steps,cycle,roi_states:data.roi_states})});
        const value=await r.json();if(!r.ok)throw new Error(typeof value.detail==='string'?value.detail:'字段无效');
        d.close();location.hash=`#/config/${encodeURIComponent(station)}`;
      }catch(e){error.textContent=e.message;}finally{button.disabled=false;}
    };
  }
  function collect(){
    root.querySelectorAll('[data-step]').forEach(el=>{const s=steps[+el.dataset.step];s.name=el.querySelector('[data-name]').value;const t=el.querySelector('[data-timeout]').value;s.timeout_s=t?Number(t):null;
      if(el.querySelector('[data-mode]')){const mode=el.querySelector('[data-mode]').value,cond={zone:el.querySelector('[data-zone]').value,min_ms:Number(el.querySelector('[data-ms]').value)};if(mode==='obj_state')cond.state=el.querySelector('[data-state]').value;s.evidence={all:[{[mode]:cond}]};}
    });const t=root.querySelector('[data-takt]')?.value;if(t!==undefined)cycle.takt_s=t?Number(t):null;
    for(const k of ['start','end']){const zone=root.querySelector(`[data-cycle-zone="${k}"]`);if(zone){cycle[k].obj_state.zone=zone.value;cycle[k].obj_state.state=root.querySelector(`[data-cycle-state="${k}"]`).value;}}
    const conditions=steps.flatMap(s=>s.evidence?.all||[]).concat([cycle.start,cycle.end]);
    for(const c of conditions){if(c?.obj_state&&!data.roi_states.some(r=>r.zone===c.obj_state.zone))data.roi_states.push({zone:c.obj_state.zone,mode:c.obj_state.state==='present'?'diff_empty':'template',positive_state:c.obj_state.state,negative_state:'not_'+c.obj_state.state});}
  }
  paint();return d;
}

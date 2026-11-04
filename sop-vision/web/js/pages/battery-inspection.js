import { openDrawer, openLightbox } from '../ui.js';
import { esc, randomKey } from '../util.js';

const faces={front:'正面',back:'背面',left:'左面',right:'右面',top:'顶面',bottom:'底面'};
const states={missing:'缺面',OK:'设备判合格',NG:'设备判不良',UNCERTAIN:'待复核',conflict:'记录冲突'};
const summaries={pending:'缺面 · 未完成',review:'待复核',NG:'存在不良',complete:'六面设备记录齐备且均为 OK'};

/** Aggregates declared equipment verdicts; model trials never overwrite them. */
export function openBatteryInspection({canWrite}) {
  let closed=false;
  let group=null;
  let busy=false;
  let models=[];
  let objectUrl=null;
  let image=null;
  let generation=0;
  let uploadKey=randomKey();
  const controller=new AbortController();
  const drawer=openDrawer({title:'电芯六面检',subtitle:'设备原判汇总 · 模型试跑单独展示',onClose:()=>{
    closed=true;generation++;controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);
  },html:`<style>.battery-inspection [hidden]{display:none!important}.battery-inspection{font-size:13px;line-height:1.7}.battery-inspection label{display:block;margin:13px 0}.battery-inspection input,.battery-inspection select{box-sizing:border-box;display:block;width:100%;padding:9px;margin-top:6px;background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);border-radius:6px;font:inherit}.battery-inspection input::file-selector-button{background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);padding:5px 9px;margin-right:10px;border-radius:4px}.battery-inspection .bi-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.battery-inspection .bi-face{border:1px solid var(--border,#34434b);padding:12px;border-radius:6px}.battery-inspection .bi-face b{display:block}.battery-inspection .bi-actions{display:flex;gap:9px;margin:16px 0;flex-wrap:wrap}.battery-inspection .bi-note{color:var(--muted,#95a6ad)}.battery-inspection [role=alert]{color:#ed958b}.battery-inspection canvas{width:100%;height:auto;display:block}.battery-inspection output{white-space:pre-wrap;display:block}.battery-inspection h3{margin:20px 0 10px}.battery-inspection .bi-face button{margin-top:8px}</style>
    <section class="battery-inspection"><p class="bi-note">同一产品、SN 与检测批次汇总六面设备结果。只有六面均为 OK、无冲突或重复图片时才齐备；不代表平台已完成视觉质量认证。</p>
    <form data-bi-group><label>产品型号<input name="product_id" required maxlength="100"></label><label>产品 SN<input name="sn" required maxlength="160"></label><label>检测批次<input name="inspection_id" required maxlength="100" placeholder="同一次六面检查使用相同批次"></label><button class="btn btn-primary" type="submit">锁定并查看</button><button class="btn" type="button" data-bi-change hidden>切换产品 / 批次</button></form>
    <p data-bi-error role="alert"></p><p data-bi-state role="status">请先填写产品、SN 与检测批次。</p><div class="bi-grid" data-bi-faces></div>
    <form data-bi-upload hidden><h3>补充面图与设备原判</h3><label>设备 ID<input name="device_id" required maxlength="96" pattern="[A-Za-z0-9_.-]+" placeholder="CELL-AOI-001"></label><label>设备采集时间<input name="observed_at" type="datetime-local" required></label><div class="bi-grid"><label>面别<select name="face">${Object.entries(faces).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label><label>设备原判<select name="result"><option value="UNCERTAIN">待复核（默认）</option><option value="OK">OK · 设备判合格</option><option value="NG">NG · 设备判不良</option></select></label></div><label>该面图片<input name="file" type="file" accept="image/png,image/jpeg,image/webp,image/bmp" required></label><p class="bi-note">图片最多 10 MB。设备原判按原始结果填写；模型零检出不会自动改为 OK。同面冲突记录不会被新记录覆盖。</p><canvas data-bi-image hidden></canvas>
    <details><summary>已有模型试跑（不改设备原判）</summary><label>检测模型<select data-bi-model><option value="">正在读取…</option></select></label><button class="btn" type="button" data-bi-trial>试跑当前图片</button><output data-bi-trial-result></output></details><div class="bi-actions"><button class="btn btn-primary" type="submit">保存该面设备结果</button><button class="btn" type="button" data-bi-refresh>刷新汇总</button></div></form></section>`});
  const body=drawer.body;
  const find=s=>body.querySelector(s);
  const form=find('[data-bi-upload]');
  const grouping=find('[data-bi-group]');
  const canvas=find('canvas');
  function error(message=''){if(!closed)find('[data-bi-error]').textContent=message;}
  function resetKey(){uploadKey=randomKey();}
  function setBusy(value){
    busy=value;
    body.querySelectorAll('button').forEach(button=>button.disabled=value);
    form.querySelectorAll('input,select').forEach(input=>input.disabled=value);
  }
  async function request(url,options={}){
    const response=await fetch(url,{...options,signal:controller.signal});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:`请求失败（${response.status}），请检查输入`);
    return data;
  }
  async function refresh(){
    if(!group)return;
    const data=await request(`/api/battery-inspections?${new URLSearchParams(group)}`);
    if(closed)return;
    find('[data-bi-state]').textContent=summaries[data.status]||data.status;
    find('[data-bi-faces]').innerHTML=data.faces.map(face=>`<article class="bi-face"><b>${esc(faces[face.face]||face.face)}</b><span>${esc(states[face.status]||face.status)}</span>${face.records.map(record=>`<div><small>${esc(record.device_id)} · ${esc(record.result)}</small>${(record.image_urls||[]).map(url=>`<button class="btn" type="button" data-bi-url="${esc(url)}">查看原图</button>`).join('')}</div>`).join('')}</article>`).join('');
    body.querySelectorAll('[data-bi-url]').forEach(button=>button.onclick=()=>openLightbox(button.dataset.biUrl,'六面检设备原图'));
  }
  grouping.onsubmit=async event=>{
    event.preventDefault();if(busy)return;
    group=Object.fromEntries(['product_id','sn','inspection_id'].map(name=>[name,grouping.elements[name].value.trim()]));
    if(Object.values(group).some(v=>!v)){error('产品、SN 和检测批次不能为空');group=null;return;}
    setBusy(true);error();
    try{
      await refresh();if(closed)return;
      grouping.querySelectorAll('input').forEach(input=>input.disabled=true);
      grouping.querySelector('[type=submit]').hidden=true;find('[data-bi-change]').hidden=false;
      form.hidden=!canWrite;resetKey();
    }catch(e){error(e.message);group=null;}finally{if(!closed)setBusy(false);}
  };
  find('[data-bi-change]').onclick=()=>{
    if(busy)return;
    group=null;form.hidden=true;grouping.querySelectorAll('input').forEach(input=>input.disabled=false);
    grouping.querySelector('[type=submit]').hidden=false;find('[data-bi-change]').hidden=true;
    find('[data-bi-faces]').innerHTML='';find('[data-bi-state]').textContent='请重新锁定产品与检测批次。';
    // A new group must not inherit a previous product's image or trial evidence.
    generation++;form.elements.file.value='';image=null;canvas.hidden=true;
    if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=null;
    find('[data-bi-trial-result]').textContent='';form.elements.result.value='UNCERTAIN';resetKey();
  };
  const now=new Date();form.elements.observed_at.value=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,16);
  form.onchange=resetKey;
  form.elements.file.onchange=async()=>{
    resetKey();const token=++generation;image=null;canvas.hidden=true;find('[data-bi-trial-result]').textContent='';
    if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=null;error();
    try{
      const file=form.elements.file.files[0];if(!file||file.size>10*1024*1024)throw new Error('请选择不超过 10 MB 的图片');
      objectUrl=URL.createObjectURL(file);const loaded=new Image();loaded.src=objectUrl;await loaded.decode();
      if(closed||token!==generation)return;
      image=loaded;canvas.width=loaded.naturalWidth;canvas.height=loaded.naturalHeight;
      canvas.getContext('2d').drawImage(image,0,0);canvas.hidden=false;
    }catch(e){if(!closed&&token===generation)error(e.message);}
  };
  find('[data-bi-refresh]').onclick=async()=>{if(busy)return;setBusy(true);try{await refresh();}catch(e){error(e.message);}finally{if(!closed)setBusy(false);}};
  find('[data-bi-trial]').onclick=async()=>{
    if(busy||!canWrite)return;error();
    const model=models.find(m=>m.id===find('[data-bi-model]').value);
    if(!model?.available||!image){error('未检测：请选择可用模型并上传当前面图片');return;}
    setBusy(true);find('[data-bi-trial-result]').textContent='正在试跑…';
    try{
      const data=new FormData();data.append('file',form.elements.file.files[0]);
      const result=await request(`/api/models/${encodeURIComponent(model.id)}/trial`,{method:'POST',body:data});if(closed)return;
      const context=canvas.getContext('2d');context.drawImage(image,0,0);context.strokeStyle='#ffbc63';context.lineWidth=Math.max(2,canvas.width/300);
      if(result.task==='classify'){
        find('[data-bi-trial-result]').textContent=`分类预测：${result.predicted_label} · ${(Number(result.confidence)*100).toFixed(1)}%\n${result.probabilities.map(p=>`${p.label} ${(Number(p.score)*100).toFixed(1)}%`).join(' / ')}\n本次耗时 ${Number(result.elapsed_ms).toFixed(0)} ms。预测 OK 不代表已验收或产品合格；试跑结果未保存为设备原判。`;
        return;
      }
      for(const target of result.targets){const [x0,y0,x1,y1]=target.xyxy_norm;context.strokeRect(x0*canvas.width,y0*canvas.height,(x1-x0)*canvas.width,(y1-y0)*canvas.height);}
      find('[data-bi-trial-result]').textContent=`试跑检出 ${result.targets.length} 个目标 · ${Number(result.elapsed_ms).toFixed(0)} ms\n${result.targets.map(t=>`${result.classes[t.label_id]||t.label_id} ${(t.confidence*100).toFixed(1)}%`).join('、')||'没有检出目标，不等于合格。'}\n试跑结果未保存为设备原判。`;
    }catch(e){if(!closed){find('[data-bi-trial-result]').textContent='未检测：试跑未完成。';error(e.message);}}
    finally{if(!closed)setBusy(false);}
  };
  form.onsubmit=async event=>{
    event.preventDefault();if(busy||!canWrite||!group)return;error();
    const file=form.elements.file.files[0];if(!file||file.size>10*1024*1024){error('请选择不超过 10 MB 的图片');return;}
    const metadata={...group,device_id:form.elements.device_id.value.trim(),face:form.elements.face.value,
      observed_at:new Date(form.elements.observed_at.value).toISOString(),result:form.elements.result.value,
      source_kind:'other',defects:[],idempotency_key:uploadKey};
    setBusy(true);
    try{
      const data=new FormData();data.append('metadata',JSON.stringify(metadata));data.append('files',file);
      await request('/api/equipment-results',{method:'POST',body:data});if(closed)return;
      await refresh();find('[data-bi-state]').textContent+=' · 该面已保存';
    }catch(e){error(e.message);}finally{if(!closed)setBusy(false);}
  };
  if(canWrite)request('/api/models').then(data=>{
    if(closed)return;models=data.filter(m=>!m.task||["detect","classify"].includes(m.task));
    find('[data-bi-model]').innerHTML='<option value="">选择适用于当前表面的模型</option>'+models.map(m=>`<option value="${esc(m.id)}" ${m.available?'':'disabled'}>${esc(m.name)} · ${m.task==='classify'?'分类':'检测'}${m.available?'':' · 不可用'}</option>`).join('');
  }).catch(e=>{if(!closed){find('[data-bi-model]').innerHTML='<option value="">模型列表不可用</option>';error(e.message);}});
  return drawer;
}

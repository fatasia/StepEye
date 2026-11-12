import { openDrawer } from "../ui.js";
import { esc } from "../util.js";
import { request, post } from "./application-form.js";

export async function openAnomaly(context={}) {
  let scene=context.scene||"", backend=context.backend||"grid", dead=false, busy=false, canWrite=false;
  const d=openDrawer({title:context.name||"良品外观检测",subtitle:"准备良品 → 核对效果 → 保存版本",html:`
    <label class="field"><span>检测应用</span><select data-scenes></select></label>
    <label class="field"><span>新场景名称</span><input data-scene maxlength="80" placeholder="例如 外壳外观"></label>
    <details><summary>算法与阈值</summary><label class="field"><span>特征方式</span><select data-backend><option value="grid">网格基线</option><option value="dinov2">DINOv2 深度特征</option></select></label>
    <label class="field"><span>光照处理</span><select data-lighting><option value="off">关闭</option><option value="auto">自动曝光校正</option></select></label>
    <label class="field"><span>异常阈值</span><input data-threshold type="number" min="0.02" max="1" step="0.01" value="0.12"></label><button class="btn" data-save-threshold>保存设置</button><p>自动校正同时用于良品建库和检测，原始图片保留。切换后重新核对阈值；过曝或全黑丢失的细节无法恢复。</p></details>
    <label class="field"><span>良品（每次1–8张）</span><input data-ok type="file" multiple accept="image/*"></label><button class="btn" data-register>登记良品</button><div data-refs style="display:flex;gap:8px;flex-wrap:wrap"></div>
    <hr><label class="field"><span>待检图片</span><input data-image type="file" accept="image/*"></label><button class="btn btn-primary" data-inspect>检查图片</button> <button class="btn" data-preview-exposure>预览曝光校正</button><p data-result role="status"></p><img data-exposure-preview alt="曝光校正预览" style="width:100%;max-width:480px" hidden>
    <div style="position:relative"><img data-photo alt="检测图片" style="width:100%" hidden><canvas data-heat style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none" hidden></canvas></div>
    <details><summary>辅助校准</summary><p>分别选择另一拍摄批次的正常、异常图片，比较分数后决定是否保存建议阈值。</p><label class="field"><span>正常核对图</span><input data-normal type="file" accept="image/*"></label><label class="field"><span>异常核对图</span><input data-abnormal type="file" accept="image/*"></label><button class="btn" data-calibrate>计算建议</button></details>
    <details><summary>参考版本与检测记录</summary><select data-versions></select><button class="btn" data-restore>恢复此版本</button><div data-history></div></details>`,onClose:()=>{dead=true;}});
  d.body.classList.add('workflow');
  const q=s=>d.body.querySelector(s), encode=()=>encodeURIComponent(q('[data-scene]').value.trim());
  if(context.station){
    const button=document.createElement('button');button.className='btn';button.textContent='使用当前相机画面';
    q('[data-image]').parentElement.after(button);
    button.onclick=async()=>{try{const r=await fetch(`/api/stations/${encodeURIComponent(context.station)}/snapshot.jpg?raw=true`);if(!r.ok)throw new Error('当前没有可用画面');const transfer=new DataTransfer();transfer.items.add(new File([await r.blob()],'camera.jpg',{type:'image/jpeg'}));q('[data-image]').files=transfer.files;q('[data-result]').textContent='已采集当前画面，点击检查图片。';}catch(e){showError(e);}};
  }
  q('[data-scene]').value=scene;q('[data-backend]').value=backend;
  if(context.scene){q('[data-scene]').readOnly=true;q('[data-scene]').parentElement.hidden=true;q('[data-scenes]').parentElement.hidden=true;}
  async function load(){
    const rows=await request('/api/anomaly/scenes');if(dead)return;
    q('[data-scenes]').innerHTML='<option value="">新建场景</option>'+rows.map(r=>`<option value="${esc(r.scene)}">${esc(r.scene)}</option>`).join('');
    q('[data-scenes]').value=q('[data-scene]').value;
    const row=rows.find(r=>r.scene===q('[data-scene]').value);
    q('[data-backend]').disabled=!!row;
    if(!row)return;
    q('[data-threshold]').value=row.threshold;q('[data-backend]').value=row.backend||'grid';q('[data-lighting]').value=row.lighting||'off';
    const info=await request(`/api/anomaly/${encode()}/details`);if(dead)return;
    q('[data-refs]').innerHTML=info.refs.map(id=>`<div><img alt="已登记良品" src="/api/anomaly/${encode()}/images/${id}.jpg" style="width:70px;height:70px;object-fit:contain">${canWrite?`<button class="btn" data-remove-ref="${id}">移除</button>`:''}</div>`).join('');
    q('[data-refs]').querySelectorAll('[data-remove-ref]').forEach(button=>button.onclick=()=>act(async()=>{if(!confirm('移除这张良品并重建参考库？旧版本会保留。'))return;await request(`/api/anomaly/${encode()}/refs/${button.dataset.removeRef}`,{method:'DELETE'});await load();}));
    q('[data-versions]').innerHTML=info.versions.map((v,i)=>`<option value="${v}">历史版本 ${info.versions.length-i}</option>`).join('');
    q('[data-history]').innerHTML=info.history.map(r=>`<p>${new Date(r.created_at*1000).toLocaleString()} · ${esc(r.verdict)} · ${r.score}</p>`).join('')||'<p>还没有检测记录。</p>';
  }
  q('[data-scenes]').onchange=()=>{q('[data-scene]').value=q('[data-scenes]').value;q('[data-backend]').disabled=false;q('[data-refs]').innerHTML='';load().catch(showError);};
  function showError(e){if(!dead)q('[data-result]').textContent=e.message;}
  async function act(fn){
    if(busy)return;
    busy=true;
    const controls=[...d.body.querySelectorAll('button')].map(button=>[button,button.disabled]);
    controls.forEach(([button])=>{button.disabled=true;});
    try{await fn();}catch(e){showError(e);}finally{
      busy=false;
      controls.forEach(([button,disabled])=>{if(button.isConnected)button.disabled=disabled;});
    }
  }
  q('[data-register]').onclick=()=>act(async()=>{
    const files=[...q('[data-ok]').files];if(!files.length||files.length>8||!encode())throw new Error('请输入名称并选择1–8张良品');
    const form=new FormData();files.forEach(f=>form.append('files',f));form.append('backend',q('[data-backend]').value);form.append('threshold',q('[data-threshold]').value);form.append('lighting',q('[data-lighting]').value);
    const value=await request(`/api/anomaly/${encode()}/register`,{method:'POST',body:form});q('[data-result]').textContent=`已登记 ${value.added} 张良品，可以检查图片。`;await load();
  });
  q('[data-inspect]').onclick=()=>act(async()=>{
    const file=q('[data-image]').files[0];if(!file)throw new Error('请选择待检图片');const form=new FormData();form.append('image',file);
    q('[data-result]').textContent='正在检查…';const value=await request(`/api/anomaly/${encode()}/inspect`,{method:'POST',body:form});if(dead)return;
    q('[data-result]').textContent=`${{PASS:'正常',NG:'发现异常',UNCERTAIN:'待复核'}[value.verdict]} · 异常分 ${value.score} / 阈值 ${value.threshold}${value.exposure?.unusable?' · 画面曝光严重异常，请调整拍摄':''}`;
    const photo=q('[data-photo]');photo.src=`/api/anomaly/${encode()}/images/${value.id}.jpg`;photo.hidden=false;
    const heat=q('[data-heat]'),map=value.heatmap;heat.width=map[0].length*20;heat.height=map.length*20;heat.hidden=false;const ctx=heat.getContext('2d');ctx.clearRect(0,0,heat.width,heat.height);
    map.forEach((r,y)=>r.forEach((v,x)=>{ctx.fillStyle=`rgba(255,60,30,${Math.min(.7,v/Math.max(value.threshold,.01)*.35)})`;ctx.fillRect(x*20,y*20,20,20);}));await load();
  });
  q('[data-preview-exposure]').onclick=()=>act(async()=>{const file=q('[data-image]').files[0];if(!file||!encode())throw new Error('请先选择场景和待检图片');const form=new FormData();form.append('image',file);const result=await request(`/api/anomaly/${encode()}/exposure-preview`,{method:'POST',body:form});const img=q('[data-exposure-preview]');img.src='data:image/jpeg;base64,'+result.image;img.hidden=false;q('[data-result]').textContent=result.unusable?'画面严重过曝或过暗，无法恢复丢失的细节，请调整相机。':`曝光校正预览 · 原图亮度中位数 ${result.median} · 亮度增益 ${result.gain}`;});
  q('[data-save-threshold]').onclick=()=>act(async()=>{await request(`/api/anomaly/${encode()}/settings`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({threshold:Number(q('[data-threshold]').value),lighting:q('[data-lighting]').value})});q('[data-result]').textContent='设置已保存；更换光照处理后请重新核对阈值。';await load();});
  q('[data-restore]').onclick=()=>act(async()=>{const version=q('[data-versions]').value;if(!version)throw new Error('没有历史版本');if(!confirm('恢复此参考库与阈值？当前版本会保留。'))return;await post(`/api/anomaly/${encode()}/versions/${version}/restore`);await load();});
  q('[data-calibrate]').onclick=()=>act(async()=>{const normal=q('[data-normal]').files[0],abnormal=q('[data-abnormal]').files[0];if(!normal||!abnormal)throw new Error('请选择正常和异常核对图片');const form=new FormData();form.append('normal',normal);form.append('abnormal',abnormal);const value=await request(`/api/anomaly/${encode()}/calibrate`,{method:'POST',body:form});q('[data-result]').textContent=value.message;if(value.separable)q('[data-threshold]').value=value.suggested;});
  const me=await request('/api/auth/me');canWrite=me.role!=='viewer';if(!canWrite)d.body.querySelectorAll('button,input').forEach(x=>x.disabled=true);
  load().catch(showError);return d;
}

import { request, post, newApplication, connectInput } from './application-form.js';
import { openAnomaly } from './anomaly.js';
import { openStepEditor } from './step-editor.js';
import { openHoldoutEvaluation } from './holdout-eval.js';
import { openReleaseManagement } from './release-manage.js';
import { esc } from '../util.js';

const TASK={safety:'安全监测',sop:'SOP 防错',appearance:'外观异常',existing:'已有工位'};
const STATE={draft:'待接入',ready:'待验证',trial:'试运行',running:'运行中',paused:'已暂停',archived:'已归档'};
const nextStep=r=>r.state==='draft'?(r.task==='appearance'?'登记良品并接入画面':'接入画面'):{ready:'配置后试运行',trial:'核对效果并启用',running:'查看运行情况',paused:'继续配置',archived:'恢复应用'}[r.state]||'查看详情';

export function render(root,params={}){
  let dead=false, rows=[], selected=null, canWrite=false, showArchived=false;
  root.innerHTML=`<section class="panel"><header class="panel-head"><div><h2>我的应用</h2><p class="muted">选任务，接画面，验证后启用。</p></div><button class="btn btn-primary" data-new-app hidden>新建应用</button></header><div class="panel-body"><p data-auth-hint class="muted" hidden>还没有启用账号登录。<a href="#/settings?tab=general">创建管理员</a></p><div data-app-list>正在读取…</div></div></section>
    <section class="panel" data-app-detail hidden><div class="panel-body" data-app-body></div></section><p data-app-error role="alert"></p>
    <details><summary>高级工具与系统设置</summary><p><a class="btn" href="#/models">模型实验室</a> <a class="btn" href="#/settings?tab=ai">AI 服务</a> <a class="btn" href="#/settings?tab=notifications">告警推送</a> <a class="btn" href="#/settings?tab=operations">环境检查</a> <button class="btn" data-archives>显示已归档应用</button></p></details>`;
  const q=s=>root.querySelector(s), showError=e=>{if(!dead)q('[data-app-error]').textContent=e.message;};
  async function load(){
    try{const [list,me]=await Promise.all([request('/api/applications?include_archived='+showArchived),request('/api/auth/me')]);if(dead)return;rows=list;canWrite=me.role!=='viewer';
      q('[data-new-app]').hidden=!canWrite;
      q('[data-app-list]').innerHTML=rows.length?`<div class="app-grid">${rows.map(r=>`<button class="app-card" data-app="${esc(r.id)}"><span class="app-card-top"><strong>${esc(r.name)}</strong><span class="app-card-state">${esc(STATE[r.state]||r.state)}</span></span><span class="app-card-type">${esc(TASK[r.task]||r.task)}</span><span class="app-card-next">${esc(nextStep(r))} <span aria-hidden="true">→</span></span></button>`).join('')}</div>`:'<h3>创建第一个应用</h3><p>选择安全监测、SOP 防错或外观异常。没有相机也可以先用演示画面。</p>';
      root.querySelectorAll('[data-app]').forEach(b=>b.onclick=()=>detail(rows.find(r=>r.id===b.dataset.app)));
      if(selected||params.id)detail(rows.find(r=>r.id===(selected?.id||params.id))||null);
    }catch(e){showError(e);}
  }
  async function detail(row){
    selected=row;q('[data-app-detail]').hidden=!row;if(!row)return;
    const body=q('[data-app-body]');
    if(row.state==='archived'){
      body.innerHTML=`<h2>${esc(row.name)}</h2><p>已归档，数据与版本保留。</p>${canWrite?'<button class="btn" data-restore-app>恢复应用</button>':''}`;
      body.querySelector('[data-restore-app]')?.addEventListener('click',async()=>{try{await post(`/api/applications/${row.id}/restore`);await load();}catch(e){showError(e);}});return;
    }
    body.innerHTML=`<h2>${esc(row.name)}</h2><p>${TASK[row.task]} · ${STATE[row.state]||row.state}</p>
      ${row.task==='appearance'?'<p>准备良品 → 核对效果 → 保存版本</p>':'<p>接入画面 → 配置检查 → 试运行 → 正式启用</p>'}
      <div style="display:flex;flex-wrap:wrap;gap:10px" data-app-actions>
      ${row.task==='appearance'?`<button class="btn ${row.state==='trial'?'':'btn-primary'}" data-appearance>管理良品与检测</button>`:''}
      ${row.running?`<a class="btn" href="#/live/${esc(row.id)}">查看实况</a><a class="btn" href="#/config/${esc(row.id)}">检查区域与规则</a>${row.task==='sop'?'<button class="btn" data-steps>编辑步骤</button>':''}`:''}
      ${canWrite&&!row.legacy?`${!row.running?'<button class="btn" data-connect>接入 / 继续配置</button>':'<button class="btn" data-pause>暂停</button>'}${row.state==='trial'?'<button class="btn btn-primary" data-enable>确认效果并启用</button>':''}`:''}
      </div><p data-app-note role="status"></p><div data-events></div>
      ${canWrite&&!row.legacy&&row.task!=='appearance'?`<details><summary>画面优化</summary><p>自动曝光仅作用于检测输入；原图和画质监测不变。请暂停后修改，并重新核对检测效果。</p><label class="field"><span>曝光校正</span><select data-exposure-setting ${row.running?'disabled':''}><option value="off" ${row.exposure_correction!=='auto'?'selected':''}>不调整</option><option value="auto" ${row.exposure_correction==='auto'?'selected':''}>自动校正</option></select></label><button class="btn" data-save-exposure ${row.running?'disabled':''}>保存画面设置</button></details>`:''}
      ${row.project_id?'<details><summary>样本增强与版本</summary><p>样本 → 训练候选 → 评测 → 发布；当前应用会自动带入。</p><button class="btn" data-samples>补样本 / 训练</button> <button class="btn" data-eval>验证模型效果</button> <button class="btn" data-release>发布 / 回滚</button></details>':''}
      ${canWrite&&!row.legacy?'<details><summary>维护</summary><button class="btn" data-archive>暂停并归档</button></details>':''}`;
    body.querySelector('[data-archive]')?.addEventListener('click',async()=>{if(!confirm('暂停并归档此应用？数据与版本会保留。'))return;try{await request(`/api/applications/${row.id}`,{method:'DELETE'});selected=null;q('[data-app-detail]').hidden=true;await load();}catch(e){showError(e);}});
    body.querySelector('[data-save-exposure]')?.addEventListener('click',async()=>{try{await request(`/api/applications/${row.id}/exposure`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:body.querySelector('[data-exposure-setting]').value})});await load();}catch(e){showError(e);}});
    body.querySelector('[data-appearance]')?.addEventListener('click',()=>openAnomaly({scene:row.id,backend:row.backend,name:row.name,station:row.running?row.id:''}).catch(showError));
    body.querySelector('[data-connect]')?.addEventListener('click',()=>connectInput(row,load));
    body.querySelector('[data-steps]')?.addEventListener('click',()=>openStepEditor(row.id).catch(showError));
    body.querySelector('[data-pause]')?.addEventListener('click',async()=>{try{await post(`/api/applications/${row.id}/pause`);await load();}catch(e){showError(e);}});
    body.querySelector('[data-enable]')?.addEventListener('click',async()=>{if(!confirm('已核对正常与异常操作的结果？启用后将按规则产生告警。'))return;try{await post(`/api/applications/${row.id}/run`,{production:true,confirmed:true});await load();}catch(e){showError(e);}});
    body.querySelector('[data-samples]')?.addEventListener('click',()=>{location.hash=`#/labeling?project=${encodeURIComponent(row.project_id)}&engine=${encodeURIComponent(row.engine_id)}`;});
    body.querySelector('[data-eval]')?.addEventListener('click',()=>openHoldoutEvaluation({canWrite,project:row.project_id,engine:params.candidate||row.engine_id}));
    body.querySelector('[data-release]')?.addEventListener('click',()=>openReleaseManagement({canWrite,station:row.id,project:row.project_id}));
    if(row.state==='trial'){
      body.querySelector('[data-app-note]').textContent='试运行仅记录证据，不推送告警。核对画面、区域与正常 / 异常操作后再启用。';
      try{const events=await request(`/api/applications/${row.id}/events`);if(!dead&&selected?.id===row.id)body.querySelector('[data-events]').textContent=`本次可复核事件 ${events.length} 条；周期证据可在追溯中查看。`;}catch(e){showError(e);}
    }
  }
  q('[data-new-app]').onclick=()=>newApplication(row=>{selected=row;load().then(()=>{if(row.task!=='appearance')connectInput(row,load);});}).catch(showError);
  q('[data-archives]').onclick=()=>{showArchived=!showArchived;q('[data-archives]').textContent=showArchived?'隐藏已归档应用':'显示已归档应用';load();};
  request('/api/health').then(health=>{if(!dead&&!health.auth)q('[data-auth-hint]').hidden=false;}).catch(()=>{});
  load();return()=>{dead=true;};
}

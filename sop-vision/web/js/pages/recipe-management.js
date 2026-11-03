import { openDrawer } from '../ui.js';
import { esc, fmtTime } from '../util.js';

/** Versions and switching remain owned by RecipeService; the UI sends no model paths. */
export function openRecipeManagement({canWrite}) {
  let closed=false;
  let busy=false;
  let station='';
  let recipes=[];
  let current=null;
  let generation=0;
  const controller=new AbortController();
  const drawer=openDrawer({title:'产品配方',subtitle:'保存当前规则 · 切换版本 · 回滚',onClose:()=>{closed=true;generation++;controller.abort();},html:`
    <style>.recipe-management [hidden]{display:none!important}.recipe-management{font-size:13px;line-height:1.7}.recipe-management label{display:block;margin:14px 0}.recipe-management input,.recipe-management select{display:block;box-sizing:border-box;width:100%;margin-top:6px;padding:9px;border:1px solid var(--border,#34434b);border-radius:6px;background:var(--panel,#172027);color:inherit;font:inherit}.recipe-management .rm-row{padding:12px 0;border-bottom:1px solid var(--border,#34434b)}.recipe-management .rm-row strong{display:block}.recipe-management small,.recipe-management .rm-note{color:var(--muted,#95a6ad)}.recipe-management .rm-actions{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.recipe-management [role=alert]{color:#ee958b}.recipe-management details{margin:18px 0}.recipe-management h3{margin:20px 0 8px}.recipe-management .rm-current{padding:12px;border:1px solid var(--border,#34434b);border-radius:6px}</style>
    <section class="recipe-management"><p class="rm-note">配方保存区域、SOP、安全规则、ROI 与画质配置。不改变相机、设备或模型路径；切换会重载工位，生产周期进行中不能切换。</p>
    <label>工位<select data-rm-station><option value="">正在读取…</option></select></label><p data-rm-error role="alert"></p><p data-rm-message role="status"></p><div class="rm-current" data-rm-current>请选择工位。</div>
    <details data-rm-create ${canWrite?'':'hidden'}><summary>将当前规则保存为新产品配方</summary><form data-rm-form><label>配方名称<input name="name" required maxlength="120" placeholder="例如：电芯 A / 标准工艺"></label><label>产品机型<input name="model_key" required maxlength="120" placeholder="产品型号，不是 AI 模型路径"></label><button class="btn btn-primary" type="submit">保存当前规则</button></form></details>
    <h3>配方与版本</h3><label>配方<select data-rm-recipe><option value="">暂无配方</option></select></label><div class="rm-actions"><button class="btn" data-rm-snapshot ${canWrite?'':'hidden'}>当前规则保存为新版本</button><button class="btn" data-rm-refresh>刷新</button></div><div data-rm-versions></div>
    <details><summary>切换历史</summary><div data-rm-history>暂无记录。</div></details>${canWrite?'':'<p class="rm-note">只读账号，只能查看配方与历史。</p>'}</section>`});
  const body=drawer.body;
  const find=s=>body.querySelector(s);
  function message(text=''){if(!closed)find('[data-rm-message]').textContent=text;}
  function error(text=''){if(!closed)find('[data-rm-error]').textContent=text;}
  async function request(url,options={}){
    const response=await fetch(url,{...options,signal:controller.signal});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:`请求失败（${response.status}）`);
    return data;
  }
  const post=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  function disabled(value){
    busy=value;body.querySelectorAll('button,input,select').forEach(e=>e.disabled=value);
    if(!value){find('[data-rm-snapshot]').disabled=!find('[data-rm-recipe]').value||!station;find('[data-rm-form] button').disabled=!station;}
  }
  async function action(fn){
    if(busy||closed)return;
    disabled(true);error();message();
    try{await fn();}catch(e){if(e.name!=='AbortError')error(e.message);}
    finally{if(!closed)disabled(false);}
  }
  async function versions(){
    const rid=find('[data-rm-recipe]').value;
    if(!rid){find('[data-rm-versions]').textContent='当前工位还没有配方。';return;}
    const rows=await request(`/api/recipes/${encodeURIComponent(rid)}/versions`);
    if(closed)return;
    find('[data-rm-versions]').innerHTML=rows.map(row=>{
      const active=current?.active?.version_id===row.id;
      const older=row.version<rows[0].version;
      return `<article class="rm-row"><strong>版本 ${Number(row.version)}${active?' · 当前生效':''}</strong><small>${esc(fmtTime(row.created))} · ${esc(row.actor)}</small><div class="rm-actions">${canWrite&&!active?`<button class="btn ${older?'':'btn-primary'}" data-rm-switch="${esc(row.id)}" data-rm-version="${Number(row.version)}">${older?'回滚到此版本':'切换到此版本'}</button>`:''}</div></article>`;
    }).join('');
    body.querySelectorAll('[data-rm-switch]').forEach(button=>button.onclick=()=>{
      if(!canWrite||busy)return;
      if(!confirm(`将工位当前规则切换为版本 ${button.dataset.rmVersion}？工位将重载。`))return;
      action(async()=>{
        await request(`/api/stations/${encodeURIComponent(station)}/recipe`,post({version_id:button.dataset.rmSwitch}));
        await load(rid);message(`已切换到版本 ${button.dataset.rmVersion}。`);
      });
    });
  }
  async function load(preferred){
    if(!station)return;
    const token=++generation;
    const [all,state]=await Promise.all([request('/api/recipes'),request(`/api/stations/${encodeURIComponent(station)}/recipe`)]);
    if(closed||token!==generation)return;
    recipes=all.filter(r=>r.station_id===station);current=state;
    const activeRecipe=recipes.find(r=>r.id===state.active?.recipe_id);
    find('[data-rm-current]').textContent=state.active?`当前配方：${activeRecipe?.name||state.active.recipe_id} · 机型 ${state.active.model_key}`:'当前工位配置未匹配已生效配方版本；可先保存当前规则。';
    const select=find('[data-rm-recipe]');
    const chosen=preferred||select.value||state.active?.recipe_id;
    select.innerHTML=recipes.length?recipes.map(r=>`<option value="${esc(r.id)}">${esc(r.name)} · ${esc(r.model_key)}</option>`).join(''):'<option value="">暂无配方</option>';
    if(recipes.some(r=>r.id===chosen))select.value=chosen;
    find('[data-rm-history]').innerHTML=state.history.length?state.history.map(row=>`<div class="rm-row"><strong>${row.status==='applied'?'已应用':'切换失败'} · ${esc(row.model_key)}</strong><small>${esc(fmtTime(row.created))} · ${esc(row.actor)}</small>${row.status==='failed'?`<p>${row.restored?'原配置已恢复':'恢复失败，需人工检查'}</p>`:''}</div>`).join(''):'暂无切换记录。';
    await versions();
  }
  find('[data-rm-station]').onchange=()=>action(async()=>{
    station=find('[data-rm-station]').value;find('[data-rm-recipe]').value='';await load();
  });
  find('[data-rm-recipe]').onchange=()=>action(versions);
  find('[data-rm-refresh]').onclick=()=>action(()=>load());
  find('[data-rm-form]').onsubmit=event=>{
    event.preventDefault();if(!canWrite||!station)return;
    const form=event.currentTarget;
    action(async()=>{
      const data=await request('/api/recipes',post({name:form.elements.name.value.trim(),model_key:form.elements.model_key.value.trim(),station_id:station,patch:{}}));
      await load(data.id);message('当前规则已保存为配方版本；保存不会自动切换。');
    });
  };
  find('[data-rm-snapshot]').onclick=()=>{
    const rid=find('[data-rm-recipe]').value;if(!canWrite||!rid)return;
    action(async()=>{
      await request(`/api/recipes/${encodeURIComponent(rid)}/versions`,post({patch:{},snapshot_current:true}));
      await load(rid);message('当前工位规则已保存为新版本；尚未切换。');
    });
  };
  action(async()=>{
    const rows=await request('/api/stations');if(closed)return;
    const stations=rows.filter(row=>row.meta?.id);
    find('[data-rm-station]').innerHTML=stations.length?stations.map(row=>`<option value="${esc(row.meta.id)}">${esc(row.meta.name||row.meta.id)}</option>`).join(''):'<option value="">没有可用工位</option>';
    station=find('[data-rm-station]').value;
    if(station)await load();else message('请先配置工位，再保存配方。');
  });
  return drawer;
}

/** Runtime availability only. Policy writes always bind an explicit active release. */
export async function mountReleaseMonitor(host, release, {isAdmin, isClosed, onError}) {
  const labels = {disabled:'未开启',monitoring:'监控中',superseded:'版本已切换',
    rollback_intent:'已触发回滚',pending_runtime_reload:'等待运行时重载',
    rolled_back:'已回滚并生效',blocked:'回滚受阻',rollback_failed:'回滚失败'};
  const reasons = {previous_release_unverified:'前一版本未通过校验',runtime_errors:'运行错误累计超限',
    frame_availability:'持续无成功帧',active_release_changed:'当前发布版本已改变',
    restart_after_rollback_intent:'重启后等待核对回滚结果'};
  const endpoint = `/api/model-release-monitor/${encodeURIComponent(release.scene)}`;
  let state = null;
  let busy = false;
  const valid = ()=>!isClosed() && host.isConnected;
  async function read() {
    host.textContent = '读取运行监控…';
    try {
      const response = await fetch(endpoint);
      const body = await response.json();
      if (response.status !== 404 && !response.ok) throw new Error(typeof body.detail==='string'?body.detail:'无法读取发布监控');
      if (!valid()) return;
      state = response.status===404 ? null : body;
      paint();
    } catch(error) {if(valid()){host.textContent='监控状态读取失败';onError(error);}}
  }
  function paint() {
    if (!valid()) return;
    host.replaceChildren();
    const same = state?.release_id === release.id;
    const terminal = same && !['disabled','monitoring'].includes(state.status);
    const text = document.createElement('p');
    text.style.margin='8px 0';
    text.textContent = `运行自动回滚：${same ? (labels[state.status]||state.status) : '未开启'}`;
    host.append(text);
    if (state && (!same || state.reason)) {
      const detail=document.createElement('small');
      detail.textContent = `${same?'':'前版：'+(labels[state.status]||state.status)+' · '}${reasons[state.reason]||state.reason||''}`;
      host.append(detail);
    }
    if (isAdmin && !terminal) {
      const button = document.createElement('button');
      button.className='btn btn-sm';
      button.dataset.rmMonitor=release.id;
      const enabled = same && state.policy?.enabled === true;
      button.textContent=enabled?'关闭自动回滚':'开启自动回滚';
      button.onclick=async()=>{
        if(busy)return;
        if(!confirm(enabled?'关闭该版本的运行可用性自动回滚？':'开启该版本运行可用性自动回滚？仅监测运行错误和成功帧，不评估识别精度。'))return;
        busy=true;button.disabled=true;
        try {
          const response=await fetch(endpoint,{method:'PUT',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({release_id:release.id,policy:{enabled:!enabled}})});
          const body=await response.json();
          if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'保存监控策略失败');
          if(valid()){state=body;paint();}
        } catch(error){if(valid())onError(error);}
        finally{busy=false;if(valid())button.disabled=false;}
      };
      host.append(button);
    } else if (terminal) {
      const note=document.createElement('small');
      note.textContent='该版本已终止自动监控，不能重新启用。';
      host.append(note);
    }
    const refresh=document.createElement('button');
    refresh.className='btn btn-sm';refresh.textContent='刷新状态';refresh.onclick=read;
    host.append(refresh);
  }
  await read();
}

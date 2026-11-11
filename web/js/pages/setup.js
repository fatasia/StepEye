import { request, post } from './application-form.js';
import { esc } from '../util.js';

export async function mountSetup(root, health) {
  const general=root.querySelector('[data-settings-panel="general"]');
  if(!health.auth){
    const box=document.createElement('section');box.className='workflow';general.append(box);
    box.innerHTML='<h3>启用账号登录</h3><p>在本机创建管理员，设置立即生效，后续启动会保留。</p><form data-account><label class="field"><span>管理员账号</span><input name="username" required autocomplete="username"></label><label class="field"><span>密码（至少8位）</span><input name="password" type="password" required minlength="8" autocomplete="new-password"></label><button class="btn" type="submit">创建 / 验证管理员并启用</button><p role="status"></p></form>';
    box.querySelector('form').onsubmit=async e=>{e.preventDefault();const form=e.target;try{await post('/api/auth/setup',{username:form.username.value,password:form.password.value});location.reload();}catch(error){box.querySelector('[role=status]').textContent=error.message;}};
  }
  const data=await request('/api/setup');
  const ops=document.createElement('section');ops.className='workflow';root.querySelector('[data-settings-panel="operations"]').append(ops);
  ops.innerHTML=`<h3>组件准备</h3><p>ONNX 推理：${data.onnx?'已就绪':'未安装'} · DINOv2：${data.dinov2?'已就绪':'未准备'} · 训练：${data.training.configured?'已配置':'待配置'}</p>
    <div><button class="btn" data-install-env>安装训练组件</button> <button class="btn" data-cancel-env hidden>取消安装</button></div><p>需要本机 Python 3.10–3.13 和网络，安装将占用数 GB 空间。</p><p data-env-result role="status">${esc(data.training_install?.message || '')}</p>
    <details><summary>使用已有训练环境</summary><label class="field"><span>独立环境</span><select data-env>${data.environments.filter(e=>e.id==='train-venv'||e.id==='managed-training').map(e=>`<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('')}</select></label><button class="btn" data-use-env>检查并启用</button></details>`;
  ops.querySelector('[data-use-env]').onclick=async()=>{try{const training=ops.querySelector('[data-env]').value;if(!training)throw new Error('没有发现独立训练环境，请按安装说明准备组件');await request('/api/setup',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({training})});ops.querySelector('[data-env-result]').textContent='训练环境已启用并保存。';}catch(e){ops.querySelector('[data-env-result]').textContent=e.message;}};
  const install=ops.querySelector('[data-install-env]');
  const cancel=ops.querySelector('[data-cancel-env]');
  async function pollInstall(){
    try{
      const state=(await request('/api/setup')).training_install;
      ops.querySelector('[data-env-result]').textContent=state.message+(state.elapsed_seconds?` · 已用 ${state.elapsed_seconds} 秒`:'');
      install.disabled=['starting','creating','installing','checking','cancelling'].includes(state.phase);
      cancel.hidden=!install.disabled;cancel.disabled=state.phase==='cancelling';
      if(install.disabled&&ops.isConnected)setTimeout(pollInstall,3000);
    }catch(e){install.disabled=false;cancel.hidden=true;ops.querySelector('[data-env-result]').textContent=e.message;}
  }
  install.onclick=async()=>{install.disabled=true;try{await post('/api/setup/training-install');await pollInstall();}catch(e){install.disabled=false;ops.querySelector('[data-env-result]').textContent=e.message;}};
  cancel.onclick=async()=>{cancel.disabled=true;try{await post('/api/setup/training-cancel');ops.querySelector('[data-env-result]').textContent='正在取消安装…';}catch(e){cancel.disabled=false;ops.querySelector('[data-env-result]').textContent=e.message;}};
  if(['starting','creating','installing','checking','cancelling'].includes(data.training_install?.phase))pollInstall();
  const wvp=document.createElement('section');wvp.className='workflow';root.querySelector('[data-settings-panel="operations"]').append(wvp);
  wvp.innerHTML=`<h3>国标平台</h3><p>${data.gb28181?.configured?'已配置 WVP 平台':'配置 WVP 后，可在接入画面时填写设备和通道编号。'}</p><label class="field"><span>平台地址</span><input data-wvp-url placeholder="http://平台地址:端口" autocomplete="off"></label><label class="field"><span>账号</span><input data-wvp-user autocomplete="off"></label><label class="field"><span>密码</span><input data-wvp-password type="password" autocomplete="new-password"></label><p>已配置的字段留空保持不变，密码不回显。</p><button class="btn" data-save-wvp>保存国标平台</button><p data-wvp-result role="status"></p>`;
  wvp.querySelector('[data-save-wvp]').onclick=async()=>{try{await request('/api/setup/gb28181',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:wvp.querySelector('[data-wvp-url]').value,username:wvp.querySelector('[data-wvp-user]').value,password:wvp.querySelector('[data-wvp-password]').value})});wvp.querySelector('[data-wvp-password]').value='';wvp.querySelector('[data-wvp-result]').textContent='平台已保存，可到应用中接入国标设备。';}catch(e){wvp.querySelector('[data-wvp-result]').textContent=e.message;}};
  const notify=document.createElement('section');notify.className='workflow';root.querySelector('[data-settings-panel="notifications"]').append(notify);
  notify.innerHTML='<h3>设置告警推送</h3><p>粘贴飞书、企业微信或钉钉机器人地址，每行一个。密钥只保存到服务器，不回显。留空并保存将关闭推送。</p><textarea data-urls rows="3" style="width:100%" autocomplete="off" aria-label="机器人地址"></textarea><details><summary>邮件与高级渠道</summary><p>也支持 mailtos://、feishu://、wecombot://、dingtalk:// 等 Apprise 地址。</p></details><button class="btn" data-save-notify>保存渠道</button> <button class="btn" data-test-notify>发送测试通知</button><p data-notify-result role="status"></p>';
  notify.querySelector('[data-save-notify]').onclick=async()=>{try{await request('/api/setup',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({notification_urls:notify.querySelector('[data-urls]').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean)})});notify.querySelector('[data-urls]').value='';notify.querySelector('[data-notify-result]').textContent='渠道已保存，可发送测试通知。';}catch(e){notify.querySelector('[data-notify-result]').textContent=e.message;}};
  notify.querySelector('[data-test-notify]').onclick=async()=>{try{const value=await post('/api/setup/notification-test');notify.querySelector('[data-notify-result]').textContent=value.accepted?'测试消息已入队，请刷新通知状态查看送达结果。':'当前没有可用渠道，请先保存配置。';}catch(e){notify.querySelector('[data-notify-result]').textContent=e.message;}};
}

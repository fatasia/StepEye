import { openDrawer } from '../ui.js';
import { esc } from '../util.js';

export async function request(url, options={}) {
  const r=await fetch(url,options), data=await r.json();
  if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'请检查输入内容');
  return data;
}
export const post=(url,body={})=>request(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});

export async function newApplication(onSaved) {
  const models=await request('/api/models');
  const d=openDrawer({title:'新建应用',subtitle:'选择要解决的问题',html:`
    <label class="field"><span>应用名称</span><input data-name placeholder="例如 装配台烟火监测" maxlength="80"></label>
    <label class="field"><span>检查任务</span><select data-task><option value="safety">安全监测</option><option value="sop">SOP 防错</option><option value="appearance">外观异常</option></select></label>
    <label class="field" data-safety><span>检查内容</span><select data-rule><option value="fire_smoke">烟雾 / 火焰</option><option value="ppe_helmet">安全帽穿戴</option><option value="intrusion">人员进入区域</option><option value="aisle_occupied">人员占用通道</option></select></label>
    <p data-recommend></p><details><summary>高级选择</summary><label class="field" data-model-field><span>检测模型</span><select data-model></select></label>
    <label class="field" data-backend-field hidden><span>外观特征</span><select data-backend><option value="grid">网格基线</option><option value="dinov2">DINOv2 深度特征</option></select></label>
    <label class="field" data-exposure-field><span>推理画面曝光</span><select data-exposure><option value="off">不调整</option><option value="auto">自动曝光校正</option></select><small>只调整检测输入，原始画面和画质监测保持不变。</small></label></details>
    <p data-error role="alert"></p><button class="btn btn-primary" data-create>创建应用</button>`});
  const q=s=>d.body.querySelector(s);
  d.body.classList.add('workflow');
  function update(){
    const task=q('[data-task]').value,rule=q('[data-rule]').value;
    const available=models.filter(m=>{const labels=(m.classes||[]).map(c=>c.toLowerCase());return m.available&&m.task==='detect'&&['yolo_raw','yolo_e2e','kairos_rtdetr','glue_yolov4'].includes(m.adapter)&&(rule==='fire_smoke'?labels.some(c=>['fire','smoke'].includes(c)):rule==='ppe_helmet'?labels.includes('person')&&labels.some(c=>['without helmet','no-helmet','no-hardhat','no hardhat'].includes(c))&&labels.some(c=>['helmet','with helmet','hardhat','hard hat'].includes(c)):labels.includes('person'));});
    if(rule==='fire_smoke')available.sort((a,b)=>{const score=m=>{const labels=m.classes.map(c=>c.toLowerCase());return (labels.includes('fire')&&labels.includes('smoke')?2:0)+(/fire|smoke/i.test(m.id)?1:0);};return score(b)-score(a);});
    q('[data-model]').innerHTML=available.map(m=>`<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');
    q('[data-safety]').hidden=task!=='safety';q('[data-model-field]').hidden=task!=='safety';q('[data-backend-field]').hidden=task!=='appearance';q('[data-exposure-field]').hidden=task==='appearance';
    q('[data-recommend]').textContent=task==='sop'?'下一步接入画面、框选区域并设置步骤。':task==='appearance'?'上传良品并核对检测效果；可自行选择是否使用 DINOv2。':available.length?`已匹配：${available[0].name}`:'没有兼容的已安装模型，可先到模型实验室准备。';
    q('[data-create]').disabled=task==='safety'&&!available.length;
  }
  q('[data-task]').onchange=update;q('[data-rule]').onchange=update;update();
  q('[data-create]').onclick=async()=>{
    const button=q('[data-create]');button.disabled=true;
    try {
      const task=q('[data-task]').value;
      const row=await post('/api/applications',{name:q('[data-name]').value,task,rule:q('[data-rule]').value,
        engine_id:task==='safety'?q('[data-model]').value:'',backend:q('[data-backend]').value,exposure_correction:task==='appearance'?'off':q('[data-exposure]').value});
      d.close();onSaved(row);
    }catch(e){q('[data-error]').textContent=e.message;button.disabled=false;}
  };
}

export function connectInput(row,onSaved){
  let verified=row.input_verified;
  const d=openDrawer({title:row.name,subtitle:'接入画面 → 验证 → 试运行',html:`
    <label class="field"><span>画面来源</span><select data-type><option value="rtsp">网络相机</option><option value="onvif">ONVIF 相机</option><option value="gb28181">国标平台（WVP）</option><option value="camera">USB 相机</option><option value="file">本地视频</option><option value="sim">演示画面</option></select></label>
    <label class="field" data-uri-field><span data-uri-label>相机 RTSP 地址</span><input data-uri placeholder="rtsp://…" autocomplete="off"></label>
    <div data-onvif hidden class="workflow"><button class="btn" data-discover>搜索局域网相机</button><label class="field"><span>发现的设备</span><select data-onvif-devices><option value="">手动填写地址</option></select></label><label class="field"><span>相机地址</span><input data-onvif-host placeholder="192.168.1.10" autocomplete="off"></label><label class="field"><span>ONVIF 端口</span><input data-onvif-port type="number" min="1" max="65535" value="80"></label><label class="field"><span>账号</span><input data-onvif-user autocomplete="off"></label><label class="field"><span>密码</span><input data-onvif-password type="password" autocomplete="new-password"></label></div>
    <div data-gb hidden class="workflow"><p>先在设置的“运行状态”中配置 WVP 平台，再填写设备和通道编号。</p><label class="field"><span>设备编号</span><input data-gb-device autocomplete="off"></label><label class="field"><span>通道编号</span><input data-gb-channel autocomplete="off"></label></div>
    <label class="field" data-file-field hidden><span>视频（最大100MB）</span><input data-video type="file" accept="video/*"></label>
    <button class="btn" data-test>测试连接</button><p data-status role="status">${verified?'已验证输入源，可继续试运行。':''}</p>
    <img data-preview alt="输入画面" style="width:100%;border-radius:8px" hidden>
    <button class="btn btn-primary" data-trial ${verified?'':'disabled'}>开始试运行</button>`});
  const q=s=>d.body.querySelector(s);
  d.body.classList.add('workflow');
  q('[data-type]').onchange=()=>{const type=q('[data-type]').value;verified=false;q('[data-trial]').disabled=true;q('[data-preview]').hidden=true;
    q('[data-uri-field]').hidden=['file','sim','onvif','gb28181'].includes(type);q('[data-file-field]').hidden=type!=='file';q('[data-onvif]').hidden=type!=='onvif';q('[data-gb]').hidden=type!=='gb28181';
    q('[data-uri]').value=type==='camera'?'0':'';
    q('[data-uri-label]').textContent=type==='camera'?'USB 相机编号（通常为 0）':'相机 RTSP 地址';};
  q('[data-video]').onchange=()=>{verified=false;q('[data-trial]').disabled=true;};
  q('[data-uri]').oninput=()=>{verified=false;q('[data-trial]').disabled=true;};
  d.body.querySelectorAll('[data-onvif] input, [data-gb] input').forEach(input=>input.oninput=()=>{verified=false;q('[data-trial]').disabled=true;});
  q('[data-discover]').onclick=async()=>{q('[data-discover]').disabled=true;q('[data-status]').textContent='正在搜索局域网相机…';try{const result=await post('/api/cameras/onvif/discover',{timeout_s:3});const urls=(result.devices||[]).map(x=>x.xaddr).filter(Boolean);q('[data-onvif-devices]').innerHTML='<option value="">手动填写地址</option>'+urls.map(url=>`<option value="${esc(url)}">${esc(url)}</option>`).join('');q('[data-status]').textContent=urls.length?`发现 ${urls.length} 台设备，请选择后填写账号。`:'没有发现设备，可手动填写地址。';}catch(e){q('[data-status]').textContent=e.message;}finally{q('[data-discover]').disabled=false;}};
  q('[data-onvif-devices]').onchange=()=>{try{const value=q('[data-onvif-devices]').value;if(!value)return;const url=new URL(value);if(!['http:','https:'].includes(url.protocol))throw new Error();q('[data-onvif-host]').value=url.hostname.replace(/^\[|\]$/g,'');q('[data-onvif-port]').value=url.port||(url.protocol==='https:'?443:80);verified=false;q('[data-trial]').disabled=true;}catch{q('[data-status]').textContent='设备地址无效，请手动填写。';}};
  q('[data-test]').onclick=async()=>{
    q('[data-test]').disabled=true;q('[data-status]').textContent='正在读取画面…';
    try {
      const type=q('[data-type]').value;
      let result;
      if(type==='file'){
        const file=q('[data-video]').files[0];if(!file)throw new Error('请选择视频');const form=new FormData();form.append('file',file);
        result=await request(`/api/applications/${row.id}/video`,{method:'POST',body:form});
      }else if(type==='onvif'){
        const user=q('[data-onvif-user]').value,password=q('[data-onvif-password]').value;
        const camera=await post('/api/cameras/onvif/adopt',{host:q('[data-onvif-host]').value.trim(),port:Number(q('[data-onvif-port]').value),user,password});
        if(!camera.rtsp)throw new Error('相机没有返回 RTSP 地址');const uri=new URL(camera.rtsp);if(!['rtsp:','rtsps:'].includes(uri.protocol))throw new Error('相机返回的画面地址无效');
        if(!uri.username&&user){uri.username=user;uri.password=password;}result=await post(`/api/applications/${row.id}/input`,{type:'rtsp',uri:uri.toString()});q('[data-onvif-password]').value='';
      }else if(type==='gb28181'){
        result=await post(`/api/applications/${row.id}/gb28181`,{device_id:q('[data-gb-device]').value.trim(),channel_id:q('[data-gb-channel]').value.trim()});
      }else result=await post(`/api/applications/${row.id}/input`,{type,uri:q('[data-uri]').value});
      verified=true;q('[data-preview]').src='data:image/jpeg;base64,'+result.image;q('[data-preview]').hidden=false;
      q('[data-status]').textContent=`画面正常 · ${result.width} × ${result.height}`;q('[data-trial]').disabled=false;
    }catch(e){verified=false;q('[data-trial]').disabled=true;q('[data-status]').textContent=e.message;}
    finally{q('[data-test]').disabled=false;}
  };
  q('[data-trial]').onclick=async()=>{
    q('[data-trial]').disabled=true;
    try{await post(`/api/applications/${row.id}/run`);d.close();onSaved();}
    catch(e){q('[data-status]').textContent=e.message;q('[data-trial]').disabled=!verified;}
  };
}

/** WHEP 非 trickle ICE：超时回退分析画面，切页时释放连接。 */
export function mountPreview(root, id, config) {
  const img = root.querySelector("[data-analysis]"), video = root.querySelector("[data-webrtc]");
  const note = root.querySelector("[data-preview-note]"), retry = root.querySelector("[data-preview-retry]");
  const analysis = root.querySelector("[data-preview-analysis]");
  let pc = null, controller = null, session = null, timer = null, leaseTimer = null, generation = 0, dead = false;
  const setNote = (text) => { if (!dead) note.textContent = text; };
  const release = (url) => { if (url) fetch(url, { method: "DELETE", keepalive: true }).catch(() => {}); };
  const stop = () => {
    generation++;
    clearTimeout(timer);
    clearInterval(leaseTimer); leaseTimer = null;
    controller?.abort(); controller = null;
    if (pc) { pc.onconnectionstatechange = null; pc.ontrack = null; pc.close(); pc = null; }
    video.srcObject?.getTracks().forEach((t) => t.stop());
    video.srcObject = null;
    release(session); session = null;
    video.hidden = true; img.hidden = false;
  };
  const fallback = (message) => { stop(); setNote(`${message} · 已切换分析画面`); };
  const start = async () => {
    stop();
    const turn = generation;
    if (!config.available) { setNote(config.reason); return; }
    if (!window.RTCPeerConnection) { fallback("浏览器不支持 WebRTC"); return; }
    setNote("正在连接 WebRTC…");
    controller = new AbortController();
    const signal = controller.signal;
    const peer = pc = new RTCPeerConnection({ iceServers: [] }); // 局域网不依赖公网 STUN
    peer.addTransceiver("video", { direction: "recvonly" });
    peer.ontrack = (event) => {
      if (turn !== generation || dead) return;
      video.srcObject = new MediaStream([event.track]);
      video.play().catch(() => fallback("浏览器未允许播放，请重试"));
    };
    video.onplaying = () => {
      if (turn !== generation || dead) return;
      clearTimeout(timer); img.hidden = true; video.hidden = false;
      setNote("WebRTC 原始画面 · 检测叠加请切换分析画面");
    };
    peer.onconnectionstatechange = () => {
      if (["failed", "disconnected"].includes(peer.connectionState) && turn === generation) fallback("WebRTC 连接中断");
    };
    timer = setTimeout(() => { if (turn === generation) fallback("WebRTC 连接超时"); }, 15000);
    try {
      await peer.setLocalDescription(await peer.createOffer());
      if (peer.iceGatheringState !== "complete") await new Promise((resolve, reject) => {
        const done = () => { peer.removeEventListener("icegatheringstatechange", change); signal.removeEventListener("abort", abort); };
        const change = () => { if (peer.iceGatheringState === "complete") { done(); resolve(); } };
        const abort = () => { done(); reject(new DOMException("Aborted", "AbortError")); };
        peer.addEventListener("icegatheringstatechange", change);
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) abort();
      });
      if (turn !== generation || dead) return;
      const res = await fetch(`/api/stations/${encodeURIComponent(id)}/preview`, {
        method: "POST", headers: { "Content-Type": "application/sdp" }, body: peer.localDescription.sdp, signal,
      });
      const location = res.headers.get("Location");
      if (turn !== generation || dead) { release(location); return; }
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `WebRTC 请求失败（HTTP ${res.status}）`);
      }
      session = location;
      if (session) leaseTimer = setInterval(async () => {
        try {
          const lease = await fetch(session + "/lease", { method: "POST" });
          if (!lease.ok && turn === generation) fallback("预览会话已过期，请重试");
        } catch { if (turn === generation) fallback("预览信令连接中断"); }
      }, 30000);
      await peer.setRemoteDescription({ type: "answer", sdp: await res.text() });
    } catch (e) {
      if (turn === generation && !dead && e.name !== "AbortError") fallback(e.message);
    }
  };
  retry.hidden = !config.available;
  retry.onclick = start;
  analysis.onclick = () => { stop(); setNote("分析画面 · 含检测区域与结果叠加"); };
  const onHide = () => stop();
  window.addEventListener("pagehide", onHide);
  start();
  return () => { dead = true; stop(); video.onplaying = null; window.removeEventListener("pagehide", onHide); };
}

const $ = id => document.getElementById(id);
let current = null, busy = false;
const players = {}, retryAt = {};
function notice(message) { $('notice').textContent = message; }
async function api(path, data) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-Relay-Request':'1'}, body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function stopPreview(source) {
  if (players[source]) {players[source].destroy(); delete players[source];}
  const video = $('video-' + source);
  video.removeAttribute('src'); video.load();
  $('empty-' + source).hidden = false;
}
function preview(source, available) {
  const video = $('video-' + source);
  if (!available) {if (players[source] || video.hasAttribute('src')) stopPreview(source); return;}
  if (players[source] || video.hasAttribute('src') || Date.now() < (retryAt[source] || 0)) return;
  video.onplaying = () => {$('empty-' + source).hidden = true;};
  video.onwaiting = () => {$('empty-' + source).hidden = false;};
  if (window.Hls && Hls.isSupported()) {
    const hls = new Hls({liveSyncDurationCount:2, maxBufferLength:8, enableWorker:true});
    players[source] = hls;
    hls.loadSource(`/preview/${source}/index.m3u8`); hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
    hls.on(Hls.Events.ERROR, (_, data) => {
      if (data.fatal) {retryAt[source] = Date.now() + 4000; stopPreview(source);}
    });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = `/preview/${source}/index.m3u8`; video.play().catch(() => {});
    video.onerror = () => {retryAt[source] = Date.now() + 4000; stopPreview(source);};
  } else notice('This browser cannot play HLS previews. Use a current Chrome, Firefox, Edge or Safari.');
}
async function refresh() {
  try {
    const response = await fetch('/api/status');
    if (!response.ok) throw new Error('Dashboard connection failed');
    const state = await response.json(); current = state;
    $('connection').textContent = state.connected ? '● Encoder connected' : '○ Encoder reconnecting';
    $('output-label').textContent = state.reconnecting ? 'Reconnecting output' : state.streaming ? 'Broadcast live' : state.want_live ? 'Retrying output' : 'Output offline';
    $('output-detail').textContent = state.streaming ? 'Persistent RTMP output' : state.want_live ? 'Checking destination' : 'Ready when you are';
    $('live-dot').classList.toggle('live', state.streaming && !state.reconnecting);
    $('active').textContent = state.connected ? ({desk:'Desk stream',irl:'IRL stream',fallback:'Fallback graphic'}[state.active]) : 'Unknown';
    $('preferred').textContent = {desk:'Desk',irl:'IRL',fallback:'Fallback'}[state.preferred];
    $('dropped').textContent = state.dropped.toLocaleString();
    if (!busy) $('automatic').checked = state.automatic;
    $('start').disabled = busy || !state.connected || state.streaming || state.want_live;
    $('stop').disabled = busy || !state.connected || (!state.streaming && !state.want_live);
    $('output-server').disabled = state.streaming || state.want_live;
    $('output-key').disabled = state.streaming || state.want_live;
    if (!$('output-server').value && state.destination) $('output-server').value = state.destination;
    for (const source of ['desk','irl']) {
      const input = state.inputs[source];
      $('badge-' + source).textContent = input.healthy ? '● Healthy' : input.ready ? 'Warming up' : 'Offline';
      $('badge-' + source).classList.toggle('healthy',input.healthy);
      $('card-' + source).classList.toggle('active',state.active === source && state.connected);
      $('program-' + source).hidden = state.active !== source || !state.streaming;
      $('server-' + source).value = state.ingest[source].server;
      $('key-' + source).value = state.ingest[source].key;
      preview(source,input.ready);
    }
    if (state.error) notice(state.error);
  } catch (error) {
    $('connection').textContent = '○ Dashboard disconnected';
    $('start').disabled = $('stop').disabled = true;
    notice('Cannot reach the server. Encoder status is unknown; closing this tab does not stop it.');
  }
}
async function action(fn) {
  busy = true;
  try {await fn(); notice('');} catch(error) {notice(error.message);}
  finally {busy = false; await refresh();}
}
document.querySelectorAll('[data-source]').forEach(button => button.onclick = () => action(() => api('/api/route',{preferred:button.dataset.source,automatic:$('automatic').checked})));
$('automatic').onchange = () => action(() => api('/api/route',{preferred:current?.preferred || 'desk',automatic:$('automatic').checked}));
$('destination').onsubmit = event => {event.preventDefault(); action(async () => {await api('/api/output/start',{server:$('output-server').value,key:$('output-key').value}); $('output-key').value = '';});};
$('stop').onclick = () => {if (window.confirm('Stop the outgoing broadcast? This may end your Twitch stream.')) action(() => api('/api/output/stop',{}));};
$('reveal').onclick = () => {const show = $('output-key').type === 'password'; $('output-key').type = show ? 'text':'password'; $('reveal').textContent = show ? 'Hide':'Show';};
document.querySelectorAll('[data-copy]').forEach(button => button.onclick = async () => {
  const input = $(button.dataset.copy);
  try {await navigator.clipboard.writeText(input.value); notice('Copied.');}
  catch (_) {input.type = 'text'; input.select(); notice('Select and copy the value. Remote clipboard access requires HTTPS.');}
});
$('upload').onchange = () => action(async () => {
  const file = $('upload').files[0]; if (!file) return;
  const data = new FormData(); data.append('image',file);
  const response = await fetch('/api/fallback',{method:'POST',headers:{'X-Relay-Request':'1'},body:data});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Upload failed');
  $('fallback-image').src = '/api/fallback?t=' + Date.now(); $('upload').value = '';
});
(async function poll(){await refresh(); setTimeout(poll,2000);})();

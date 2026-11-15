import"./modulepreload-polyfill-B5Qt9EMX.js";import{l as r,s as o}from"./messaging-Ddi-Z1Vx.js";try{}catch(n){console.error("[wxt] Failed to initialize plugins",n)}async function a(n){const s=new AbortController,i=setTimeout(()=>s.abort(),8e3);try{const t=await fetch(`${n.replace(/\/$/,"")}/health`,{signal:s.signal});if(!t.ok)return`Server answered HTTP ${t.status} \u2014 is this the orchestrator?`;const e=await t.json();return`OK (${e.status??"unknown"}, engines: ${JSON.stringify(e.engines??{})})`}catch(t){const e=t instanceof Error?t.message:String(t);return e.includes("abort")?"Timed out \u2014 host unreachable or wrong port.":`Cannot reach it (${e}). Check the IP, port, and firewall.`}finally{clearTimeout(i)}}async function c(){const n=document.getElementById("app"),s=await r();n.innerHTML=`
    <h3>InkCast server</h3>
    <input id="url" style="width:100%" placeholder="http://192.168.x.x:8095" value="${s.serverUrl}" />
    <button id="test">Test connection</button>
    <div id="status"></div>
    <div style="margin-top:12px">
      <button id="play">Play</button>
      <button id="pause">Pause</button>
    </div>
    <p style="font-size:12px">Find the URL in the terminal where you ran update.sh (it prints the LAN URL).</p>`;const i=document.getElementById("url"),t=document.getElementById("status");document.getElementById("test").onclick=async()=>{const e=i.value.trim();t.textContent="Checking\u2026",t.textContent=await a(e),t.textContent.startsWith("OK")&&await o({...s,serverUrl:e})},document.getElementById("play").onclick=()=>chrome.runtime.sendMessage({type:"PLAY"}),document.getElementById("pause").onclick=()=>chrome.runtime.sendMessage({type:"PAUSE"})}c();

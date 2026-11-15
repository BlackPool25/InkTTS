import"./modulepreload-polyfill-B5Qt9EMX.js";import{l,D as n,s as i}from"./messaging-Ddi-Z1Vx.js";try{}catch(t){console.error("[wxt] Failed to initialize plugins",t)}async function r(){const t=document.getElementById("app"),e=await l(),a=JSON.stringify(e.perSite,null,2);t.innerHTML=`
    <h3>Voice & model</h3>
    <label>Voice <input id="voice" value="${e.voice}" /></label>
    <label>Speed <input id="speed" type="number" step="0.1" min="0.5" max="2" value="${e.speed}" /></label>
    <label>Model <select id="model">
      <option value="kokoro"${e.model==="kokoro"?" selected":""}>Kokoro (fast, CPU)</option>
      <option value="chatterbox"${e.model==="chatterbox"?" selected":""}>Chatterbox (expressive, GPU)</option>
    </select></label>
    <h3>Per-site overrides (domain -&gt; JSON)</h3>
    <textarea id="persite" rows="6" style="width:100%">${a.replace(/</g,"&lt;")}</textarea>
    <h3>Pronunciation rules (one per line: pattern =&gt; say-as)</h3>
    <textarea id="pron" rows="6" style="width:100%" placeholder="FBI => F B I"></textarea>
    <h3>Site extraction rules (JSON, optional)</h3>
    <textarea id="rules" rows="6" style="width:100%" placeholder='[{"match":"example.com","content":["#text"]}]'></textarea>
    <br/><button id="save">Save</button> <span id="ok"></span>`,document.getElementById("save").onclick=async()=>{const o={...e,voice:document.getElementById("voice").value.trim()||n.voice,speed:parseFloat(document.getElementById("speed").value)||1,model:document.getElementById("model").value,perSite:JSON.parse(document.getElementById("persite").value||"{}")};o.siteRules=document.getElementById("rules").value,o.pron=document.getElementById("pron").value,await i(o),document.getElementById("ok").textContent="Saved."}}r();

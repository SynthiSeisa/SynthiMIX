"""Fernbedienung und Musikwuensche (eigener Server auf Port 8080)."""
import asyncio
import base64
import json
import os
import re
import socket as _socket
import time
import uvicorn
from difflib import SequenceMatcher
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pathlib import Path
from typing import Any
from .core import _state
from . import core, download, keys, library, media, search, store

# Nachrichten der Fernbedienung laufen durch denselben Verteiler wie die
# der App; main.py traegt ihn beim Import ein.
handle_message = None

_remote_clients: set[WebSocket] = set()
_wish_clients: set[WebSocket] = set()      # offene Musikwunsch-Seiten
_remote_server: Any = None
# Fernbedienung und Musikwunsch laufen ueber denselben Server, lassen sich aber
# einzeln ein- und ausschalten. Der Server laeuft, solange einer an ist.
_remote_services: dict = {"remote": False, "wishes": False}

def _remote_status_msg(**extra) -> dict:
    ip = _get_local_ip()
    running = _remote_server is not None
    return {"type": "remote_status", "running": running, "services": dict(_remote_services),
            **({"ip": ip, "port": _remote_port, **_remote_urls(ip)} if running else {}), **extra}
_remote_port = 8080

def _get_local_ip() -> str:
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

# ── Geheimer Link fuer die Fernbedienung ─────────────────────────────────────
# Fernbedienung und Wunschseite teilen sich Port 8080. Ohne Schutz kam ein Gast,
# der im Wunsch-Link "/wunsch" wegloeschte, auf die Fernbedienung und konnte
# pausieren oder die Warteschlange leeren. Einen PIN wollte der Nutzer nicht —
# stattdessen steckt ein zufaelliger Schluessel in der Adresse (und im QR-Code).
def _remote_key() -> str:
    import secrets
    if not _state.get("remote_key"):
        _state["remote_key"] = secrets.token_urlsafe(9)
        store.save_settings()
    return _state["remote_key"]

def _remote_key_ok(k: str | None) -> bool:
    import secrets
    return bool(k) and secrets.compare_digest(str(k), _remote_key())

def _remote_urls(ip: str) -> dict:
    base = f"http://{ip}:{_remote_port}"
    return {"url": f"{base}/?k={_remote_key()}", "wish_url": f"{base}/wunsch"}

_REMOTE_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>SynthiMIX Remote</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg viewBox='0 0 40 40' xmlns='http://www.w3.org/2000/svg'%3E%3Ccircle cx='20' cy='20' r='19' fill='%230d1a2e'/%3E%3Crect x='5' y='16' width='4' height='9' rx='1.5' fill='%23e07800'/%3E%3Crect x='11' y='10' width='4' height='15' rx='1.5' fill='%23e07800'/%3E%3Crect x='17' y='13' width='4' height='12' rx='1.5' fill='%23f59332'/%3E%3Crect x='23' y='7' width='4' height='18' rx='1.5' fill='%23e07800'/%3E%3Crect x='29' y='11' width='4' height='14' rx='1.5' fill='%23f59332'/%3E%3Cline x1='20' y1='29' x2='20' y2='35' stroke='%233b82f6' stroke-width='2' stroke-linecap='round'/%3E%3Cpolyline points='16,32 20,36 24,32' fill='none' stroke='%233b82f6' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<link rel="manifest" href="/manifest.json?k=__KEY__">
<meta name="theme-color" content="#0d1625">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="SynthiMIX">
<style>
/* Grundsystem von SynthiMIX (App.svelte / lib/ui.css) als eigene Variablen —
   die Seite laeuft ohne die App. Werte hier mitziehen, wenn sich die App aendert.
   Handy: Schrift mindestens 13px, Klickziele mindestens 44px. */
:root{
  --bg:#0a0e18;--bg2:#080c16;--surf:#0e1624;--hover:#101828;--sel:#0e1c38;
  --br1:#121e30;--br2:#1e2e44;--br3:#2a3e5c;
  --tx1:#ecf2ff;--tx2:#d4e0f2;--tx3:#b0c6dc;--tx4:#9ab2cc;--tx5:#8ba6c4;
  --accent:#e07800;--accent2:#ff9020;--accent-tx:#ff9a33;--on-accent:#0a0e18;--act-bg:#1a1206;
  --green-tx:#6fcf7c;--green-bg:#0e1a10;--green-br:#2a6a30;
  --red-tx:#ff7b7b;--red-bg:#1a0808;--red-br:#8a3030;
  --blue-tx:#7fb0ec;--blue-bg:#0e1a2c;--blue-br:#2a5888;
  --r-s:4px;--r-m:8px;--r-l:12px;
  --fs-sm:13px;--fs-body:15px;--fs-lg:17px;--fs-h:20px;
  --tap:44px;
  color-scheme:dark;
}
/* Handy auf hell gestellt: helles Theme der App — draussen in der Sonne lesbarer */
@media (prefers-color-scheme: light){
  :root{
    --bg:#f4f0eb;--bg2:#ebe7e1;--surf:#ffffff;--hover:#e0dcd6;--sel:#ddeeff;
    --br1:#d6d0c8;--br2:#bdb6ad;--br3:#a39b91;
    --tx1:#0a0806;--tx2:#1e1a14;--tx3:#3a342a;--tx4:#4a443c;--tx5:#554e47;
    --accent:#b35400;--accent2:#c86000;--accent-tx:#9a4700;--on-accent:#ffffff;--act-bg:#fff1dc;
    --green-tx:#1a7030;--green-bg:#eaf6ec;--green-br:#8ac098;
    --red-tx:#b82020;--red-bg:#fff0f0;--red-br:#d89a9a;
    --blue-tx:#1a5cb0;--blue-bg:#e8f0fb;--blue-br:#93b6e0;
    color-scheme:light;
  }
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--tx2);font:var(--fs-body)/1.4 'Segoe UI',system-ui,-apple-system,sans-serif;
  padding-bottom:calc(172px + env(safe-area-inset-bottom,0px))}
button{font:inherit;color:inherit}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent-tx);outline-offset:2px}
.ico{width:22px;height:22px;flex-shrink:0}

/* Kopf mit Verbindungsstatus als Text */
.hdr{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:10px;
  padding:calc(10px + env(safe-area-inset-top,0px)) 16px 10px;background:var(--bg2);border-bottom:1px solid var(--br1)}
.logo{font-weight:700;font-size:var(--fs-lg);color:var(--accent-tx)}.logo span{color:var(--blue-tx)}
.sub{font-size:var(--fs-sm);color:var(--tx3)}
.conn{margin-left:auto;display:inline-flex;align-items:center;gap:6px;font-size:var(--fs-sm);font-weight:700;
  padding:4px 10px;border-radius:999px;color:var(--red-tx);background:var(--red-bg);border:1px solid var(--red-br)}
.conn::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.conn.on{color:var(--green-tx);background:var(--green-bg);border-color:var(--green-br)}

/* Laufender Titel */
.np{padding:16px 16px 12px;text-align:center;border-bottom:1px solid var(--br1)}
.cov{display:none;width:148px;height:148px;object-fit:cover;border-radius:var(--r-l);margin:0 auto 12px;box-shadow:0 8px 24px rgba(0,0,0,.35)}
.cov.on{display:block}
.np-t{font-size:var(--fs-h);font-weight:700;color:var(--tx1);line-height:1.25;word-break:break-word}
.np-a{font-size:var(--fs-body);color:var(--tx3);min-height:20px;margin-top:4px}
.pb-hit{padding:14px 0;margin:6px 0 -6px;cursor:pointer}
.pb-wrap{background:var(--br2);border-radius:3px;height:6px;overflow:hidden}
.pb-fill{background:var(--accent);height:100%;width:0%;transition:width .9s linear}
.pb-row{display:flex;justify-content:space-between;font-size:var(--fs-sm);color:var(--tx3);font-variant-numeric:tabular-nums}
.np-nx{font-size:var(--fs-sm);color:var(--tx3);margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* Feste Bedienleiste unten — im Daumenbereich */
.dock{position:fixed;left:0;right:0;bottom:0;z-index:60;background:var(--bg2);border-top:1px solid var(--br2);
  padding:10px 16px calc(12px + env(safe-area-inset-bottom,0px));box-shadow:0 -8px 24px rgba(0,0,0,.25)}
.vol-row{display:flex;align-items:center;gap:12px}
.vol-row label{font-size:var(--fs-sm);font-weight:700;color:var(--tx3);min-width:32px}
.vol-row input{flex:1}
.vol-val{font-size:var(--fs-body);font-weight:700;color:var(--tx1);min-width:44px;text-align:right;font-variant-numeric:tabular-nums}
.ctrls{display:flex;justify-content:center;align-items:center;gap:28px;margin-top:8px}
.tbtn{width:56px;height:56px;border-radius:50%;border:none;background:var(--surf);color:var(--tx1);
  display:flex;align-items:center;justify-content:center;cursor:pointer}
.tbtn .ico{width:26px;height:26px}
.tbtn.big{width:68px;height:68px;background:var(--accent);color:var(--on-accent)}
.tbtn.big .ico{width:32px;height:32px}
.tbtn:active{transform:scale(.95)}
input[type=range]{height:var(--tap);accent-color:var(--accent);width:100%}

/* Abschnitte */
.sec{padding:16px 16px 8px;border-top:1px solid var(--br1)}
.sec-h{display:flex;align-items:center;gap:8px;font-size:var(--fs-sm);font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--tx3);margin-bottom:10px}
.sec-h .cnt,.wc{font-size:var(--fs-sm);letter-spacing:0;font-weight:700;padding:1px 8px;border-radius:999px;
  background:var(--surf);border:1px solid var(--br2);color:var(--tx2);font-variant-numeric:tabular-nums}
.wc{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.wc:empty{display:none}

/* Schalter-Knoepfe (Normalisierung, Auto-Mix, Radio) */
.norm-wrap{display:grid;gap:10px}
.norm-head{display:flex;align-items:center;gap:10px}
.norm-btn{flex:1;min-height:var(--tap);border-radius:var(--r-m);border:1px solid var(--br3);background:var(--surf);
  color:var(--tx2);font-size:var(--fs-body);font-weight:600;cursor:pointer;padding:0 12px}
.norm-btn.on{background:var(--act-bg);border-color:var(--accent);color:var(--accent-tx)}
.norm-val{font-size:var(--fs-body);font-weight:700;color:var(--tx1);min-width:76px;text-align:right;font-variant-numeric:tabular-nums}
.tg-row{display:flex;gap:10px}

/* Warteschlange */
.q-wrap{max-height:52vh;overflow-y:auto;-webkit-overflow-scrolling:touch;border-radius:var(--r-m);border:1px solid var(--br1)}
.qi{display:flex;align-items:center;gap:6px;min-height:56px;padding:6px 8px 6px 2px;border-bottom:1px solid var(--br1);user-select:none;background:var(--bg)}
.qi.cur{background:var(--act-bg);box-shadow:inset 4px 0 0 var(--accent)}
.ghost{background:var(--surf);border:1px solid var(--accent);border-radius:var(--r-m);box-shadow:0 8px 24px rgba(0,0,0,.4);color:var(--tx1);opacity:.96}
.dh{color:var(--tx4);font-size:22px;width:44px;height:var(--tap);user-select:none;-webkit-user-select:none;display:flex;align-items:center;justify-content:center;flex-shrink:0;touch-action:none;cursor:grab}
.qi-info{flex:1;min-width:0;cursor:pointer}
.qi-t{font-size:var(--fs-body);color:var(--tx1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.qi-t.a{color:var(--accent-tx);font-weight:700}
.qi-t.p{color:var(--tx4)}
.qi-d{font-size:var(--fs-sm);color:var(--tx3);margin-top:2px;display:flex;align-items:center;flex-wrap:wrap;gap:4px;font-variant-numeric:tabular-nums}
.qi-eta{color:var(--tx4)}
.qa{display:flex;gap:8px;padding:8px 8px 12px 42px;border-bottom:1px solid var(--br1);background:var(--surf)}
.qa button{flex:1;min-height:var(--tap);border-radius:var(--r-m);border:1px solid var(--br3);background:var(--bg);
  color:var(--tx1);font-size:var(--fs-sm);font-weight:600;cursor:pointer;padding:0 6px}
.qa button.rm{color:var(--red-tx);border-color:var(--red-br);background:var(--red-bg)}

/* Tonart-Chips wie in der App: Zeichen + Farbe */
.kc{display:inline-block;padding:1px 7px;border-radius:var(--r-s);background:var(--surf);border:1px solid var(--br2);
  color:var(--tx2);font-size:var(--fs-sm);font-weight:600}
.kc.ok{color:var(--green-tx);border-color:var(--green-br);background:var(--green-bg)}
.kc.no{color:var(--red-tx);border-color:var(--red-br);background:var(--red-bg)}
.kc.est{font-style:italic}

/* Wuensche */
.wi{display:flex;align-items:center;gap:8px;min-height:60px;padding:8px 0;border-bottom:1px solid var(--br1)}
.wi-info{flex:1;min-width:0}
.wi-t{font-size:var(--fs-body);font-weight:600;color:var(--tx1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wi-s{font-size:var(--fs-sm);color:var(--tx3);margin-top:2px}
.wi-s.err{color:var(--red-tx)}

/* Aktionsknoepfe in Listen */
.nxt-b,.add-b,.dl-b,.wno{width:var(--tap);height:var(--tap);flex-shrink:0;border-radius:var(--r-m);cursor:pointer;
  display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;padding:0;
  border:1px solid var(--br3);background:var(--surf);color:var(--tx1)}
.add-b,.dl-b{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.wno{color:var(--red-tx);border-color:var(--red-br);background:var(--red-bg)}
.nxt-b:disabled,.add-b:disabled,.dl-b:disabled{opacity:.5}
.nxt-b:active,.add-b:active,.dl-b:active,.wno:active{transform:scale(.95)}

/* Playlisten */
.pl-list{display:flex;flex-wrap:wrap;gap:8px}
.pl-b{min-height:var(--tap);border-radius:var(--r-m);border:1px solid var(--br3);background:var(--surf);
  color:var(--tx1);font-size:var(--fs-sm);font-weight:600;padding:0 14px;cursor:pointer}
.pl-b:active{border-color:var(--accent);background:var(--act-bg)}

/* Suche */
.s-tabs{display:flex;gap:8px;margin-bottom:10px}
.s-tab{flex:1;min-height:var(--tap);border-radius:var(--r-m);border:1px solid var(--br3);background:var(--surf);
  color:var(--tx2);font-size:var(--fs-body);font-weight:600;cursor:pointer}
.s-tab.active{border-color:var(--accent);background:var(--act-bg);color:var(--accent-tx)}
.s-row{display:flex;gap:8px;margin-bottom:8px}
.inp{flex:1;min-height:48px;border-radius:var(--r-m);border:1px solid var(--br3);background:var(--surf);
  color:var(--tx1);font-size:16px;padding:0 14px}
.inp::placeholder{color:var(--tx4)}
.inp:focus{border-color:var(--accent);outline:none}
.ri{display:flex;align-items:center;gap:8px;min-height:60px;padding:8px 0;border-bottom:1px solid var(--br1)}
.ri-info{flex:1;min-width:0}
.ri-t{font-size:var(--fs-body);color:var(--tx1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ri-a,.ri-sub{font-size:var(--fs-sm);color:var(--tx3);margin-top:2px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.empty{padding:18px 4px;color:var(--tx3);font-size:var(--fs-sm);text-align:center}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <symbol id="i-prev" viewBox="0 0 24 24"><path fill="currentColor" d="M6 5h2v14H6zM20 5.5v13a1 1 0 0 1-1.5.87L9 13.1v-2.2l9.5-6.27A1 1 0 0 1 20 5.5z"/></symbol>
  <symbol id="i-next" viewBox="0 0 24 24"><path fill="currentColor" d="M16 5h2v14h-2zM4 5.5v13a1 1 0 0 0 1.5.87L15 13.1v-2.2L5.5 4.63A1 1 0 0 0 4 5.5z"/></symbol>
  <symbol id="i-play" viewBox="0 0 24 24"><path fill="currentColor" d="M7 4.8v14.4a1 1 0 0 0 1.53.85l11.2-7.2a1 1 0 0 0 0-1.7L8.53 3.95A1 1 0 0 0 7 4.8z"/></symbol>
  <symbol id="i-pause" viewBox="0 0 24 24"><rect x="6" y="4" width="4.5" height="16" rx="1" fill="currentColor"/><rect x="13.5" y="4" width="4.5" height="16" rx="1" fill="currentColor"/></symbol>
</svg>
<header class="hdr">
  <span class="logo">Synthi<span>MIX</span></span>
  <span class="sub">Fernbedienung</span>
  <span id="dot" class="conn" role="status">getrennt</span>
</header>
<section class="np">
  <img id="cov" class="cov" alt="">
  <div id="npT" class="np-t">&#8211;</div>
  <div id="npA" class="np-a"></div>
  <div class="pb-hit" onclick="seekAt(event)" title="Zum Spulen antippen">
    <div class="pb-wrap"><div id="pbf" class="pb-fill"></div></div>
  </div>
  <div class="pb-row"><span id="pbt">0:00</span><span id="pbr">&#8211;</span></div>
  <div id="npNx" class="np-nx"></div>
</section>
<section class="sec" id="wsec" style="display:none">
  <div class="sec-h">W&#252;nsche <span id="wcn" class="wc"></span></div>
  <div id="wl"></div>
</section>
<section class="sec">
  <div class="sec-h">Warteschlange <span id="qc" class="cnt">0</span></div>
  <div id="qw" class="q-wrap"><div id="ql"></div></div>
</section>
<section class="sec">
  <div class="sec-h">Mix</div>
  <div class="norm-wrap">
    <div class="norm-head">
      <button id="normb" class="norm-btn on" onclick="toggleNorm()">Normalisierung an</button>
      <span id="normv" class="norm-val">-10 LUFS</span>
    </div>
    <input type="range" id="normr" min="-23" max="-5" step="1" value="-10" oninput="onNorm(this.value)" onchange="flushNorm()" aria-label="Ziel-Lautst&#228;rke">
    <div class="tg-row">
      <button id="amb" class="norm-btn" onclick="toggleAM()">Auto-Mix</button>
      <button id="rdb" class="norm-btn" onclick="toggleRadio()">Radio</button>
    </div>
  </div>
</section>
<section class="sec">
  <div class="sec-h">Playlisten</div>
  <div id="pll" class="pl-list"><div class="empty">&#8230;</div></div>
</section>
<section class="sec" style="padding-bottom:24px">
  <div class="sec-h">Suche</div>
  <div class="s-tabs">
    <button id="tb-lib" class="s-tab active" onclick="setMode('lib')">Bibliothek</button>
    <button id="tb-yt" class="s-tab" onclick="setMode('yt')">YouTube</button>
  </div>
  <div class="s-row"><input class="inp" id="si" placeholder="Titel oder K&#252;nstler&#8230;" type="search" oninput="onS(this.value)" aria-label="Suchen"></div>
  <div id="sr"></div>
</section>
<nav class="dock" aria-label="Wiedergabe">
  <div class="vol-row">
    <label for="vr">Vol</label>
    <input type="range" id="vr" min="0" max="100" value="80" oninput="onVol(this.value)" onchange="flushVol()">
    <span id="vv" class="vol-val">80%</span>
  </div>
  <div class="ctrls">
    <button class="tbtn" onclick="send({type:'play_prev'})" aria-label="Zur&#252;ck"><svg class="ico"><use href="#i-prev"/></svg></button>
    <button id="pb" class="tbtn big" onclick="toggle()" aria-label="Abspielen"><svg class="ico"><use href="#i-play"/></svg></button>
    <button class="tbtn" onclick="send({type:'play_next'})" aria-label="Weiter"><svg class="ico"><use href="#i-next"/></svg></button>
  </div>
</nav>
<script>
var KEY='__KEY__'
var _lastCi=null,_openPath=null
var st={playing:false,current_idx:-1,volume:80,normalize_volume:true,target_lufs:-10,queue:[]},ws,_vt,_res=[],_ytRes=[],_srMode='lib'
var _rt=null,_wl=null
function conn(){
  if(ws&&(ws.readyState===0||ws.readyState===1))return
  clearTimeout(_rt);_rt=null
  ws=new WebSocket('ws://'+location.host+'/ws?k='+encodeURIComponent(KEY))
  ws.onopen=function(){dot(true);send({type:'remote_playlists'})}
  ws.onclose=function(){dot(false);clearTimeout(_rt);_rt=setTimeout(conn,2000)}
  ws.onerror=function(){ws.close()}
  ws.onmessage=function(e){
    var m=JSON.parse(e.data)
    if(m.type==='state'){st=m;if(dg.on)dg.pend=true;else render();updPos(m)}
    else if(m.type==='pos'){updPos(m)}
    else if(m.type==='search_results'){showRes(m.results||[])}
    else if(m.type==='yt_results'){showYtRes(m.results||[])}
    else if(m.type==='playlists'){showPls(m.items||[])}
    else if(m.type==='yt_dl_status'){updDl(m)}
  }
}
function setMode(m){
  _srMode=m
  document.getElementById('tb-lib').className='s-tab'+(m==='lib'?' active':'')
  document.getElementById('tb-yt').className='s-tab'+(m==='yt'?' active':'')
  document.getElementById('si').placeholder=m==='lib'?'Titel oder Künstler…':'YouTube suchen…'
  document.getElementById('sr').innerHTML=''
  document.getElementById('si').value=''
}
function dot(on){var d=document.getElementById('dot');d.className='conn'+(on?' on':'');d.textContent=on?'verbunden':'getrennt'}
function send(o){if(ws&&ws.readyState===1)ws.send(JSON.stringify(o))}
function toggle(){send({type:st.playing?'pause':'resume'})}
var _pv=null
function onVol(v){document.getElementById('vv').textContent=v+'%';_pv=+v;clearTimeout(_vt);_vt=setTimeout(flushVol,120)}
function flushVol(){if(_pv!=null){send({type:'set_volume',value:_pv});_pv=null}}
function updateNormUI(){var nb=document.getElementById('normb');var nr=document.getElementById('normr');if(nb){nb.textContent=st.normalize_volume?'Normalisierung an':'Normalisierung aus';nb.className='norm-btn'+(st.normalize_volume?' on':'')};if(nr)nr.style.opacity=st.normalize_volume?'1':'0.4'}
function toggleNorm(){st.normalize_volume=!st.normalize_volume;send({type:'set_normalize_volume',value:st.normalize_volume});updateNormUI()}
var _nv=null,_nt
function onNorm(v){document.getElementById('normv').textContent=v+' LUFS';_nv=+v;clearTimeout(_nt);_nt=setTimeout(flushNorm,200)}
function flushNorm(){if(_nv!=null){st.target_lufs=_nv;send({type:'set_normalize_volume',value:st.normalize_volume,target_lufs:_nv});_nv=null}}
function fmt(s){if(!s)return'';var m=Math.floor(s/60);return m+':'+(Math.floor(s%60)+'').padStart(2,'0')}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function rm(i){var t=(st.queue||[])[i];if(t)send({type:'queue_remove',index:i,path:t.path});_openPath=null}
function addLib(i){if(_res[i]){send({type:'queue_append',path:_res[i].path});document.getElementById('si').value='';document.getElementById('sr').innerHTML='';_res=[]}}
var _st=null
function onS(q){
  clearTimeout(_st)
  if(!q.trim()){document.getElementById('sr').innerHTML='';return}
  var delay=_srMode==='yt'?700:300
  _st=setTimeout(function(){
    if(_srMode==='lib'){send({type:'search_library',query:q})}
    else{document.getElementById('sr').innerHTML='<div class=empty>Suche läuft…</div>';send({type:'yt_search_remote',query:q})}
  },delay)
}
function showRes(rs){
  _res=rs
  var el=document.getElementById('sr')
  if(!rs.length){el.innerHTML='<div class=empty>Keine Ergebnisse</div>';return}
  el.innerHTML=rs.map(function(r,i){
    return'<div class=ri><div class=ri-info><div class=ri-t>'+esc(r.title||'&#8211;')+'</div><div class=ri-a>'+esc(r.artist||'')+(r.duration_sec?' &middot; '+fmt(r.duration_sec):'')+keyChip(r.key,r.key_src,r.bpm,-1)+'</div></div><button class=nxt-b onclick="addNext('+i+')" title="Als n&#228;chstes einreihen">&#9197;</button><button class=add-b onclick="addLib('+i+')" title="Ans Ende">+</button></div>'
  }).join('')
}
function addNext(i){if(_res[i]){send({type:'queue_insert_next',path:_res[i].path});document.getElementById('si').value='';document.getElementById('sr').innerHTML='';_res=[]}}
function showYtRes(rs){
  _ytRes=rs
  var el=document.getElementById('sr')
  if(!rs.length){el.innerHTML='<div class=empty>Keine Ergebnisse</div>';return}
  el.innerHTML=rs.map(function(r,i){
    return'<div class=ri id="ytr'+i+'"><div class=ri-info><div class=ri-t>'+esc(r.title||'&#8211;')+'</div><div class=ri-sub>'+esc(r.uploader||'')+(r.duration?' &middot; '+fmt(r.duration):'')+'</div></div><button class=nxt-b id="nlb'+i+'" onclick="dlYtNext('+i+')" title="Herunterladen &amp; als n&#228;chstes einreihen">&#9197;</button><button class=dl-b id="dlb'+i+'" onclick="dlYt('+i+')" title="Herunterladen &amp; ans Ende">+</button></div>'
  }).join('')
}
function dlYt(i){
  if(!_ytRes[i])return
  var b=document.getElementById('dlb'+i),n=document.getElementById('nlb'+i)
  if(b){b.disabled=true;b.textContent='⏳'}
  if(n)n.disabled=true
  send({type:'yt_dl_queue',url:_ytRes[i].url,title:_ytRes[i].title})
}
function dlYtNext(i){
  if(!_ytRes[i])return
  var b=document.getElementById('dlb'+i),n=document.getElementById('nlb'+i)
  if(n){n.disabled=true;n.textContent='⏳'}
  if(b)b.disabled=true
  send({type:'yt_dl_queue',url:_ytRes[i].url,title:_ytRes[i].title,as_next:true})
}
function updDl(m){
  for(var i=0;i<_ytRes.length;i++){
    if(_ytRes[i].title===m.title){
      var b=document.getElementById('dlb'+i),n=document.getElementById('nlb'+i)
      if(!b)break
      if(m.status==='done'){b.textContent='✓';b.style.color='#75d595';if(n){n.textContent='✓';n.style.color='#75d595'}}
      else{b.textContent='✕';b.style.color='#d57575';b.disabled=false;if(n){n.textContent='⏭';n.style.color='';n.disabled=false}}
      break
    }
  }
}
/* ── Ziehen zum Umsortieren: Finger und Maus (Pointer-Events) ────────────
   Startet sofort am Griff (frueher erst nach 0,16 s Stillhalten — wer gleich
   zog, brach es unbemerkt ab). Status-Updates warten, bis losgelassen wird,
   sonst zeichnete render() die Liste mitten im Ziehen neu. */
var dg={on:false,idx:-1,ghost:null,gy0:0,gy1:0,dropAt:-1,scInt:null,pend:false,el:null,pid:null}
function dhStart(e,i){
  e.preventDefault()
  dg.idx=i;dg.gy0=e.clientY;dg.el=e.currentTarget;dg.pid=e.pointerId
  try{dg.el.setPointerCapture(e.pointerId)}catch(x){}
  dg.el.onpointermove=dhMove;dg.el.onpointerup=dhEnd;dg.el.onpointercancel=dhEnd
  dhAct(i)
}
function dhAct(i){
  dg.on=true
  var rows=document.querySelectorAll('.qi'),src=rows[i];if(!src)return
  var rect=src.getBoundingClientRect()
  var g=document.createElement('div')
  g.className='ghost'
  g.style.cssText='position:fixed;left:8px;right:8px;top:'+rect.top+'px;height:'+rect.height+'px;z-index:999;pointer-events:none;display:flex;align-items:center;padding:0 14px;overflow:hidden'
  var info=src.querySelector('.qi-info');if(info)g.innerHTML=info.outerHTML
  document.body.appendChild(g)
  dg.ghost=g;dg.gy1=rect.top;dg.dropAt=i
  src.style.opacity='.25';showLine(i)
  if(navigator.vibrate)try{navigator.vibrate(15)}catch(x){}
}
function dhMove(e){
  if(!dg.on)return
  e.preventDefault()
  var y=e.clientY,dy=y-dg.gy0
  if(dg.ghost)dg.ghost.style.top=(dg.gy1+dy)+'px'
  var rows=document.querySelectorAll('.qi'),drop=0
  for(var i=0;i<rows.length;i++){var r=rows[i].getBoundingClientRect();if(y>r.top+r.height/2)drop=i+1}
  if(drop!==dg.dropAt){dg.dropAt=drop;showLine(drop)}
  clearInterval(dg.scInt);dg.scInt=null
  var qw=document.getElementById('qw'),qr=qw.getBoundingClientRect()
  if(y<qr.top+60)dg.scInt=setInterval(function(){qw.scrollTop-=8},20)
  else if(y>qr.bottom-60)dg.scInt=setInterval(function(){qw.scrollTop+=8},20)
}
function dhEnd(e){
  clearInterval(dg.scInt);dg.scInt=null
  if(dg.el){try{dg.el.releasePointerCapture(dg.pid)}catch(x){};dg.el.onpointermove=dg.el.onpointerup=dg.el.onpointercancel=null;dg.el=null}
  if(!dg.on)return
  dg.on=false
  if(dg.ghost){dg.ghost.remove();dg.ghost=null}
  hideLine()
  var rows=document.querySelectorAll('.qi')
  for(var i=0;i<rows.length;i++)rows[i].style.opacity=''
  var from=dg.idx,to=dg.dropAt>from?dg.dropAt-1:dg.dropAt
  if(to>=0&&from!==to)send({type:'queue_move',from:from,to:to})
  if(dg.pend){dg.pend=false;render()}
}
function showLine(i){
  var dl=document.getElementById('dl')
  if(!dl){dl=document.createElement('div');dl.id='dl';dl.style.cssText='position:fixed;left:0;right:0;height:3px;background:#3b82f6;z-index:1000;pointer-events:none';document.body.appendChild(dl)}
  var rows=document.querySelectorAll('.qi'),ref=rows[Math.min(i,rows.length-1)]
  if(!ref){dl.style.display='none';return}
  var r=ref.getBoundingClientRect()
  dl.style.top=(i<rows.length?r.top:r.bottom)+'px';dl.style.display='block'
}
function hideLine(){var d=document.getElementById('dl');if(d)d.style.display='none'}
function updPos(m){
  var pos=m.pos!=null?m.pos:(st.position_ms||0)
  var dur=m.dur!=null?m.dur:(st.duration_ms||0)
  var pct=dur>0?Math.min(100,pos/dur*100):0
  document.getElementById('pbf').style.width=pct+'%'
  document.getElementById('pbt').textContent=fmt(pos/1000)
  document.getElementById('pbr').textContent=dur>0?('-'+fmt((dur-pos)/1000)):'–'
}
/* ── Render ─────────────────────────────────────────────────────────────── */
function render(){
  var q=st.queue||[],ci=st.current_idx,cur=q[ci]
  setCover(cur?ci:-1)
  document.getElementById('npT').textContent=cur&&cur.title?cur.title:'–'
  document.getElementById('npA').textContent=cur&&cur.artist?cur.artist:''
  var nx=st.next_title?'↓ '+st.next_title+(st.next_artist?' · '+st.next_artist:''):''
  document.getElementById('npNx').textContent=nx
  var pb=document.getElementById('pb');pb.innerHTML='<svg class="ico"><use href="#i-'+(st.playing?'pause':'play')+'"/></svg>';pb.setAttribute('aria-label',st.playing?'Pause':'Abspielen')
  document.getElementById('vr').value=st.volume
  document.getElementById('vv').textContent=st.volume+'%'
  var nr=document.getElementById('normr')
  if(nr&&_nv==null){nr.value=st.target_lufs;document.getElementById('normv').textContent=st.target_lufs+' LUFS'}
  updateNormUI()
  document.getElementById('qc').textContent=q.length
  // compute ETA for upcoming tracks
  var posMs=st.position_ms||0,durMs=st.duration_ms||0
  var rem=durMs>0?Math.max(0,(durMs-posMs)/1000):0
  var etas={}
  for(var j=ci+1;j<q.length;j++){etas[j]=rem;rem+=q[j].duration_sec||0}
  document.getElementById('ql').innerHTML=q.length?q.map(function(t,i){
    var a=i===ci,p=t.played&&!a
    var etaAbs=etas[i]!=null?absTime(etas[i]):'';
    var row='<div class="qi'+(a?' cur':'')+'" data-i="'+i+'"><div class=dh onpointerdown="dhStart(event,'+i+')" title="Ziehen zum Verschieben">☰</div><div class=qi-info onclick="toggleRow('+i+')"><div class="qi-t'+(a?' a':p?' p':'')+'">'+esc(t.title||'–')+'</div><div class=qi-d>'+fmt(t.duration_sec)+keyChip(t.key,t.key_src,t.bpm,t.compat)+(etaAbs?'<span class=qi-eta> · '+etaAbs+'</span>':'')+'</div></div></div>'
    if(_openPath&&t.path===_openPath){
      row+='<div class=qa>'
      if(!a)row+='<button onclick="mixNow('+i+')">&#8646; Jetzt mischen</button>'
      if(!a&&i!==ci+1)row+='<button onclick="asNext('+i+')">&#9197; Als n&#228;chstes</button>'
      if(!a)row+='<button class=rm onclick="rm('+i+')">&#10005; Entfernen</button>'
      if(a)row+='<button onclick="toggleRow(-1)">L&#228;uft gerade &#8212; schlie&#223;en</button>'
      row+='</div>'
    }
    return row
  }).join(''):'<div class=empty>Warteschlange leer</div>'
  // Nur beim Titelwechsel zum laufenden Titel springen — vorher sprang die
  // Liste bei jeder Aenderung (Lautstaerke, Pause) zurueck, auch mitten beim Scrollen.
  if(ci>=0&&ci!==_lastCi){var cr=document.querySelector('#ql .qi[data-i="'+ci+'"]');if(cr)cr.scrollIntoView({behavior:'smooth',block:'nearest'})}
  _lastCi=ci
  setTog('amb',st.auto_mix,'Auto-Mix')
  setTog('rdb',st.radio_enabled,'Radio')
  renderWishes(st.wishes||[])
}
function setTog(id,on,lbl){var b=document.getElementById(id);if(b){b.className='norm-btn'+(on?' on':'');b.textContent=lbl+(on?' an':' aus')}}
function toggleAM(){send({type:'set_auto_mix',value:!st.auto_mix})}
function toggleRadio(){send({type:'set_radio',enabled:!st.radio_enabled})}
/* Tonart-Chip: gleiche Farben wie in der App (passt / passt nicht) */
function keyChip(k,src,bpm,compat){
  if(!k&&!bpm)return''
  var cls='kc'+(compat>=2?' ok':compat===0?' no':'')+(src==='analyse'?' est':'')
  var sym=compat>=2?'&#10003; ':compat===0?'&#9888; ':''
  return'<span class="'+cls+'">'+(k?sym+esc(k):'')+(k&&bpm?' &middot; ':'')+(bpm?bpm+' BPM':'')+'</span>'
}
function toggleRow(i){var t=(st.queue||[])[i];_openPath=(t&&_openPath!==t.path)?t.path:null;render()}
function mixNow(i){send({type:'play_at',index:i});_openPath=null}
function asNext(i){var ci=st.current_idx,to=i>ci?ci+1:ci;if(ci<0)to=0;send({type:'queue_move',from:i,to:to});_openPath=null}
/* ── Wuensche direkt am Handy ─────────────────────────────────────────── */
var WL={neu:'wartet',laedt:'l&#228;dt&#8230;',analysiert:'analysiert&#8230;',bereit:'bereit',fehler:'Fehler'}
function renderWishes(ws){
  var sec=document.getElementById('wsec')
  sec.style.display=ws.length?'':'none'
  document.getElementById('wcn').textContent=ws.length?ws.length:''
  var rang={bereit:0,analysiert:1,laedt:2,neu:3,fehler:4}
  ws=ws.slice().sort(function(a,b){return(rang[a.status]||9)-(rang[b.status]||9)||(b.count||1)-(a.count||1)})
  document.getElementById('wl').innerHTML=ws.map(function(w){
    var sub=(WL[w.status]||esc(w.status))+((w.count||1)>1?' &middot; '+w.count+'&#215; gew&#252;nscht':'')
    var h='<div class=wi><div class=wi-info><div class=wi-t>'+esc(w.title||'–')+'</div><div class="wi-s'+(w.status==='fehler'?' err':'')+'">'+sub+(w.error?' &middot; '+esc(w.error):'')+'</div></div>'
    if(w.status==='bereit'){
      h+='<button class=nxt-b onclick="wAcc('+w.id+',true)" title="Als n&#228;chstes">&#9197;</button>'
      h+='<button class=add-b onclick="wAcc('+w.id+',false)" title="Ans Ende">+</button>'
    }
    h+='<button class=wno onclick="wRej('+w.id+')" title="Ablehnen">&#10005;</button></div>'
    return h
  }).join('')
}
function wAcc(id,next){send({type:'wish_accept',id:id,as_next:next})}
function wRej(id){if(confirm('Wunsch ablehnen?'))send({type:'wish_reject',id:id})}
function seekAt(e){
  var box=e.currentTarget.getBoundingClientRect()
  var frac=Math.min(1,Math.max(0,(e.clientX-box.left)/box.width))
  var dur=st.duration_ms||0
  if(dur>0){send({type:'seek',position_ms:Math.round(frac*dur)});updPos({pos:frac*dur,dur:dur})}
}
function setCover(ci){
  var el=document.getElementById('cov')
  if(ci==null||ci<0){el.className='cov';el.removeAttribute('src');return}
  var want='/cover?i='+ci+'&k='+encodeURIComponent(KEY)
  if(el.getAttribute('src')===want)return
  el.onload=function(){el.className='cov on'}
  el.onerror=function(){el.className='cov';el.removeAttribute('src')}
  el.setAttribute('src',want)
}
function showPls(items){
  document.getElementById('pll').innerHTML=(items&&items.length)
    ?items.map(function(pl){
        return'<button class=pl-b onclick="loadPl(this)" data-p="'+esc(pl.path)+'">'+esc(pl.name||'?')+'</button>'
      }).join('')
    :'<div class=empty>Keine Playlisten</div>'
}
function loadPl(b){send({type:'remote_load_playlist',path:b.getAttribute('data-p')})}
function absTime(secs){var d=new Date(Date.now()+secs*1000);return'~'+('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)}

/* Sperrt das Handy den Bildschirm, stirbt die Verbindung. Ohne das hier
   merkt die Seite das erst beim naechsten Sendeversuch und haengt beim
   Zurueckkommen ein paar Sekunden auf "getrennt". */
document.addEventListener('visibilitychange',function(){
  if(document.visibilityState==='visible'){conn();lock()}
  else releaseLock()
})
window.addEventListener('online',conn)
window.addEventListener('pageshow',conn)

/* Beim Auflegen soll der Bildschirm nicht dauernd zugehen. */
function lock(){
  if(!navigator.wakeLock||_wl)return
  navigator.wakeLock.request('screen').then(function(w){
    _wl=w
    w.addEventListener('release',function(){_wl=null})
  }).catch(function(){})
}
function releaseLock(){if(_wl){try{_wl.release()}catch(e){}_wl=null}}

conn()
lock()
</script>
</body>
</html>"""

# ── Musikwuensche ────────────────────────────────────────────────────────────
# Gaeste wuenschen sich Titel ueber eine eigene Seite. Der Titel wird sofort
# heruntergeladen und analysiert, damit er beim Annehmen ohne Wartezeit
# spielbar ist. In die Warteschlange kommt er erst, wenn der DJ zustimmt.
_wish_counter = 0

def load_wishes():
    global _wish_counter
    try:
        if core.WISHES_FILE.exists():
            _state["wishes"] = json.loads(core.WISHES_FILE.read_text(encoding="utf-8"))
            _wish_counter = max((w.get("id", 0) for w in _state["wishes"]), default=0)
    except Exception as e:
        print(f"[wishes] konnte nicht geladen werden: {e}", flush=True)
        _state["wishes"] = []

def save_wishes():
    try:
        core.WISHES_FILE.write_text(json.dumps(_state.get("wishes", []),
                                          ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[wishes] konnte nicht gespeichert werden: {e}", flush=True)

def _resume_wishes():
    """Wuensche, die beim Beenden noch in Arbeit waren, weiterfuehren.

    Vorher blieben sie fuer immer auf "laedt…" stehen: gespeichert wird der
    Status, die Arbeit selbst lief nur im alten Prozess.
    """
    for w in _state.get("wishes", []):
        if w.get("status") not in ("neu", "laedt", "analysiert"):
            continue
        if w.get("path") and os.path.exists(w["path"]):
            w["status"] = "bereit"
        else:
            asyncio.create_task(_process_wish(w))
    save_wishes()

async def push_wishes():
    await core.broadcast({"type": "wishes", "items": _state.get("wishes", [])})
    if _remote_clients:
        asyncio.create_task(_broadcast_remote_state())

def _title_matches(a: str, b: str) -> bool:
    na, nb = library._norm_queue_title(a), library._norm_queue_title(b)
    if not na or not nb:
        return False
    return SequenceMatcher(None, na, nb).ratio() >= 0.82

def _queue_eta_sec(idx: int) -> float:
    """Sekunden, bis der Titel an Position idx an der Reihe ist."""
    q  = _state.get("queue", [])
    ci = _state.get("current_idx", -1)
    if idx <= ci:
        return 0.0
    dur = _state.get("duration_ms", 0) / 1000
    pos = _state.get("position_ms", 0) / 1000
    rem = max(0.0, dur - pos) if dur > 0 else 0.0
    for j in range(ci + 1, idx):
        if 0 <= j < len(q):
            rem += q[j].get("duration_sec", 0) or 0
    return rem

_WISH_PLAYED_WINDOW = 12 * 3600   # "lief schon" gilt fuer die letzten 12 Stunden

# Was aus angenommenen und abgelehnten Wuenschen wurde — die Wuensche selbst
# verschwinden dabei aus der Liste, der Gast will aber wissen, wann sein Titel
# kommt. Nur im Speicher: nach einem Neustart zaehlt der neue Abend.
_wish_outcomes: dict[int, dict] = {}

def _guest_wish_status(wid: int) -> dict | None:
    """Stand eines Wunsches aus Sicht des Gastes. Keine Pfade, keine IPs."""
    q, ci = _state.get("queue", []), _state.get("current_idx", -1)
    out = _wish_outcomes.get(wid)
    if out:
        base = {"id": wid, "title": out.get("title", "")}
        if out["state"] == "abgelehnt":
            return {**base, "state": "rejected"}
        i = next((j for j, t in enumerate(q) if t.get("path") == out.get("path")), -1)
        if i == ci and i >= 0:
            return {**base, "state": "playing"}
        if i >= 0 and q[i].get("played"):
            return {**base, "state": "played", "at": q[i].get("played_at", 0)}
        if i > ci:
            return {**base, "state": "queued", "in_sec": round(_queue_eta_sec(i))}
        if i < 0:
            # Nicht mehr in der Warteschlange: lief er (und wurde danach automatisch
            # entfernt) oder hat ihn der DJ wieder herausgenommen?
            since = out.get("at", 0)
            hit = next((h for h in _state.get("play_log", [])
                        if h.get("path") == out.get("path") and (h.get("played_at") or 0) >= since - 1), None)
            if hit:
                return {**base, "state": "played", "at": hit.get("played_at", 0)}
            return {**base, "state": "rejected"}
        return {**base, "state": "accepted"}
    w = next((x for x in _state.get("wishes", []) if x.get("id") == wid), None)
    if w:
        return {"id": wid, "title": w.get("title", ""),
                "state": "failed" if w.get("status") == "fehler" else "pending"}
    return None

def _guest_now_next() -> dict:
    """Laufender Titel und die naechsten drei — fuer die Wunschseite."""
    q, ci = _state.get("queue", []), _state.get("current_idx", -1)
    lib = {lt.get("path"): lt for lt in _state.get("library", [])}
    def _t(t):
        return {"title": t.get("title", ""),
                "artist": t.get("artist", "") or (lib.get(t.get("path")) or {}).get("artist", "")}
    now = _t(q[ci]) if 0 <= ci < len(q) and _state.get("playing") else None
    nxt = [_t(t) for t in q[ci + 1:] if not t.get("played")][:3]
    return {"now": now, "next": nxt}

def _wish_title_status(title: str) -> dict:
    """Laeuft der Titel schon, lief er bereits, oder wuenscht ihn schon wer?"""
    # 1. Steht er in der Warteschlange? Dann die Uhrzeit dazu.
    for i, q in enumerate(_state.get("queue", [])):
        if _title_matches(title, q.get("title", "")):
            if i == _state.get("current_idx", -1):
                return {"state": "playing"}
            if q.get("played"):
                continue
            return {"state": "queued", "in_sec": round(_queue_eta_sec(i))}
    # 2. Lief er an diesem Abend schon? Das Play-Log reicht ueber Wochen
    # zurueck — ohne Grenze stand bei den Gaesten "lief um 21:14 Uhr" fuer
    # einen Titel von letzter Woche, und Wuenschen ging trotzdem.
    seit = time.time() - _WISH_PLAYED_WINDOW
    for entry in _state.get("play_log", []):
        if entry.get("played_at", 0) < seit:
            continue
        if _title_matches(title, entry.get("title", "")):
            return {"state": "played", "at": entry.get("played_at", 0)}
    # 3. Hat ihn schon jemand gewuenscht?
    for w in _state.get("wishes", []):
        if w.get("status") != "abgelehnt" and _title_matches(title, w.get("title", "")):
            return {"state": "wished", "count": w.get("count", 1)}
    return {"state": "free"}

async def _process_wish(wish: dict):
    """Titel herunterladen und analysieren, damit er sofort spielbar ist."""
    def _set(status: str, **kw):
        wish["status"] = status
        wish.update(kw)
        save_wishes()

    _set("laedt")
    await push_wishes()
    # Was schon vor dem Wunsch da war (Bibliothek, fruehere Downloads), darf
    # beim Ablehnen nicht im Papierkorb landen — run_download liefert fuer
    # bereits geladene Titel einfach die vorhandene Datei zurueck.
    vorher = {lt.get("path") for lt in _state.get("library", [])} | \
             {h.get("path") for h in _state.get("history", [])}
    try:
        path = await download.run_download(wish["url"], _state.get("wish_format", "mp3-best"))
    except Exception as e:
        _set("fehler", error=str(e)[:120]); await push_wishes(); return

    if not path or not os.path.exists(path):
        # Den echten Grund aus dem Download-Eintrag holen — "fehlgeschlagen"
        # allein hilft beim Auflegen niemandem weiter.
        eintrag = next((d for d in _state.get("downloads", [])
                        if d.get("url") == wish["url"] and d.get("error_msg")), None)
        grund = (eintrag or {}).get("error_msg") or "Download fehlgeschlagen"
        _set("fehler", error=grund[:120]); await push_wishes(); return

    _set("analysiert", path=path, keep_file=path in vorher)
    await push_wishes()
    try:
        await media._enrich_track(path, force=True)
    except Exception:
        pass          # ohne BPM/LUFS ist er trotzdem spielbar
    _set("bereit", path=path)
    await push_wishes()

remote_app = FastAPI()
remote_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def _off_page(was: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>SynthiMIX</title><body style=\"font-family:system-ui,sans-serif;background:#0b1220;color:#e6edf6;"
        "display:flex;align-items:center;justify-content:center;min-height:90vh;text-align:center;padding:24px\">"
        f"<div><h2 style=\"margin:0 0 8px\">{was} ist gerade ausgeschaltet</h2>"
        "<p style=\"color:#93a4bb\">Der DJ kann sie in SynthiMIX wieder einschalten.</p></div>", status_code=503)

@remote_app.get("/")
async def remote_index(k: str = ""):
    # Ohne gueltigen Schluessel: freundlich auf die Wunschseite
    if not _remote_key_ok(k):
        return RedirectResponse("/wunsch")
    if not _remote_services["remote"]:
        return RedirectResponse("/wunsch") if _remote_services["wishes"] else _off_page("Die Fernbedienung")
    return HTMLResponse(_REMOTE_HTML.replace("__KEY__", _remote_key()))

# Cover werden per ffmpeg aus der Datei geholt — das dauert, also einmal je
# Pfad merken. None heisst "hat keins", damit nicht jedes Mal neu gesucht wird.
_remote_art: dict[str, bytes | None] = {}

@remote_app.get("/cover")
async def remote_cover(i: int = -1, k: str = ""):
    """Cover des Titels an Warteschlangenposition i.

    Bewusst ueber den Index und nicht ueber einen Pfad: ein Pfad aus der
    Anfrage waere ein Weg, beliebige Dateien vom Rechner zu lesen.
    """
    if not _remote_key_ok(k):
        return Response(status_code=403)
    q = _state.get("queue", [])
    if not (0 <= i < len(q)):
        return Response(status_code=404)
    path = q[i].get("path", "")
    if not path or not os.path.exists(path):
        return Response(status_code=404)

    if path not in _remote_art:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, media._extract_art_sync, path)
        if data and data.startswith("data:image/jpeg;base64,"):
            _remote_art[path] = base64.b64decode(data.split(",", 1)[1])
        else:
            _remote_art[path] = None
        if len(_remote_art) > 200:
            _remote_art.clear()

    art = _remote_art[path]
    if not art:
        return Response(status_code=404)
    return Response(content=art, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=3600"})

_WISH_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Musikwunsch</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg viewBox='0 0 40 40' xmlns='http://www.w3.org/2000/svg'%3E%3Ccircle cx='20' cy='20' r='19' fill='%230d1a2e'/%3E%3Crect x='5' y='16' width='4' height='9' rx='1.5' fill='%23e07800'/%3E%3Crect x='11' y='10' width='4' height='15' rx='1.5' fill='%23e07800'/%3E%3Crect x='17' y='13' width='4' height='12' rx='1.5' fill='%23f59332'/%3E%3Crect x='23' y='7' width='4' height='18' rx='1.5' fill='%23e07800'/%3E%3Crect x='29' y='11' width='4' height='14' rx='1.5' fill='%23f59332'/%3E%3Cline x1='20' y1='29' x2='20' y2='35' stroke='%233b82f6' stroke-width='2' stroke-linecap='round'/%3E%3Cpolyline points='16,32 20,36 24,32' fill='none' stroke='%233b82f6' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<meta name="theme-color" content="#0d1625">
<style>
/* Grundsystem von SynthiMIX (App.svelte / lib/ui.css) als eigene Variablen —
   die Seite laeuft ohne die App. Werte hier mitziehen, wenn sich die App aendert.
   Handy: Schrift mindestens 13px, Klickziele mindestens 44px. */
:root{
  --bg:#0a0e18;--bg2:#080c16;--surf:#0e1624;--hover:#101828;--sel:#0e1c38;
  --br1:#121e30;--br2:#1e2e44;--br3:#2a3e5c;
  --tx1:#ecf2ff;--tx2:#d4e0f2;--tx3:#b0c6dc;--tx4:#9ab2cc;--tx5:#8ba6c4;
  --accent:#e07800;--accent2:#ff9020;--accent-tx:#ff9a33;--on-accent:#0a0e18;--act-bg:#1a1206;
  --green-tx:#6fcf7c;--green-bg:#0e1a10;--green-br:#2a6a30;
  --red-tx:#ff7b7b;--red-bg:#1a0808;--red-br:#8a3030;
  --blue-tx:#7fb0ec;--blue-bg:#0e1a2c;--blue-br:#2a5888;
  --r-s:4px;--r-m:8px;--r-l:12px;
  --fs-sm:13px;--fs-body:15px;--fs-lg:17px;--fs-h:20px;
  --tap:44px;
  color-scheme:dark;
}
/* Handy auf hell gestellt: helles Theme der App — draussen in der Sonne lesbarer */
@media (prefers-color-scheme: light){
  :root{
    --bg:#f4f0eb;--bg2:#ebe7e1;--surf:#ffffff;--hover:#e0dcd6;--sel:#ddeeff;
    --br1:#d6d0c8;--br2:#bdb6ad;--br3:#a39b91;
    --tx1:#0a0806;--tx2:#1e1a14;--tx3:#3a342a;--tx4:#4a443c;--tx5:#554e47;
    --accent:#b35400;--accent2:#c86000;--accent-tx:#9a4700;--on-accent:#ffffff;--act-bg:#fff1dc;
    --green-tx:#1a7030;--green-bg:#eaf6ec;--green-br:#8ac098;
    --red-tx:#b82020;--red-bg:#fff0f0;--red-br:#d89a9a;
    --blue-tx:#1a5cb0;--blue-bg:#e8f0fb;--blue-br:#93b6e0;
    color-scheme:light;
  }
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--tx2);font:var(--fs-body)/1.45 'Segoe UI',system-ui,-apple-system,sans-serif;padding-bottom:32px}
button{font:inherit}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent-tx);outline-offset:2px}

/* Einladender Kopf */
.hdr{padding:calc(22px + env(safe-area-inset-top,0px)) 20px 18px;text-align:center;background:var(--bg2);border-bottom:1px solid var(--br1)}
.logo{font-weight:700;font-size:var(--fs-lg);color:var(--accent-tx)}.logo span{color:var(--blue-tx)}
.sub{font-size:24px;font-weight:700;color:var(--tx1);margin-top:6px;line-height:1.2}
.sub-2{font-size:var(--fs-body);color:var(--tx3);margin-top:6px}

/* Laeuft gerade */
.np{display:none;margin:16px 16px 0;padding:14px 16px;border-radius:var(--r-l);background:var(--surf);border:1px solid var(--br2)}
.np-l{display:flex;align-items:center;gap:8px;font-size:var(--fs-sm);font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--green-tx)}
.np-l::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.np-t{font-size:var(--fs-lg);font-weight:700;color:var(--tx1);margin-top:4px;word-break:break-word}
.np-n{font-size:var(--fs-sm);color:var(--tx3);margin-top:8px;line-height:1.5}
.np-h{font-size:var(--fs-sm);font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--tx3);margin-bottom:2px}
.np-list{margin:0;padding-left:20px;color:var(--tx1);font-size:var(--fs-body)}
.np-list li{padding:2px 0;word-break:break-word}
.np-list span{color:var(--tx3)}

.wrap{padding:16px}
.inp{width:100%;min-height:52px;border-radius:var(--r-l);border:2px solid var(--br3);background:var(--surf);
  color:var(--tx1);font-size:17px;padding:0 16px}
.inp::placeholder{color:var(--tx4)}
.inp:focus{border-color:var(--accent);outline:none}
.note{font-size:var(--fs-sm);color:var(--tx3);margin:10px 2px 16px;line-height:1.5}

/* Bestaetigung nach dem Wuenschen: gross und deutlich */
.msg{display:flex;align-items:flex-start;gap:10px;padding:14px 16px;border-radius:var(--r-l);
  font-size:var(--fs-body);font-weight:600;line-height:1.4;color:var(--green-tx);background:var(--green-bg);border:1px solid var(--green-br)}
.msg::before{content:"\\2713";font-size:20px;line-height:1;font-weight:700}
.msg.no{color:var(--red-tx);background:var(--red-bg);border-color:var(--red-br)}
.msg.no::before{content:"!"}

/* Eigene Wuensche */
.mine{display:none;margin:0 0 18px;padding:12px 14px;border-radius:var(--r-l);background:var(--surf);border:1px solid var(--br2)}
.mine-h{font-size:var(--fs-sm);font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--tx3);margin-bottom:4px}
.mi{display:flex;justify-content:space-between;align-items:center;gap:10px;min-height:44px;border-bottom:1px solid var(--br1)}
.mi:last-child{border-bottom:none}
.mi-t{flex:1;min-width:0;font-size:var(--fs-body);color:var(--tx1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mi-s{flex-shrink:0;font-size:var(--fs-sm);font-weight:600;color:var(--tx3)}
.mi-s.ok{color:var(--green-tx)}.mi-s.no{color:var(--red-tx)}

/* Suchergebnisse mit Status-Etiketten */
.r{display:flex;align-items:center;gap:12px;min-height:68px;padding:10px 0;border-bottom:1px solid var(--br1)}
.r-i{flex:1;min-width:0}
.r-t{font-size:var(--fs-body);font-weight:600;color:var(--tx1);line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.r-s{font-size:var(--fs-sm);margin-top:4px;color:var(--tx3)}
.s-free{color:var(--tx3)}
.s-playing,.s-queued,.s-played,.s-wished{display:inline-block;font-weight:700;padding:2px 8px;border-radius:999px}
.s-playing{color:var(--on-accent);background:var(--green-tx)}
.s-queued{color:var(--accent-tx);border:1px solid var(--accent)}
.s-played{color:var(--tx2);background:var(--hover)}
.s-wished{color:var(--blue-tx);border:1px solid var(--blue-br);background:var(--blue-bg)}
.lib{color:var(--green-tx);font-weight:700}
.b{flex-shrink:0;min-height:48px;min-width:108px;padding:0 16px;border-radius:var(--r-m);cursor:pointer;
  font-size:var(--fs-body);font-weight:700;border:none;background:var(--accent);color:var(--on-accent)}
.b:active{transform:scale(.97)}
.b:disabled{background:var(--hover);color:var(--tx4);cursor:default}
.empty{text-align:center;padding:24px 10px;font-size:var(--fs-sm);color:var(--tx3)}
</style>
</head>
<body>
<header class="hdr">
  <div class="logo">Synthi<span>MIX</span></div>
  <h1 class="sub">Was m&#246;chtest du h&#246;ren?</h1>
  <div class="sub-2">Titel suchen, auf W&#252;nschen tippen &#8212; der DJ sieht deinen Wunsch sofort.</div>
</header>
<div class="np" id="np">
  <div class="np-l" id="npL">L&#228;uft gerade</div>
  <div class="np-t" id="npT"></div>
  <div class="np-n" id="npN"></div>
</div>
<div class="wrap">
  <input class="inp" id="q" type="search" placeholder="Titel oder K&#252;nstler suchen&#8230;" autocomplete="off" aria-label="Titel oder K&#252;nstler suchen">
  <div class="note" id="note" role="status">Mindestens zwei Buchstaben eingeben. Titel mit &#8222;&#10003; sofort da&#8220; kann der DJ gleich spielen.</div>
  <div class="mine" id="mine"><div class="mine-h">Deine W&#252;nsche</div><div id="mineL"></div></div>
  <div id="res"></div>
</div>
<script>
var ws,_t,_busy={}
/* Eigene Wuensche merkt sich das Handy (12 Stunden), damit der Gast sieht,
   ob sein Titel angenommen wurde und wann er ungefaehr laeuft. */
function mineLoad(){try{var a=JSON.parse(localStorage.getItem('synthimix-wuensche')||'[]');var g=Date.now()-12*3600e3;return a.filter(function(x){return x.at>g})}catch(e){return[]}}
function mineAdd(id){if(!id)return;var a=mineLoad().filter(function(x){return x.id!==id});a.push({id:id,at:Date.now()});try{localStorage.setItem('synthimix-wuensche',JSON.stringify(a.slice(-20)))}catch(e){}}
function info(){if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'wish_info',ids:mineLoad().map(function(x){return x.id})}))}
setInterval(info,15000)
document.addEventListener('visibilitychange',function(){if(document.visibilityState==='visible'){conn();info()}})
function showInfo(m){
  var np=document.getElementById('np')
  var nx=m.next||[]
  np.style.display=(m.now||nx.length)?'block':'none'
  document.getElementById('npL').style.display=m.now?'':'none'
  document.getElementById('npT').textContent=m.now?m.now.title+(m.now.artist&&m.now.title.indexOf(m.now.artist)<0?' · '+m.now.artist:''):''
  document.getElementById('npN').innerHTML=nx.length?'<div class=np-h>Danach</div><ol class=np-list>'+nx.map(function(t){
    return '<li>'+esc(t.title)+(t.artist&&t.title.indexOf(t.artist)<0?' <span>· '+esc(t.artist)+'</span>':'')+'</li>'}).join('')+'</ol>':''
  var mine=m.mine||[],box=document.getElementById('mine')
  box.style.display=mine.length?'block':'none'
  document.getElementById('mineL').innerHTML=mine.map(function(w){
    var t='wartet auf den DJ',c=''
    if(w.state==='queued'){t='l&#228;uft etwa um '+inClock(w.in_sec||0)+' Uhr';c='ok'}
    else if(w.state==='playing'){t='l&#228;uft gerade!';c='ok'}
    else if(w.state==='played'){t='lief um '+clock(w.at||0)+' Uhr';c='ok'}
    else if(w.state==='accepted'){t='angenommen';c='ok'}
    else if(w.state==='rejected'){t='leider nicht dabei';c='no'}
    else if(w.state==='failed'){t='konnte nicht geladen werden';c='no'}
    return'<div class=mi><span class=mi-t>'+esc(w.title)+'</span><span class="mi-s '+c+'">'+t+'</span></div>'
  }).join('')
}
function conn(){
  if(ws&&(ws.readyState===0||ws.readyState===1))return
  ws=new WebSocket('ws://'+location.host+'/wunsch/ws')
  ws.onopen=info
  ws.onclose=function(){setTimeout(conn,2000)}
  ws.onerror=function(){ws.close()}
  ws.onmessage=function(e){
    var m=JSON.parse(e.data)
    if(m.type==='wish_results')show(m.results||[],m.final!==false)
    else if(m.type==='wish_info')showInfo(m)
    else if(m.type==='wish_ack'){
      mineAdd(m.id);info()
      document.getElementById('note').innerHTML='<div class=msg><span>'+esc(m.title||'Dein Wunsch')+' ist beim DJ angekommen. Unten unter &#8222;Deine W&#252;nsche&#8220; siehst du, wann er l&#228;uft.</span></div>'
      document.getElementById('res').innerHTML=''
      document.getElementById('q').value=''
    }
    else if(m.type==='wish_deny'){
      document.getElementById('note').innerHTML='<div class="msg no">'+esc(m.text||'Das ging nicht.')+'</div>'
    }
  }
}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function clock(ts){var d=new Date(ts*1000);return('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)}
function inClock(sec){return clock(Date.now()/1000+sec)}
document.getElementById('q').oninput=function(){
  var v=this.value.trim()
  clearTimeout(_t)
  if(v.length<2){document.getElementById('res').innerHTML='';return}
  _t=setTimeout(function(){
    document.getElementById('res').innerHTML='<div class=empty>Suche&#8230;</div>'
    if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'wish_search',query:v}))
  },400)
}
function show(rs,final){
  window._rs=rs
  if(!rs.length){document.getElementById('res').innerHTML=final?'<div class=empty>Nichts gefunden</div>':'<div class=empty>Suche&#8230;</div>';return}
  document.getElementById('res').innerHTML=rs.map(function(r,i){
    var st=r.status||{},txt='',cls='s-free',dis=''
    if(st.state==='playing'){txt='l&#228;uft gerade';cls='s-playing';dis=' disabled'}
    else if(st.state==='queued'){txt='l&#228;uft etwa um '+inClock(st.in_sec||0)+' Uhr';cls='s-queued';dis=' disabled'}
    else if(st.state==='played'){txt='lief um '+clock(st.at||0)+' Uhr';cls='s-played'}
    else if(st.state==='wished'){txt='schon gew&#252;nscht'+(st.count>1?' ('+st.count+'x)':'');cls='s-wished';dis=' disabled'}
    else txt=(r.lib?'<span class=lib>&#10003; sofort da</span>'+(r.uploader?' &middot; ':''):'')+esc(r.uploader||'')
    return'<div class=r><div class=r-i><div class=r-t>'+esc(r.title)+'</div><div class="r-s '+cls+'">'+txt+'</div></div>'+
      '<button class=b'+dis+' onclick="wish('+i+',this)">W&#252;nschen</button></div>'
  }).join('')+(final?'':'<div class=empty>Suche weiter auf YouTube&#8230;</div>')
}
function wish(i,btn){
  var r=(window._rs||[])[i]
  var k=r&&(r.url||r.lib)
  if(!r||_busy[k])return
  _busy[k]=1;btn.disabled=true;btn.textContent='…'
  ws.send(JSON.stringify(r.lib?{type:'wish_add',lib:r.lib,title:r.title}:{type:'wish_add',url:r.url,title:r.title}))
}
conn()
</script>
</body>
</html>"""

@remote_app.get("/wunsch")
async def wish_page():
    if not _remote_services["wishes"]:
        return _off_page("Die Musikwunsch-Seite")
    return HTMLResponse(_WISH_HTML)

# Wie viele offene Wuensche ein Geraet gleichzeitig haben darf. Ohne Grenze
# kippt ein einzelner Spassvogel die Liste voll.
_WISH_LIMIT_PER_CLIENT = 3

@remote_app.websocket("/wunsch/ws")
async def wish_ws(websocket: WebSocket):
    """Bewusst getrennt vom Remote: hier gibt es keine Wiedergabesteuerung."""
    global _wish_counter
    if not _remote_services["wishes"]:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _wish_clients.add(websocket)
    who = websocket.client.host if websocket.client else "?"
    try:
        while True:
            msg = await websocket.receive_json()
            t = msg.get("type", "")

            if t == "wish_search":
                q = (msg.get("query") or "").strip()
                if q:
                    core._start_search(websocket, "wish", _do_wish_search(q, websocket))

            elif t == "wish_info":
                ids = [int(i) for i in (msg.get("ids") or [])[:20] if str(i).isdigit()]
                mine = [st for st in (_guest_wish_status(i) for i in ids) if st]
                await websocket.send_text(json.dumps({"type": "wish_info", "mine": mine,
                                                      **_guest_now_next()}))

            elif t == "wish_add":
                url    = (msg.get("url") or "").strip()
                title  = (msg.get("title") or "").strip()
                lib_id = (msg.get("lib") or "").strip()
                lib_entry = None
                if lib_id:
                    # Nie einen Pfad vom Gast uebernehmen — nur ueber die Kennung
                    lib_entry = next((lt for lt in _state.get("library", [])
                                      if lt.get("path") and _lib_wish_id(lt["path"]) == lib_id), None)
                    if lib_entry is None:
                        continue
                    title = lib_entry.get("title", "") or title
                elif not url.startswith("http"):
                    continue

                offen = [w for w in _state.get("wishes", []) if w.get("from") == who]
                if len(offen) >= _WISH_LIMIT_PER_CLIENT:
                    await websocket.send_text(json.dumps({"type": "wish_deny",
                        "text": f"Du hast schon {len(offen)} Wuensche offen. Warte, bis der DJ sie bearbeitet hat."}))
                    continue

                # Schon gewuenscht? Dann nur hochzaehlen — das zeigt dem DJ,
                # was die Leute wirklich hoeren wollen.
                vorhanden = next((w for w in _state.get("wishes", [])
                                  if (url and w.get("url") == url)
                                  or (lib_entry and w.get("path") == lib_entry["path"])
                                  or _title_matches(title, w.get("title", ""))), None)
                if vorhanden:
                    vorhanden["count"] = vorhanden.get("count", 1) + 1
                    save_wishes()
                    await push_wishes()
                    # Mit Kennung — so verfolgt auch der zweite Gast den Wunsch
                    await websocket.send_text(json.dumps({"type": "wish_ack", "title": title,
                                                          "id": vorhanden.get("id")}))
                    continue

                _wish_counter += 1
                wish = {"id": _wish_counter, "url": url, "title": title,
                        "from": who, "count": 1, "status": "neu",
                        "path": None, "error": "", "created_at": int(time.time())}
                if lib_entry is not None:
                    # Liegt schon auf der Platte: sofort bereit, nichts zu laden
                    wish.update(status="bereit", path=lib_entry["path"], from_library=True)
                _state.setdefault("wishes", []).append(wish)
                save_wishes()
                await push_wishes()
                await websocket.send_text(json.dumps({"type": "wish_ack", "title": title,
                                                      "id": wish["id"]}))
                if lib_entry is None:
                    asyncio.create_task(_process_wish(wish))
                elif lib_entry.get("lufs", -99) <= -90:
                    asyncio.create_task(media._enrich_track(lib_entry["path"]))
    except Exception:
        pass
    finally:
        _wish_clients.discard(websocket)

def _lib_wish_id(path: str) -> str:
    """Kennung eines Bibliothekstitels fuer die Wunschseite — der Pfad selbst
    bleibt auf dem Rechner, Gaeste sollen die Ordnerstruktur nicht sehen."""
    import hashlib
    return hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:12]

def _wish_library_hits(query: str, limit: int = 5) -> list[dict]:
    words = [w for w in query.lower().split() if w]
    if not words:
        return []
    hits = []
    for lt in _state.get("library", []):
        if lt.get("missing") or not lt.get("path"):
            continue
        text = f"{lt.get('title', '')} {lt.get('artist', '')}".lower()
        if all(w in text for w in words):
            hits.append(lt)
            if len(hits) >= limit:
                break
    return hits

_WISH_SKIP_RE = re.compile(r"\b(instrumental|karaoke|acapella|a\s*cappella|sped\s*up|slowed|nightcore|8d\s*audio|backing\s*track)\b", re.IGNORECASE)


async def _do_wish_search(query: str, ws: WebSocket):
    # Erst die eigene Sammlung: sofort spielbar, kein Download, keine
    # YouTube-Kopie in schlechter Qualitaet. Die Treffer gehen gleich raus,
    # YouTube folgt ein paar Sekunden spaeter.
    lib_hits = [{"lib": _lib_wish_id(lt["path"]), "title": lt.get("title", ""),
                 "uploader": lt.get("artist", ""),
                 "status": _wish_title_status(lt.get("title", ""))}
                for lt in _wish_library_hits(query)]
    async def _send(results, final):
        try:
            await ws.send_text(json.dumps({"type": "wish_results", "results": results,
                                           "final": final}))
        except Exception:
            pass
    if lib_hits:
        await _send(lib_hits, False)

    def online(results):
        # Songs tragen erst nach _ytm_fill_details den Kuenstler — fuer Gaeste
        # und den Abgleich mit der Sammlung als "Kuenstler - Titel" zeigen
        out = []
        for r in results:
            title = f'{r["artist"]} - {r["title"]}' if r.get("kind") == "song" and r.get("artist") else r["title"]
            if any(_title_matches(title, h["title"]) for h in lib_hits):
                continue   # gibt es schon in der Sammlung
            out.append({"url": r["url"], "title": title, "uploader": r.get("uploader", ""),
                        "status": _wish_title_status(title)})
        return out[:max(3, 8 - len(lib_hits))]

    # Nur Song-Versionen von YouTube Music — keine beliebigen YouTube-Videos,
    # keine Instrumentals, Karaoke- oder Live-Fassungen
    songs = await search._ytm_songs(query, 10, details=False)
    if songs and not all(r.get("duration") for r in songs):
        await _send(lib_hits + online(songs), False)
        await search._ytm_fill_details(songs)
    songs = [r for r in songs if not search._is_unwanted_result(r) and not search.LIVE_RE.search(r.get("title", ""))
             and not _WISH_SKIP_RE.search(r.get("title", ""))]
    await _send(lib_hits + online(songs), True)

@remote_app.get("/manifest.json")
async def remote_manifest(k: str = ""):
    """Macht die Seite ueber "Zum Startbildschirm" zur eigenstaendigen App."""
    if not _remote_key_ok(k):
        return Response(status_code=403)
    return JSONResponse({
        "name": "SynthiMIX Remote",
        "short_name": "SynthiMIX",
        "start_url": f"/?k={_remote_key()}",
        "display": "standalone",
        "background_color": "#0a0f1a",
        "theme_color": "#0d1625",
        "icons": [],
    })

@remote_app.websocket("/ws")
async def remote_ws_endpoint(websocket: WebSocket):
    if not _remote_key_ok(websocket.query_params.get("k")) or not _remote_services["remote"]:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _remote_clients.add(websocket)
    try:
        await _send_remote_state(websocket)
        while True:
            msg = await websocket.receive_json()
            t = msg.get("type", "")
            if t == "queue_move":
                q = _state["queue"]
                fi, ti = int(msg.get("from", 0)), int(msg.get("to", 0))
                if 0 <= fi < len(q) and 0 <= ti < len(q) and fi != ti:
                    item = q.pop(fi)
                    q.insert(ti, item)
                    ci = _state.get("current_idx", -1)
                    if ci == fi:            _state["current_idx"] = ti
                    elif fi < ci <= ti:     _state["current_idx"] = ci - 1
                    elif ti <= ci < fi:     _state["current_idx"] = ci + 1
                    store.save_queue()
                    await core.push_queue()
            elif t == "queue_remove":
                idx = int(msg.get("index", -1))
                q = _state["queue"]
                # Ueber den Pfad: hat sich die Warteschlange seit dem Anzeigen
                # geaendert, traf der Index sonst einen anderen Titel.
                path = msg.get("path")
                if path:
                    if not (0 <= idx < len(q) and q[idx].get("path") == path):
                        idx = next((i for i, t in enumerate(q) if t.get("path") == path), -1)
                if 0 <= idx < len(q):
                    q.pop(idx)
                    ci = _state.get("current_idx", -1)
                    if idx < ci:   _state["current_idx"] = ci - 1
                    elif idx == ci: _state["current_idx"] = min(ci, len(q) - 1)
                    store.save_queue()
                    await core.push_queue()
            elif t == "queue_append":
                path = msg.get("path", "")
                lib = _state.get("library", [])
                td = next((x for x in lib if x.get("path") == path), None)
                if td and not library._queue_is_duplicate(path, td.get("title", "")):
                    _state["queue"].append({
                        "path": path,
                        "title": td.get("title") or Path(path).stem,
                        "duration_sec": td.get("duration_sec", 0),
                        "lufs": td.get("lufs", -99.0),
                        "bpm": td.get("bpm", 0),
                        "bitrate_kbps": td.get("bitrate_kbps", 0),
                        "played": False,
                    })
                    store.save_queue()
                    await core.push_queue()
            elif t == "queue_insert_next":
                path = msg.get("path", "")
                lib = _state.get("library", [])
                td = next((x for x in lib if x.get("path") == path), None)
                if td:
                    ci = _state.get("current_idx", -1)
                    insert_at = ci + 1 if ci >= 0 else 0
                    _state["queue"].insert(insert_at, {
                        "path": path,
                        "title": td.get("title") or Path(path).stem,
                        "duration_sec": td.get("duration_sec", 0),
                        "lufs": td.get("lufs", -99.0),
                        "bpm": td.get("bpm", 0),
                        "bitrate_kbps": td.get("bitrate_kbps", 0),
                        "played": False,
                    })
                    store.save_queue()
                    await core.push_queue()
            elif t == "search_library":
                query = (msg.get("query") or "").lower()
                if query and query != "__clear__":
                    lib = _state.get("library", [])
                    results = [
                        {"title": x.get("title",""), "artist": x.get("artist",""),
                         "path": x.get("path",""), "duration_sec": x.get("duration_sec",0),
                         "key": x.get("key", ""), "key_src": x.get("key_src", ""),
                         "bpm": x.get("bpm", 0)}
                        for x in lib
                        if query in (x.get("title","") or "").lower()
                        or query in (x.get("artist","") or "").lower()
                    ][:30]
                    await websocket.send_text(json.dumps({"type": "search_results", "results": results}))
            elif t == "yt_search_remote":
                query = msg.get("query", "").strip()
                if query:
                    core._start_search(websocket, "remote", _do_yt_search_remote(query, websocket))
            elif t == "yt_dl_queue":
                url = msg.get("url", "")
                title = msg.get("title", "")
                as_next = bool(msg.get("as_next", False))
                if url:
                    asyncio.create_task(_remote_dl_and_queue(url, title, websocket, as_next))
            elif t in ("set_normalize_volume", "wish_accept", "wish_reject",
                       "set_auto_mix", "set_radio"):
                await handle_message(websocket, msg)
            elif t == "remote_playlists":
                await websocket.send_text(json.dumps(
                    {"type": "playlists", "items": library._get_playlists()}))
            elif t == "remote_load_playlist":
                # Pfad gegen die bekannten Playlisten pruefen — load_playlist
                # wuerde sonst jede beliebige Datei einlesen.
                want  = msg.get("path", "")
                known = {pl.get("path") for pl in library._get_playlists()}
                if want in known:
                    await handle_message(websocket, {"type": "load_playlist", "path": want})
            elif t in ("pause", "resume", "play_at", "play_next", "play_prev",
                       "set_volume", "seek"):
                await handle_message(websocket, msg)
    except Exception:
        pass
    finally:
        _remote_clients.discard(websocket)

async def _do_yt_search_remote(query: str, ws: WebSocket):
    async def send(results):
        try:
            await ws.send_text(json.dumps({"type": "yt_results", "results": [
                {"url": r["url"], "title": r["title"], "uploader": r.get("uploader", ""),
                 "duration": r.get("duration", 0)}
                for r in results[:8]
            ]}))
        except Exception:
            pass

    songs, videos = await search._songs_and_videos(query, 4, 8)
    if songs and all(r.get("duration") and r.get("artist") for r in songs):
        songs = [r for r in songs if not search._is_unwanted_result(r) and not search.LIVE_RE.search(r.get("title", ""))]
        await send(search._merge_songs_first(songs, videos))
        return
    await send(search._merge_songs_first(songs, videos))
    if songs:
        await search._ytm_fill_details(songs)
        songs = [r for r in songs if not search._is_unwanted_result(r) and not search.LIVE_RE.search(r.get("title", ""))]
        await send(search._merge_songs_first(songs, videos))

async def _remote_dl_and_queue(url: str, title: str, ws: WebSocket, as_next: bool = False):
    try:
        path = await download.run_download(url)
        if path and os.path.exists(path):
            lib = _state.get("library", [])
            td = next((x for x in lib if x.get("path") == path), None)
            entry = {
                "path": path,
                "title": (td.get("title") if td else None) or title or Path(path).stem,
                "duration_sec": (td.get("duration_sec") if td else 0) or 0,
                "lufs": (td.get("lufs") if td else -99.0) or -99.0,
                "bpm": (td.get("bpm") if td else 0) or 0,
                "bitrate_kbps": (td.get("bitrate_kbps") if td else 0) or 0,
                "played": False,
            }
            if not library._queue_is_duplicate(path, entry["title"]):
                if as_next:
                    ci = _state.get("current_idx", -1)
                    _state["queue"].insert(ci + 1, entry)
                else:
                    _state["queue"].append(entry)
                store.save_queue()
                await core.push_queue()
            status = "done"
        else:
            status = "error"
    except Exception:
        status = "error"
    try:
        await ws.send_text(json.dumps({"type": "yt_dl_status", "title": title, "status": status}))
    except Exception:
        pass

def _remote_state_payload() -> str:
    q = _state.get("queue", [])
    ci = _state.get("current_idx", -1)
    nxt = q[ci + 1] if 0 <= ci + 1 < len(q) else None
    # Tonart und BPM kommen aus der Bibliothek — Queue-Eintraege tragen sie nicht
    lib = {lt.get("path"): lt for lt in _state.get("library", [])}
    def _q_item(i, t):
        lt = lib.get(t.get("path")) or {}
        key = lt.get("key", "") or ""
        prev_key = (lib.get(q[i - 1].get("path")) or {}).get("key", "") if i > 0 else ""
        return {"title": t.get("title", ""), "artist": t.get("artist", "") or lt.get("artist", ""),
                "duration_sec": t.get("duration_sec", 0),
                "played": t.get("played", False),
                "path": t.get("path", ""),
                "key": key, "key_src": lt.get("key_src", ""),
                "bpm": lt.get("bpm", 0) or t.get("bpm", 0),
                # 3 gleich, 2 passt, 0 passt nicht, -1 unbekannt (wie in der App)
                "compat": keys._key_compat(prev_key, key) if key and prev_key else -1}
    return json.dumps({
        "type":        "state",
        "playing":     _state.get("playing", False),
        "current_idx": ci,
        "volume":      _state.get("volume", 80),
        "position_ms": _state.get("position_ms", 0),
        "duration_ms": _state.get("duration_ms", 0),
        "next_title":  nxt.get("title", "") if nxt else "",
        "next_artist": nxt.get("artist", "") if nxt else "",
        "normalize_volume": _state.get("normalize_volume", True),
        "target_lufs":      _state.get("target_lufs", -10.0),
        "queue": [_q_item(i, t) for i, t in enumerate(q)],
        "auto_mix":      _state.get("auto_mix", True),
        "radio_enabled": _state.get("radio_enabled", False),
        "radio_ok":      bool(_state.get("lastfm_api_key", "").strip()),
        # Ohne Pfade — die gehen nur die App etwas an
        "wishes": [{"id": w.get("id"), "title": w.get("title", ""), "count": w.get("count", 1),
                    "status": w.get("status", ""), "error": w.get("error", "")}
                   for w in _state.get("wishes", [])],
    })

async def _send_remote_state(ws: WebSocket):
    await ws.send_text(_remote_state_payload())

async def _broadcast_remote_state():
    if not _remote_clients:
        return
    payload = _remote_state_payload()
    dead = set()
    for ws in list(_remote_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _remote_clients.difference_update(dead)

async def _broadcast_remote_pos():
    if not _remote_clients:
        return
    payload = json.dumps({
        "type": "pos",
        "pos": _state.get("position_ms", 0),
        "dur": _state.get("duration_ms", 0),
    })
    dead = set()
    for ws in list(_remote_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _remote_clients.difference_update(dead)

async def _start_remote_server(requester: WebSocket):
    global _remote_server
    if _remote_server is not None:
        await core.broadcast(_remote_status_msg())
        return
    try:
        ip = _get_local_ip()
        config = uvicorn.Config(remote_app, host="0.0.0.0", port=_remote_port, log_level="warning")
        server = uvicorn.Server(config)
        _remote_server = server

        async def _serve_task():
            global _remote_server
            try:
                await server.serve()
            except OSError as e:
                print(f"[remote] Port {_remote_port} nicht verfügbar: {e}", flush=True)
            except Exception as e:
                print(f"[remote] serve Fehler: {e}", flush=True)
            finally:
                if _remote_server is server:
                    _remote_server = None
                    try:
                        await core.broadcast(_remote_status_msg())
                    except Exception:
                        pass

        asyncio.create_task(_serve_task())
        # Kurz warten bis uvicorn den Port gebunden hat
        await asyncio.sleep(0.3)
        if _remote_server is None:
            # Startup fehlgeschlagen (z.B. Port belegt)
            try:
                await requester.send_text(json.dumps(_remote_status_msg(error=f"Port {_remote_port} nicht verfügbar")))
            except Exception:
                pass
            return
        print(f"[remote] gestartet auf http://{ip}:{_remote_port}", flush=True)
        await core.broadcast(_remote_status_msg())
    except Exception as e:
        print(f"[remote] Fehler beim Starten: {e}", flush=True)
        _remote_server = None
        try:
            await requester.send_text(json.dumps(_remote_status_msg(error=str(e))))
        except Exception:
            pass

async def _stop_remote_server():
    global _remote_server
    if _remote_server:
        _remote_server.should_exit = True
        _remote_server = None
        print("[remote] gestoppt", flush=True)

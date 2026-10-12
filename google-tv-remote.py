#!/usr/bin/env python3
"""
TCL Google TV - One Script for MacBook (Premium UI - Trackpad, No Netflix, Bigger Controls, Auto-select, Last IP)
Install: pip install androidtvremote2 flask flask-cors zeroconf
Run: python tcl_one_mac_fixed.py
"""
import asyncio, os, socket, time, sys, traceback, threading, queue, subprocess, re

CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"
CLIENT_NAME = "iPhone Remote"
PORT = 8000
LAST_IP_FILE = "tcl_last_ip.txt"

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "YOUR_MAC_IP"

def load_last_ip():
    try:
        if os.path.exists(LAST_IP_FILE):
            with open(LAST_IP_FILE, "r") as f:
                ip = f.read().strip()
                if len(ip) >= 7 and "." in ip:
                    return ip
    except:
        pass
    return None

def save_last_ip(ip):
    try:
        with open(LAST_IP_FILE, "w") as f:
            f.write(ip.strip())
        print(f"💾 Saved IP {ip} to {LAST_IP_FILE}")
    except Exception as e:
        print(f"Failed to save IP: {e}")

def is_tv_reachable(ip, timeout=2):
    for port in [6466, 6467, 8008, 8009]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            result = s.connect_ex((ip, port))
            s.close()
            if result == 0:
                return True
        except:
            pass
    try:
        subprocess.run(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        return True
    except:
        pass
    return False

def discover_tv():
    try:
        from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
        import socket as sock
        class Listener(ServiceListener):
            def __init__(self):
                self.tvs = []
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name)
                if info and info.addresses:
                    ip = sock.inet_ntoa(info.addresses[0])
                    self.tvs.append((name, ip))
        print("🔍 Scanning for TCL Google TVs (10s)... Same WiFi required")
        zc = Zeroconf()
        listener = Listener()
        browser = ServiceBrowser(zc, "_androidtvremote2._tcp.local.", listener)
        time.sleep(10)
        zc.close()
        return listener.tvs
    except ImportError:
        print("zeroconf not installed, skipping auto-discovery")
        return []
    except Exception as e:
        print(f"Discovery failed: {e}")
        return []

async def pair_tv(tv_ip):
    from androidtvremote2 import AndroidTVRemote
    remote = AndroidTVRemote(CLIENT_NAME, CERT_FILE, KEY_FILE, tv_ip)
    if not os.path.exists(CERT_FILE):
        print("🔑 Generating certs...")
        await remote.async_generate_cert_if_missing()
    else:
        print(f"🔑 Using existing certs {CERT_FILE}")

    print(f"📡 Connecting to {tv_ip}...")
    try:
        await remote.async_connect()
        print("✅ Already paired - connection works!")
        try:
            remote.send_key_command("HOME")
        except AttributeError:
            await remote.async_send_keycode("HOME")
        print("🏠 Sent HOME test")
        return remote
    except Exception as e:
        print(f"Need to pair: {e}")

    print("\n📺 >>> CHECK YOUR TCL TV NOW - PIN should appear <<<")
    try:
        await remote.async_start_pairing()
    except Exception as e:
        print(f"Failed to start pairing: {e}")
        traceback.print_exc()
        sys.exit(1)

    pin = input("\nEnter PIN shown on TV: ").strip()
    if not pin:
        print("No PIN")
        sys.exit(1)

    try:
        await remote.async_finish_pairing(pin)
        print("✅ Paired!")
        await remote.async_connect()
        try:
            remote.send_key_command("HOME")
        except AttributeError:
            await remote.async_send_keycode("HOME")
        print("🏠 Sent HOME")
        return remote
    except Exception as e:
        print(f"Pair failed: {e}")
        traceback.print_exc()
        if os.path.exists(CERT_FILE):
            os.remove(CERT_FILE)
        if os.path.exists(KEY_FILE):
            os.remove(KEY_FILE)
        sys.exit(1)

def get_mac_address(ip):
    try:
        try:
            subprocess.run(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        except:
            pass
        for cmd in [["arp", "-n", ip], ["arp", ip]]:
            try:
                out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=2).decode(errors="ignore")
                m = re.search(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", out)
                if m:
                    return m.group(0).replace("-", ":")
            except:
                continue
    except Exception as e:
        print(f"MAC lookup failed: {e}")
    return None


def has_live_connection(remote_obj):
    """Check if remote has live transport, not just stale is_on flag"""
    try:
        # Try multiple possible attribute names for different library versions
        proto = getattr(remote_obj, '_remote_message_protocol', None)
        if not proto:
            proto = getattr(remote_obj, '_remote_protocol', None)
        if not proto:
            proto = getattr(remote_obj, '_protocol', None)
        if proto:
            transport = getattr(proto, 'transport', None)
            if transport is None:
                return False
            # Check if closing
            try:
                if transport.is_closing():
                    return False
            except:
                pass
            return True
        # Fallback: check _transport directly
        transport = getattr(remote_obj, '_transport', None)
        if transport:
            try:
                return not transport.is_closing()
            except:
                return True
        # Fallback to is_on if nothing else, but treat as not live if False
        is_on = getattr(remote_obj, 'is_on', None)
        if is_on is False:
            return False
        # If we can't determine, assume not live to force reconnect
        return False
    except Exception as e:
        # On any error, assume not live
        return False

def create_remote_instance(ip):
    """Create new remote instance with same certs"""
    from androidtvremote2 import AndroidTVRemote
    return AndroidTVRemote(CLIENT_NAME, CERT_FILE, KEY_FILE, ip)


def send_wol(mac, ip_hint=None):
    if not mac:
        print("No MAC for WOL, skipping")
        return False
    try:
        mac_clean = mac.replace(":", "").replace("-", "")
        if len(mac_clean) != 12:
            print(f"Invalid MAC {mac}")
            return False
        data = bytes.fromhex("FF"*6 + mac_clean*16)
        broadcasts = ["255.255.255.255", "192.168.86.255", "192.168.1.255", "192.168.0.255"]
        try:
            local_ip = get_local_ip()
            if "." in local_ip:
                parts = local_ip.split(".")
                broadcasts.append(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
            if ip_hint and "." in ip_hint:
                parts = ip_hint.split(".")
                broadcasts.append(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
        except:
            pass
        sent = 0
        for bcast in set(broadcasts):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(data, (bcast, 9))
                sock.sendto(data, (bcast, 7))
                sock.close()
                sent += 1
            except Exception as e:
                print(f"WOL to {bcast} failed: {e}")
        print(f"📡 Sent WOL to {mac} via {sent} broadcast(s)")
        return sent>0
    except Exception as e:
        print(f"WOL failed: {e}")
        traceback.print_exc()
        return False

PREMIUM_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>TCL Remote</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>
* { font-family: 'Inter', -apple-system, sans-serif; -webkit-tap-highlight-color: transparent; box-sizing: border-box; }
body { background: #0a0a0a; color: white; margin: 0; min-height: 100vh; display: flex; justify-content: center; align-items: flex-start; padding: 16px; -webkit-touch-callout: none; -webkit-user-select: none; user-select: none; }
.remote-shell { width: 100%; max-width: 390px; }
.status { display: flex; justify-content: space-between; align-items: center; padding: 12px 4px; font-size: 12px; opacity: .7; }
.status-dot { width: 8px; height: 8px; background: #30d158; border-radius: 50%; display: inline-block; margin-right: 6px; box-shadow: 0 0 8px #30d158; }
.remote { background: linear-gradient(180deg, #1c1c1e 0%, #151517 100%); border-radius: 44px; padding: 28px 22px; box-shadow: 0 30px 80px rgba(0,0,0,.9), inset 0 1px 0 rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.06); }
.top-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; }
.power { width: 56px; height: 56px; border-radius: 50%; background: radial-gradient(circle at 30% 30%, #ff6b6b, #ff3b30); border: 0; color: white; font-size: 20px; box-shadow: 0 4px 20px rgba(255,59,48,.5), inset 0 1px 0 rgba(255,255,255,.3); cursor: pointer; transition: transform 0.08s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.08s; touch-action: manipulation; }
.power:active, .power.pressed { transform: scale(0.82) !important; filter: brightness(0.85); box-shadow: inset 0 4px 12px rgba(0,0,0,.4), 0 2px 8px rgba(255,59,48,.3); }
.pill-group { display: flex; gap: 12px; }
.pill { background: #2c2c2e; border: 1px solid rgba(255,255,255,.08); color: white; border-radius: 24px; padding: 16px 24px; font-size: 15px; font-weight: 700; cursor: pointer; min-width: 88px; box-shadow: inset 0 1px 0 rgba(255,255,255,.08); transition: transform 0.08s cubic-bezier(0.34,1.56,0.64,1), background 0.08s, box-shadow 0.08s; touch-action: manipulation; }
.pill:active, .pill.pressed { background: #4a4a4c !important; transform: scale(0.88) !important; box-shadow: inset 0 3px 10px rgba(0,0,0,.5); }
.dpad-wrap { display: flex; justify-content: center; align-items: center; margin: 32px 0 24px 0; width: 100%; }
.dpad { width: 280px; height: 280px; min-width: 280px; background: radial-gradient(circle at 50% 45%, #323236 0%, #2c2c2e 60%, #242428 100%); border-radius: 50%; position: relative; border: 1px solid rgba(255,255,255,.08); box-shadow: inset 0 0 0 1px rgba(255,255,255,.06), inset 0 16px 32px rgba(0,0,0,.6), 0 8px 24px rgba(0,0,0,.4); touch-action: none; user-select: none; cursor: pointer; transition: transform 0.1s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.1s; }
.dpad.pressed { transform: scale(0.97); box-shadow: inset 0 0 0 1px rgba(255,255,255,.08), inset 0 20px 40px rgba(0,0,0,.7), 0 4px 12px rgba(0,0,0,.3); }
.dpad.tracking { box-shadow: inset 0 0 0 2px rgba(10,132,255,.5), inset 0 16px 32px rgba(0,0,0,.6); }
.arrow { position: absolute; color: rgba(255,255,255,.55); font-size: 18px; width: 60px; height: 60px; display: flex; align-items: center; justify-content: center; pointer-events: none; border-radius: 50%; transition: all 0.15s ease; }
.arrow.active { color: white; background: rgba(255,255,255,.18); transform: scale(1.25); box-shadow: 0 0 16px rgba(255,255,255,.2); }
.arrow.up { top: 18px; left: 50%; transform: translateX(-50%); }
.arrow.up.active { transform: translateX(-50%) scale(1.3); }
.arrow.down { bottom: 18px; left: 50%; transform: translateX(-50%); }
.arrow.down.active { transform: translateX(-50%) scale(1.3); }
.arrow.left { left: 18px; top: 50%; transform: translateY(-50%); }
.arrow.left.active { transform: translateY(-50%) scale(1.3); }
.arrow.right { right: 18px; top: 50%; transform: translateY(-50%); }
.arrow.right.active { transform: translateY(-50%) scale(1.3); }
.center-dot { position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); width: 96px; height: 96px; background: white; color: black; border-radius: 50%; font-weight: 800; font-size: 18px; display: flex; align-items: center; justify-content: center; box-shadow: 0 6px 24px rgba(255,255,255,.35); transition: transform 0.1s cubic-bezier(0.34,1.56,0.64,1), background 0.1s; pointer-events: none; }
.center-dot.active { transform: translate(-50%,-50%) scale(0.85); background: #d0d0d0; }
.trackpad-hint { position: absolute; bottom: 72px; left: 50%; transform: translateX(-50%); font-size: 10px; letter-spacing: 1.2px; text-transform: uppercase; color: rgba(255,255,255,.25); pointer-events: none; font-weight: 600; }
.volume-horizontal { display: flex; justify-content: center; align-items: center; gap: 14px; margin: 0 0 22px 0; }
.vol-btn { height: 52px; border-radius: 26px; background: #2c2c2e; border: 1px solid rgba(255,255,255,.08); color: white; font-size: 18px; font-weight: 700; cursor: pointer; box-shadow: inset 0 1px 0 rgba(255,255,255,.08); padding: 0 20px; min-width: 64px; transition: transform 0.08s cubic-bezier(0.34,1.56,0.64,1), background 0.08s; touch-action: manipulation; }
.vol-btn:active, .vol-btn.pressed { background: #4a4a4c !important; transform: scale(0.88) !important; box-shadow: inset 0 3px 10px rgba(0,0,0,.5); }
.action-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
.action { height: 50px; border-radius: 16px; background: #2c2c2e; border: 1px solid rgba(255,255,255,.08); color: white; font-size: 14px; font-weight: 600; cursor: pointer; transition: transform 0.08s cubic-bezier(0.34,1.56,0.64,1), background 0.08s; touch-action: manipulation; }
.action:active, .action.pressed { transform: scale(0.90) !important; background: #4a4a4c !important; box-shadow: inset 0 2px 8px rgba(0,0,0,.4); }
.wide { width: 100%; height: 56px; border-radius: 28px; background: #0a84ff; border: 0; color: white; font-weight: 700; margin-top: 16px; cursor: pointer; font-size: 15px; transition: transform 0.08s cubic-bezier(0.34,1.56,0.64,1), background 0.08s; touch-action: manipulation; }
.wide:active, .wide.pressed { transform: scale(0.94) !important; background: #0066cc !important; box-shadow: inset 0 2px 10px rgba(0,0,0,.3); }
.hint { text-align: center; font-size: 11px; opacity: .3; margin-top: 20px; display: flex; align-items: center; justify-content: center; gap: 8px; }
.line { width: 20px; height: 1px; background: rgba(255,255,255,.15); }

.status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; transition: all 0.3s ease; }
.status-dot.connected { background: #30d158; box-shadow: 0 0 8px #30d158; }
.status-dot.reconnecting { background: #ffcc02; box-shadow: 0 0 12px #ffcc02; animation: pulse 1.2s infinite; }
.status-dot.offline { background: #ff3b30; box-shadow: 0 0 8px #ff3b30; }
@keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.6; transform: scale(1.3); } }
.reconnect-banner { background: linear-gradient(90deg, #ffcc02, #ff9500); color: black; text-align: center; padding: 8px 12px; border-radius: 12px; font-size: 12px; font-weight: 700; margin-bottom: 12px; display: none; align-items: center; justify-content: center; gap: 8px; }
.reconnect-banner.show { display: flex; }
.spinner { width: 14px; height: 14px; border: 2px solid rgba(0,0,0,.2); border-top-color: black; border-radius: 50%; animation: spin 0.8s linear infinite; display: inline-block; }
@keyframes spin { to { transform: rotate(360deg); } }
.status-text { transition: all 0.3s ease; }
.kb-toggle { width: 100%; height: 50px; border-radius: 16px; background: transparent; border: 1px dashed rgba(255,255,255,.18); color: rgba(255,255,255,.75); font-size: 14px; font-weight: 600; margin-top: 12px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 8px; touch-action: manipulation; }
.kb-toggle:active { background: rgba(255,255,255,.06); }
.kb-panel { display: none; margin-top: 12px; background: rgba(0,0,0,.35); border: 1px solid rgba(255,255,255,.1); border-radius: 16px; padding: 12px; }
.kb-panel.open { display: block; }
.kb-inputrow { display: flex; gap: 8px; }
.kb-field { flex: 1; background: #0c0c0d; border: 1px solid rgba(255,255,255,.14); color: #fff; border-radius: 12px; padding: 12px 14px; font-size: 16px; outline: none; min-width: 0; font-family: inherit; }
.kb-field:focus { border-color: #0a84ff; }
.kb-send { background: #0a84ff; border: 0; color: #fff; border-radius: 12px; padding: 0 18px; font-size: 14px; font-weight: 700; cursor: pointer; touch-action: manipulation; }
.kb-send:active { background: #0066cc !important; }
.kb-tools { display: flex; gap: 8px; margin-top: 8px; }
.kb-tool { flex: 1; background: #2c2c2e; border: 1px solid rgba(255,255,255,.08); color: #fff; border-radius: 10px; padding: 9px 0; font-size: 12px; font-weight: 600; cursor: pointer; touch-action: manipulation; }
.kb-tool:active { background: #4a4a4c !important; }
.kb-close { width: 100%; background: transparent; border: 0; color: rgba(255,255,255,.4); font-size: 11px; margin-top: 8px; cursor: pointer; }

#toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(12px) scale(.96); background: rgba(40,40,42,.9); backdrop-filter: blur(20px); color: white; padding: 12px 18px; border-radius: 24px; font-size: 13px; font-weight: 600; opacity: 0; pointer-events: none; transition: all .3s; border: 1px solid rgba(255,255,255,.1); }
#toast.show { opacity: 1; transform: translateX(-50%) translateY(0) scale(1); }
</style>
</head>
<body ontouchstart="">
<div class="remote-shell">
<div class="status"><span><span class="status-dot connected" id="statusDot"></span><span class="status-text" id="statusText">Connected</span> • __TV_IP__</span><span id="ip">__LOCAL_IP__:__PORT__</span></div>
<div class="reconnect-banner" id="reconnectBanner"><span class="spinner"></span><span id="reconnectText">Reconnecting...</span></div>
<div class="remote">
<div class="top-row">
<button class="power" data-key="POWER">⏻</button>
<div class="pill-group">
<button class="pill" data-key="HOME">Home</button>
<button class="pill" data-key="BACK">Back</button>
</div>
</div>

<div class="dpad-wrap">
<div class="dpad" id="trackpad">
<div class="arrow up" id="arrowUp">▲</div>
<div class="arrow down" id="arrowDown">▼</div>
<div class="arrow left" id="arrowLeft">◀</div>
<div class="arrow right" id="arrowRight">▶</div>
<div class="center-dot" id="centerDot">OK</div>
<div class="trackpad-hint">Swipe • Tap</div>
</div>
</div>

<div class="volume-horizontal">
<button class="vol-btn" data-key="VOLUME_DOWN">−</button>
<button class="vol-btn" data-key="MUTE">MUTE</button>
<button class="vol-btn" data-key="VOLUME_UP">+</button>
</div>

<div class="action-row">
<button class="action" data-key="TV_INPUT">Input</button>
<button class="action" data-key="SETTINGS">Settings</button>
</div>
<button class="wide" data-key="MEDIA_PLAY_PAUSE">▶︎  Play / Pause</button>
<button class="kb-toggle" id="kbToggle"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg><span id="kbToggleLabel">Keyboard</span></button>
<div class="kb-panel" id="kbPanel">
<div class="kb-inputrow">
<input class="kb-field" id="kbInput" type="text" placeholder="Type on your phone… 在此输入" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" inputmode="search" enterkeyhint="search" />
<button class="kb-send" id="kbSend">Send</button>
</div>
<div class="kb-tools">
<button class="kb-tool" id="kbBackspace">⌫ Delete</button>
<button class="kb-tool" id="kbEnter">⏎ Search</button>
<button class="kb-tool" id="kbClear">Clear</button>
</div>
<button class="kb-close" id="kbClose">Hide keyboard</button>
</div>
<div class="hint"><div class="line"></div> iPhone • Glassmorphism • Haptics <div class="line"></div></div>
</div>
<p style="text-align:center;font-size:11px;opacity:.25;margin-top:16px">Add to Home Screen: Share → Add to Home Screen</p>
</div>
<div id="toast"></div>
<script>
let audioCtx;
let audioUnlocked = false;
let connectionState = {live: true, reconnecting: false};

function unlockAudio(){
  try{
    if(!audioCtx) audioCtx = new (window.AudioContext||window.webkitAudioContext)();
    if(audioCtx.state==='suspended'){
      audioCtx.resume();
    }
    // iOS silent buffer unlock - must be inside user gesture
    if(!audioUnlocked){
      const buffer = audioCtx.createBuffer(1, 1, 22050);
      const source = audioCtx.createBufferSource();
      source.buffer = buffer;
      source.connect(audioCtx.destination);
      source.start(0);
      if(audioCtx.state === 'running') audioUnlocked = true;
      else {
        // If still suspended, mark unlocked on next state change
        audioCtx.onstatechange = ()=>{ if(audioCtx.state==='running') audioUnlocked = true; };
      }
    }
    audioUnlocked = true;
  }catch(e){}
}

function ensureAudio(){
  try{
    if(!audioCtx){
      audioCtx = new (window.AudioContext||window.webkitAudioContext)();
    }
    if(audioCtx.state==='suspended'){
      // Try resume synchronously inside gesture
      audioCtx.resume().then(()=>{ audioUnlocked = true; }).catch(()=>{});
    }
    if(audioCtx.state==='running') audioUnlocked = true;
  }catch(e){}
}

function playClick(type){
  try{
    // Critical fix for first swipe: unlock BEFORE any preventDefault, and ensure running
    if(!audioCtx){
      unlockAudio();
    }
    if(audioCtx){
      if(audioCtx.state==='suspended'){
        // iOS needs resume inside same gesture - try immediate
        const p = audioCtx.resume();
        if(p && p.then){
          p.then(()=>{ 
            try{
              const now = audioCtx.currentTime;
              const o2 = audioCtx.createOscillator();
              const g2 = audioCtx.createGain();
              o2.type = 'square';
              o2.frequency.value = type==='POWER'?320:type==='DPAD_CENTER'?1100:type.includes('VOLUME')?650:type==='MUTE'?500:type==='HOME'||type==='BACK'?750:type==='RECONNECT'?400:900;
              g2.gain.setValueAtTime(0.16, now);
              g2.gain.exponentialRampToValueAtTime(0.001, now+0.09);
              o2.connect(g2); g2.connect(audioCtx.destination);
              o2.start(now); o2.stop(now+0.09);
            }catch(e2){}
          });
          // Also try immediate play in case iOS 17+ allows it
        }
      }
    }
    if(!audioCtx) return;
    const now = audioCtx.currentTime;
    const o = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    o.type = 'square';
    if(type==='POWER') o.frequency.value = 320;
    else if(type==='DPAD_CENTER') o.frequency.value = 1100;
    else if(type.includes('VOLUME')) o.frequency.value = 650;
    else if(type==='MUTE') o.frequency.value = 500;
    else if(type==='HOME' || type==='BACK') o.frequency.value = 750;
    else if(type==='RECONNECT') o.frequency.value = 400;
    else o.frequency.value = 900;
    g.gain.setValueAtTime(0.16, now);
    g.gain.exponentialRampToValueAtTime(0.001, now+0.09);
    o.connect(g); g.connect(audioCtx.destination);
    try{
      o.start(now); 
      o.stop(now+0.09);
    }catch(e){
      // If start fails because still suspended, try after resume
      if(audioCtx.state==='suspended'){
        audioCtx.resume().then(()=>{
          try{
            const o2 = audioCtx.createOscillator();
            const g2 = audioCtx.createGain();
            o2.type = 'square';
            o2.frequency.value = o.frequency.value;
            const now2 = audioCtx.currentTime;
            g2.gain.setValueAtTime(0.16, now2);
            g2.gain.exponentialRampToValueAtTime(0.001, now2+0.09);
            o2.connect(g2); g2.connect(audioCtx.destination);
            o2.start(now2); o2.stop(now2+0.09);
          }catch(e2){}
        });
      }
    }
  }catch(e){}
}
function haptic(){ if(navigator.vibrate) navigator.vibrate(10); }

// Global iOS unlock - capture first interaction anywhere, before trackpad preventDefault
function setupAudioUnlock(){
  const opts = {once:true, passive:true, capture:true};
  const unlockOnce = ()=>{
    unlockAudio();
    // Remove other listeners after first unlock
    document.removeEventListener('touchstart', unlockOnce, true);
    document.removeEventListener('touchend', unlockOnce, true);
    document.removeEventListener('click', unlockOnce, true);
  };
  document.addEventListener('touchstart', unlockOnce, {once:true, capture:true, passive:true});
  document.addEventListener('touchend', unlockOnce, {once:true, capture:true, passive:true});
  document.addEventListener('click', unlockOnce, {once:true, capture:true});
  // Also listen on body for PWA launch
  document.body.addEventListener('touchstart', unlockAudio, {once:true, passive:true});
}
function toast(m, duration=1800){
  let t=document.getElementById('toast');
  t.innerText=m;
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),duration);
}
function updateStatusUI(live, reconnecting, error){
  const dot = document.getElementById('statusDot');
  const txt = document.getElementById('statusText');
  const banner = document.getElementById('reconnectBanner');
  const bannerText = document.getElementById('reconnectText');
  if(!dot || !txt) return;
  connectionState = {live, reconnecting};
  dot.classList.remove('connected','reconnecting','offline');
  if(reconnecting){
    dot.classList.add('reconnecting');
    txt.innerText = 'Reconnecting...';
    if(banner){ banner.classList.add('show'); bannerText.innerText = error ? error : 'Reconnecting to TV...'; }
  } else if(live){
    dot.classList.add('connected');
    txt.innerText = 'Connected';
    if(banner) banner.classList.remove('show');
  } else {
    dot.classList.add('offline');
    txt.innerText = 'Offline';
    if(banner){ banner.classList.add('show'); bannerText.innerText = error || 'TV offline - tap Power to wake'; }
  }
}
async function fetchStatus(){
  try{
    let r = await fetch('/status', {cache:'no-store'});
    let j = await r.json();
    updateStatusUI(j.live, j.reconnecting, j.error);
    return j;
  }catch(e){
    // If server unreachable, show offline
    updateStatusUI(false, false, 'Server unreachable');
    return {live:false, reconnecting:false};
  }
}

function addPressVisuals(){
  // SMOOTH: Immediate pointer events, no 300ms delay, no blocking
  document.querySelectorAll('button[data-key]').forEach(btn=>{
    if(btn.closest('#trackpad')) return;
    let downTime = 0;
    const onDown = (e)=>{
      if(e.cancelable) e.preventDefault();
      unlockAudio();
      btn.classList.add('pressed');
      downTime = Date.now();
      const k = btn.dataset.key;
      if(k && !k.startsWith('DPAD_')){
        playClick(k);
        haptic();
      }
    };
    const onUp = (e)=>{
      if(e.cancelable) e.preventDefault();
      btn.classList.remove('pressed');
      if(Date.now() - downTime < 1000){
        const k = btn.dataset.key;
        if(k) press(k, 0, true);
      }
      setTimeout(()=>btn.classList.remove('pressed'), 100);
    };
    btn.addEventListener('pointerdown', onDown, {passive:false});
    btn.addEventListener('pointerup', onUp, {passive:false});
    btn.addEventListener('pointercancel', ()=>btn.classList.remove('pressed'), {passive:true});
    btn.addEventListener('pointerleave', ()=>btn.classList.remove('pressed'), {passive:true});
    btn.addEventListener('click', e=>e.preventDefault(), {passive:false});
  });
}
function initTrackpad(){
  const pad = document.getElementById('trackpad');
  if(!pad) return;
  const centerDot = document.getElementById('centerDot');
  const arrows = {
    up: document.getElementById('arrowUp'),
    down: document.getElementById('arrowDown'),
    left: document.getElementById('arrowLeft'),
    right: document.getElementById('arrowRight')
  };
  let startX=0, startY=0, startTime=0, isTracking=false;
  let lastSwipeDir=null;
  let swipeSoundPlayed=false;

  function highlight(dir){
    Object.values(arrows).forEach(a=>a.classList.remove('active'));
    if(centerDot) centerDot.classList.remove('active');
    if(dir==='center' && centerDot){
      centerDot.classList.add('active');
      setTimeout(()=>centerDot.classList.remove('active'), 250);
    } else if(arrows[dir]){
      arrows[dir].classList.add('active');
      setTimeout(()=>arrows[dir].classList.remove('active'), 250);
    }
  }

  function handleStart(x,y){
    unlockAudio(); // critical: must be first, before any preventDefault
    startX=x; startY=y; startTime=Date.now(); isTracking=true;
    lastSwipeDir=null;
    swipeSoundPlayed=false;
    pad.classList.add('pressed');
    pad.classList.remove('tracking');
  }
  function handleMove(x,y){
    if(!isTracking) return;
    const dx=x-startX, dy=y-startY;
    const dist=Math.hypot(dx,dy);
    if(dist>12){
      pad.classList.add('tracking');
      // live preview of direction + SOUND ON FIRST DIRECTION LOCK (fix for first swipe)
      Object.values(arrows).forEach(a=>a.classList.remove('active'));
      let dir=null;
      if(Math.abs(dx)>Math.abs(dy)){
        if(dx>10) { arrows.right.classList.add('active'); dir='right'; }
        else if(dx<-10) { arrows.left.classList.add('active'); dir='left'; }
      } else {
        if(dy>10) { arrows.down.classList.add('active'); dir='down'; }
        else if(dy<-10) { arrows.up.classList.add('active'); dir='up'; }
      }
      // Play sound as soon as direction locks - this is inside touchmove which is still valid gesture
      if(dir && dir!==lastSwipeDir && !swipeSoundPlayed){
        lastSwipeDir=dir;
        // Only play once per swipe gesture to avoid spamming
        if(dist>18){
          if(dir==='right'){ playClick('DPAD_RIGHT'); haptic(); }
          else if(dir==='left'){ playClick('DPAD_LEFT'); haptic(); }
          else if(dir==='down'){ playClick('DPAD_DOWN'); haptic(); }
          else if(dir==='up'){ playClick('DPAD_UP'); haptic(); }
          swipeSoundPlayed=true;
        }
      }
    }
  }
  function handleEnd(x,y){
    if(!isTracking) return;
    isTracking=false;
    pad.classList.remove('pressed','tracking');
    Object.values(arrows).forEach(a=>a.classList.remove('active'));
    const dx=x-startX, dy=y-startY;
    const dist=Math.hypot(dx,dy);
    const dt=Date.now()-startTime;
    // Tap = OK - if swipe sound already played, don't play again
    if(dist<22 && dt<350){
      if(!swipeSoundPlayed){
        highlight('center');
        playClick('DPAD_CENTER');
        haptic();
      }
      press('DPAD_CENTER');
      return;
    }
    // Swipe - if sound already played in handleMove, just press, else play now
    if(Math.abs(dx)>Math.abs(dy)){
      if(dx>32){ 
        highlight('right'); 
        if(!swipeSoundPlayed){ playClick('DPAD_RIGHT'); haptic(); }
        press('DPAD_RIGHT'); 
      }
      else if(dx<-32){ 
        highlight('left'); 
        if(!swipeSoundPlayed){ playClick('DPAD_LEFT'); haptic(); }
        press('DPAD_LEFT'); 
      }
    } else {
      if(dy>32){ 
        highlight('down'); 
        if(!swipeSoundPlayed){ playClick('DPAD_DOWN'); haptic(); }
        press('DPAD_DOWN'); 
      }
      else if(dy<-32){ 
        highlight('up'); 
        if(!swipeSoundPlayed){ playClick('DPAD_UP'); haptic(); }
        press('DPAD_UP'); 
      }
    }
  }

  // Touch - FIX: don't preventDefault on touchstart/end, only on move, and unlock BEFORE preventDefault
  pad.addEventListener('touchstart', e=>{
    unlockAudio(); // unlock first
    const t=e.touches[0];
    handleStart(t.clientX,t.clientY);
    // No preventDefault here - keep user activation valid for audio
  }, {passive:true});
  pad.addEventListener('touchmove', e=>{
    // Only preventDefault for move to stop scrolling, after unlock
    e.preventDefault();
    const t=e.touches[0];
    handleMove(t.clientX,t.clientY);
  }, {passive:false});
  pad.addEventListener('touchend', e=>{
    // No preventDefault on end - keeps audio gesture valid
    const t=e.changedTouches[0];
    handleEnd(t.clientX,t.clientY);
  }, {passive:true});
  // Mouse for desktop testing
  let mouseDown=false;
  pad.addEventListener('mousedown', e=>{
    mouseDown=true;
    handleStart(e.clientX,e.clientY);
  });
  pad.addEventListener('mousemove', e=>{
    if(!mouseDown) return;
    handleMove(e.clientX,e.clientY);
  });
  pad.addEventListener('mouseup', e=>{
    if(!mouseDown) return;
    mouseDown=false;
    handleEnd(e.clientX,e.clientY);
  });
  pad.addEventListener('mouseleave', e=>{
    if(mouseDown){ mouseDown=false; pad.classList.remove('pressed','tracking'); Object.values(arrows).forEach(a=>a.classList.remove('active')); }
  });
}
function initKeyboard(){
  const panel = document.getElementById('kbPanel');
  const input = document.getElementById('kbInput');
  const toggleLabel = document.getElementById('kbToggleLabel');
  if(!panel || !input || !toggleLabel) return;
  let deb = null;
  // The TV-side IME op is INSERT-at-cursor (commitText), not replace-field:
  // sending the whole field per keystroke duplicates text on the TV.
  // So we send only the delta vs what we last sent: DELs for removed chars,
  // then the newly added suffix. The server applies DELs before text in one call.
  let lastSent = "";
  function sendDelta(newVal){
    let i = 0;
    const n = Math.min(lastSent.length, newVal.length);
    while(i < n && lastSent[i] === newVal[i]) i++;
    const dels = lastSent.length - i;
    const ins = newVal.slice(i);
    lastSent = newVal;
    if(!dels && !ins) return;
    fetch('/type?text='+encodeURIComponent(ins)+'&dels='+dels+'&full='+encodeURIComponent(newVal), {cache:'no-store'}).catch(()=>{});
    playClick('KEYBOARD');
    haptic();
  }
  function scheduleDelta(){
    clearTimeout(deb);
    deb = setTimeout(()=>sendDelta(input.value), 250); // read fresh at fire time
  }
  function submitSearch(){
    clearTimeout(deb);
    sendDelta(input.value);
    setTimeout(()=>{ press('ENTER'); }, 600);
    toast('Searching\u2026');
  }
  document.getElementById('kbToggle').addEventListener('click', ()=>{
    const opening = !panel.classList.contains('open');
    panel.classList.toggle('open', opening);
    toggleLabel.textContent = opening ? 'Keyboard \u2014 hide' : 'Keyboard';
    // iOS PWA: focus() must run synchronously inside this tap, after the panel
    // is visible — any async gap and iOS silently refuses to open the keyboard.
    if(opening) input.focus();
    playClick('KEYBOARD');
    haptic();
  });
  input.addEventListener('input', (e)=>{
    // CJK IME (iOS Pinyin etc.): input events during composition carry
    // uncommitted pinyin — never send those. The committed text arrives in a
    // final input event that is NOT composing. Trust the event's own
    // isComposing over the flag, so a missed compositionend can never
    // wedge all future typing.
    const composingNow = (e && e.isComposing !== undefined) ? e.isComposing : isComposing;
    if(composingNow) return;
    isComposing = false;
    scheduleDelta();
  });
  // CJK IME: compositionstart/end bracket the pinyin composition.
  // compositionend is only a fast path — send after a short settle delay,
  // because iOS updates input.value a beat after the event fires.
  let isComposing = false;
  input.addEventListener('compositionstart', ()=>{ isComposing = true; clearTimeout(deb); });
  input.addEventListener('compositionend', ()=>{
    isComposing = false;
    clearTimeout(deb);
    deb = setTimeout(()=>sendDelta(input.value), 60);
  });
  input.addEventListener('keydown', (e)=>{ if(e.key==='Enter'){ e.preventDefault(); submitSearch(); } });
  document.getElementById('kbSend').addEventListener('click', ()=>{ clearTimeout(deb); sendDelta(input.value); });
  document.getElementById('kbEnter').addEventListener('click', ()=>{ submitSearch(); });
  document.getElementById('kbBackspace').addEventListener('click', ()=>{
    if(input.value.length) input.value = input.value.slice(0,-1);
    clearTimeout(deb);
    sendDelta(input.value);
  });
  document.getElementById('kbClear').addEventListener('click', ()=>{
    input.value = '';
    clearTimeout(deb);
    sendDelta('');
  });
  document.getElementById('kbClose').addEventListener('click', ()=>{
    isComposing = false; // in case compositionend never fires on blur
    panel.classList.remove('open'); toggleLabel.textContent='Keyboard'; input.blur();
  });
}
document.addEventListener('DOMContentLoaded', ()=>{ setupAudioUnlock(); addPressVisuals(); initTrackpad(); initKeyboard(); fetchStatus(); unlockAudio(); });
let pressQueue = [];
let isProcessing = false;
let lastFire = 0;
async function press(k, retryCount=0, fromPointer=false){
  unlockAudio();
  const now = Date.now();
  // Dedupe ghost click within 40ms
  if(retryCount===0 && now - lastFire < 40) return;
  if(retryCount===0) lastFire = now;
  if(!fromPointer && !k.startsWith('DPAD_')){ playClick(k); haptic(); }
  else if(fromPointer){ haptic(); }
  pressQueue.push({k, retryCount, t: now});
  if(!isProcessing) processQueue();
}

async function processQueue(){
  if(pressQueue.length===0){ isProcessing = false; return; }
  isProcessing = true;
  const {k, retryCount} = pressQueue.shift();
  try{
    let r = await fetch('/press?key='+k, {cache:'no-store'});
    let j = await r.json();
    if(j.status==='ok'){
      updateStatusUI(true, false, null);
      if(pressQueue.length===0) toast(j.key+' ✓', 600);
      setTimeout(fetchStatus, 200);
    } else if(j.status==='reconnecting' && retryCount < 2){
      pressQueue.unshift({k, retryCount: retryCount+1, t: Date.now()});
      setTimeout(()=>processQueue(), 500);
      return;
    } else if(j.status==='offline' && j.reconnecting && retryCount < 2){
      pressQueue.unshift({k, retryCount: retryCount+1, t: Date.now()});
      setTimeout(()=>processQueue(), 700);
      return;
    }
  }catch(e){
    updateStatusUI(false, false, e.message);
  } finally {
    setTimeout(()=>processQueue(), 70);
  }
}
// Poll status every 5s
setInterval(fetchStatus, 5000);
</script>
</body>
</html>
"""

def main():
    print("=== TCL Google TV - MacBook One Script (Premium UI) ===\n")

    tv_ip = None
    tvs = []
    last_ip = load_last_ip()

    def input_with_timeout(prompt, timeout=3):
        print(prompt, end='', flush=True)
        q = queue.Queue()
        def _get():
            try:
                q.put(input())
            except:
                q.put("")
        t = threading.Thread(target=_get, daemon=True)
        t.start()
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return ""

    if last_ip:
        print(f"💾 Found saved IP {last_ip} from {LAST_IP_FILE}")
        print(f"🔍 Trying saved IP first (2s check)...")
        if is_tv_reachable(last_ip, timeout=2):
            print(f"✅ Saved IP {last_ip} reachable, skipping scan")
            tv_ip = last_ip
        else:
            print(f"⚠️ Saved IP {last_ip} not reachable, will re-scan")
            mac = get_mac_address(last_ip)
            if mac:
                print(f"📡 Trying WOL for saved IP {last_ip} MAC {mac}")
                send_wol(mac, last_ip)
                time.sleep(2)
                if is_tv_reachable(last_ip, timeout=3):
                    print(f"✅ Saved IP {last_ip} now reachable after WOL")
                    tv_ip = last_ip

    if not tv_ip:
        tvs = discover_tv()
        if tvs:
            print(f"\nFound {len(tvs)} TV(s):")
            for i, (name, ip) in enumerate(tvs):
                print(f"  {i+1}. {name} -> {ip}")
            sel = input_with_timeout(f"\nSelect (1-{len(tvs)}) or enter IP manually [1] (auto-selecting 1 in 3s): ", timeout=3).strip()
            if not sel:
                print(f"\n⏱️ No input in 3s, auto-selecting first TV: {tvs[0][1]}")
                tv_ip = tvs[0][1]
            elif sel.isdigit() and 1 <= int(sel) <= len(tvs):
                tv_ip = tvs[int(sel)-1][1]
            elif "." in sel:
                tv_ip = sel
            else:
                tv_ip = tvs[0][1]
        else:
            print("\nNo TV auto-found.")
            print("Find IP on TCL: Settings > Network & Internet > Your WiFi > IP address")
            tv_ip = input("Enter TCL IP (e.g. 192.168.86.43): ").strip()

    if not tv_ip or len(tv_ip) < 7:
        print("Invalid IP")
        sys.exit(1)

    tv_mac = get_mac_address(tv_ip)
    if tv_mac:
        print(f"📋 TV MAC for WOL: {tv_mac}")
    else:
        print(f"⚠️ Could not get MAC for {tv_ip}, WOL will be skipped (enable 'Keep network on' on TV)")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        remote = loop.run_until_complete(pair_tv(tv_ip))
        save_last_ip(tv_ip)
    except SystemExit:
        if last_ip and tv_ip == last_ip:
            print(f"\n❌ Saved IP {last_ip} failed to pair/connect")
            print("🔄 Falling back to discovery...")
            try:
                loop.close()
            except:
                pass
            tvs = discover_tv()
            if tvs:
                print(f"\nFound {len(tvs)} TV(s):")
                for i, (name, ip) in enumerate(tvs):
                    print(f"  {i+1}. {name} -> {ip}")
                sel = input_with_timeout(f"\nSelect (1-{len(tvs)}) or enter IP manually [1] (auto-selecting 1 in 3s): ", timeout=3).strip()
                if not sel:
                    print(f"\n⏱️ No input in 3s, auto-selecting first TV: {tvs[0][1]}")
                    tv_ip = tvs[0][1]
                elif sel.isdigit() and 1 <= int(sel) <= len(tvs):
                    tv_ip = tvs[int(sel)-1][1]
                elif "." in sel:
                    tv_ip = sel
                else:
                    tv_ip = tvs[0][1]
            else:
                print("\nNo TV auto-found after fallback.")
                tv_ip = input("Enter TCL IP: ").strip()
            if not tv_ip or len(tv_ip) < 7:
                sys.exit(1)
            tv_mac = get_mac_address(tv_ip)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            remote = loop.run_until_complete(pair_tv(tv_ip))
            save_last_ip(tv_ip)
        else:
            raise
    except Exception as e:
        if last_ip and tv_ip == last_ip:
            print(f"\n❌ Saved IP {last_ip} error: {e}")
            print("🔄 Falling back to discovery...")
            try:
                loop.close()
            except:
                pass
            tvs = discover_tv()
            if tvs:
                print(f"\nFound {len(tvs)} TV(s):")
                for i, (name, ip) in enumerate(tvs):
                    print(f"  {i+1}. {name} -> {ip}")
                sel = input_with_timeout(f"\nSelect (1-{len(tvs)}) or enter IP manually [1] (auto-selecting 1 in 3s): ", timeout=3).strip()
                if not sel:
                    tv_ip = tvs[0][1]
                elif sel.isdigit() and 1 <= int(sel) <= len(tvs):
                    tv_ip = tvs[int(sel)-1][1]
                elif "." in sel:
                    tv_ip = sel
                else:
                    tv_ip = tvs[0][1]
            else:
                tv_ip = input("Enter TCL IP: ").strip()
            if not tv_ip or len(tv_ip) < 7:
                sys.exit(1)
            tv_mac = get_mac_address(tv_ip)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            remote = loop.run_until_complete(pair_tv(tv_ip))
            save_last_ip(tv_ip)
        else:
            raise

    def start_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    threading.Thread(target=start_loop, args=(loop,), daemon=True).start()
    print("🔄 Event loop running in background thread (thread-safe)")

    from flask import Flask, request, jsonify
    from flask_cors import CORS

    app = Flask(__name__)
    CORS(app)

    # SMOOTH TAPPING: Separate locks, non-blocking press
    press_lock = threading.Lock()
    keepalive_lock = threading.Lock()
    state_lock = threading.Lock()
    connection_state = {
        "live": False,
        "reconnecting": False,
        "error": None,
        "last_seen": time.time(),
        "tv_ip": tv_ip,
        "tv_mac": tv_mac,
        "power_cycle_detected": False
    }

    def set_connection_state(live=None, reconnecting=None, error=None, power_cycle=None):
        with state_lock:
            if live is not None:
                connection_state["live"] = live
                if live:
                    connection_state["last_seen"] = time.time()
                    connection_state["power_cycle_detected"] = False
            if reconnecting is not None:
                connection_state["reconnecting"] = reconnecting
            if error is not None:
                connection_state["error"] = error
            if power_cycle is not None:
                connection_state["power_cycle_detected"] = power_cycle
            connection_state["tv_ip"] = tv_ip
            connection_state["tv_mac"] = tv_mac

    def run_async(coro, timeout=5):
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)
    
    def run_async_nowait(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop)

    # --- Live IME field state -------------------------------------------------
    # The TV validates every text batch edit against the LIVE state of the
    # focused editor: ime_counter must equal the editor's own counter (sent on
    # the IME key-inject when a field gains focus), field_counter must be the
    # latest counter from the TV's text-field status (key-inject, plus the IME
    # show-request the TV sends after every edit), and start/end must be the
    # field length after the edit. androidtvremote2's send_text() gets none of
    # these right (it reuses the {1,1} greeting counters and len(text)-1), so
    # surfaces that check them silently drop its edits. We track the live state
    # by observing inbound messages the library otherwise ignores, and build
    # the batch edit ourselves.
    _ime_state = {"ime_counter": None, "field_counter": None, "value": None}
    _ime_hook = {"proto": None}

    def _u16len(text):
        # Android measures field positions in UTF-16 code units.
        return len(text.encode("utf-16-le")) // 2

    def _apply_field_status(tfs):
        if tfs.counter_field:
            _ime_state["field_counter"] = tfs.counter_field
        _ime_state["value"] = tfs.value

    def _track_ime_message(raw):
        from androidtvremote2.remotemessage_pb2 import RemoteMessage
        msg = RemoteMessage()
        msg.ParseFromString(raw)
        changed = False
        if msg.HasField("remote_ime_key_inject"):
            ki = msg.remote_ime_key_inject
            if ki.HasField("app_info") and ki.app_info.counter:
                _ime_state["ime_counter"] = ki.app_info.counter
                changed = True
            if ki.HasField("text_field_status"):
                _apply_field_status(ki.text_field_status)
                changed = True
        if msg.HasField("remote_ime_show_request"):
            _apply_field_status(msg.remote_ime_show_request.remote_text_field_status)
            changed = True
        if changed:
            print(f"[ime] live state: ime_counter={_ime_state['ime_counter']} "
                  f"field_counter={_ime_state['field_counter']} value={_ime_state['value']!r}")

    def _install_ime_hook():
        """Wrap the current protocol's inbound handler to track IME state.

        The protocol object is recreated on every reconnect, so re-wrap when
        the instance changes. Called from the press/type coroutines, which run
        ahead of field interaction (D-pad navigation focuses the field).
        """
        proto = getattr(remote, "_remote_message_protocol", None)
        if proto is None or _ime_hook["proto"] is proto:
            return
        orig = proto._handle_message
        def _hooked(*args):
            try:
                _track_ime_message(args[-1])
            except Exception:
                pass
            return orig(args[-1])
        proto._handle_message = _hooked
        _ime_hook["proto"] = proto
        print("[ime] hook installed on live protocol")

    def _send_ime_text(proto, text, span_end):
        """Send one text batch edit built the way the TV expects (see above).

        Returns the (ime_counter, field_counter) used, for logging.
        """
        from androidtvremote2.remotemessage_pb2 import (
            RemoteMessage, RemoteImeBatchEdit, RemoteEditInfo, RemoteImeObject)
        ime_counter = _ime_state["ime_counter"] or proto.ime_counter
        field_counter = _ime_state["field_counter"] or proto.ime_field_counter
        msg = RemoteMessage()
        msg.remote_ime_batch_edit.CopyFrom(RemoteImeBatchEdit(
            ime_counter=ime_counter,
            field_counter=field_counter,
            edit_info=[RemoteEditInfo(
                insert=1,
                text_field_status=RemoteImeObject(
                    start=span_end, end=span_end, value=text))],
        ))
        proto._reset_idle_disconnect_task()
        proto._send_message(msg)
        return ime_counter, field_counter

    def recreate_remote():
        """Recreate remote instance after power cycle - fixes stale transport"""
        nonlocal remote
        try:
            print(f"🔄 Recreating remote for {tv_ip} after power cycle...")
            try:
                # Try to disconnect old
                future = asyncio.run_coroutine_threadsafe(remote.async_disconnect() if hasattr(remote, 'async_disconnect') else remote.disconnect(), loop)
                future.result(timeout=2)
            except:
                pass
            # Create new instance
            from androidtvremote2 import AndroidTVRemote
            new_remote = AndroidTVRemote(CLIENT_NAME, CERT_FILE, KEY_FILE, tv_ip)
            # Replace global
            remote = new_remote
            print(f"✅ Remote recreated")
            return True
        except Exception as e:
            print(f"❌ Remote recreation failed: {e}")
            return False

    # Keepalive to prevent 16s idle disconnect + handle power cycle
    def keepalive_loop():
        offline_since = None
        while True:
            try:
                time.sleep(10)
                is_live = has_live_connection(remote)
                set_connection_state(live=is_live)
                if is_live:
                    # Keep the IME state hook attached to the live protocol
                    # from the start, so the focus-time key-inject (which
                    # carries the live counters) is never missed.
                    _install_ime_hook()
                if not is_live:
                    if offline_since is None:
                        offline_since = time.time()
                    # Don't block press - check if press active
                    if press_lock.locked():
                        continue
                    if keepalive_lock.acquire(timeout=0.1):
                        try:
                            def _reconnect():
                                return asyncio.run_coroutine_threadsafe(remote.async_connect(), loop).result(timeout=3)
                            _reconnect()
                            set_connection_state(live=True, reconnecting=False, error=None)
                            offline_since = None
                        except Exception as e:
                            set_connection_state(live=False, reconnecting=False, error=str(e))
                        finally:
                            keepalive_lock.release()
                else:
                    offline_since = None
                    set_connection_state(live=True, reconnecting=False, error=None)
            except Exception as e:
                set_connection_state(live=False, reconnecting=False, error=str(e))

    print("💓 Keepalive thread started (10s interval + power cycle detection)")

    @app.route("/")
    def index():
        html = PREMIUM_HTML.replace("__TV_IP__", tv_ip).replace("__LOCAL_IP__", get_local_ip()).replace("__PORT__", str(PORT))
        return html

    @app.route("/status")
    def status_route():
        """Return connection status for UI polling - recommendation B"""
        with state_lock:
            # Refresh live check
            live = has_live_connection(remote)
            # Update state with current live check but keep reconnecting flag
            current = dict(connection_state)
            current["live"] = live
            current["tv_ip"] = tv_ip
            current["tv_mac"] = tv_mac
            # If not live and not already reconnecting, mark as offline
            if not live and not current["reconnecting"]:
                if not current["error"]:
                    current["error"] = "TV offline"
            return jsonify(current)

    @app.route("/press")
    def press_route():
        key = request.args.get("key", "HOME")
        is_power = key == "POWER"

        async def _ensure_connected_and_press():
            max_attempts = 6 if is_power else 3
            for attempt in range(max_attempts):
                try:
                    if not has_live_connection(remote):
                        try:
                            await remote.async_connect()
                            set_connection_state(live=True, reconnecting=False, error=None)
                        except Exception as ce:
                            if is_power and attempt==0:
                                send_wol(tv_mac, tv_ip)
                                await asyncio.sleep(0.8)
                                continue
                            if attempt < max_attempts-1:
                                await asyncio.sleep(0.3)
                                continue
                            raise ce
                    _install_ime_hook()
                    remote.send_key_command(key)
                    set_connection_state(live=True, reconnecting=False, error=None)
                    return True
                except Exception as e:
                    if attempt >= max_attempts-1:
                        raise e
                    await asyncio.sleep(0.3)
            return False

        # SMOOTH: Fire and forget - return immediately, no 2s blocking
        try:
            asyncio.run_coroutine_threadsafe(_ensure_connected_and_press(), loop)
            if is_power and not has_live_connection(remote):
                send_wol(tv_mac, tv_ip)
            return jsonify({"status":"ok","key":key, "live": True, "reconnecting": False, "queued": True}), 200
        except Exception as e:
            return jsonify({"status":"ok","key":key, "live": False, "reconnecting": True, "queued": True}), 200

    @app.route("/type")
    def type_route():
        """Apply phone-keyboard input to the TV's focused field.

        The client sends the delta vs its last send: `dels` KEYCODE_DEL
        presses for removed chars, then `text` with the newly typed suffix,
        plus `full` (its whole field) so the batch edit's span end can be the
        true post-edit field length. The batch edit itself is built with the
        TV's live IME counters (see _send_ime_text) — the library's send_text
        sends stale greeting counters and len(text)-1, which strict TV
        surfaces silently drop.
        """
        text = request.args.get("text", "")
        full = request.args.get("full")
        try:
            dels = max(0, min(int(request.args.get("dels", "0")), 100))
        except ValueError:
            dels = 0
        if len(text) > 500:
            text = text[:500]
        if full is not None and len(full) > 1000:
            full = full[:1000]
        print(f"[type] text={text!r} dels={dels}")
        async def _ensure_connected_and_type():
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    if not has_live_connection(remote):
                        try:
                            await remote.async_connect()
                            set_connection_state(live=True, reconnecting=False, error=None)
                        except Exception:
                            await asyncio.sleep(0.5)
                            continue
                    _install_ime_hook()
                    # send_key_command / batch edits buffer into the protocol
                    # (non-blocking); DELs buffer before the insert, so order holds.
                    for _ in range(dels):
                        remote.send_key_command("DEL")
                    if text:
                        proto = getattr(remote, "_remote_message_protocol", None)
                        if proto is not None and full is not None:
                            if _ime_state["value"] is not None:
                                # Ground truth: the TV's own reported field.
                                # After `dels` deletions + this insert, its
                                # length is the span end the TV validates.
                                span_end = max(0, _u16len(_ime_state["value"]) - dels) + _u16len(text)
                            else:
                                span_end = _u16len(full)
                            ic, fc = _send_ime_text(proto, text, span_end)
                            print(f"[type] batch edit: span_end={span_end} "
                                  f"ime_counter={ic} field_counter={fc}")
                        else:
                            # No field mirror (stale cached page): legacy path.
                            remote.send_text(text)
                    set_connection_state(live=True, reconnecting=False, error=None)
                    return True
                except Exception:
                    if attempt >= max_attempts - 1:
                        raise
                    await asyncio.sleep(0.3)
            return False
        # SMOOTH: Fire and forget - return immediately, no blocking
        try:
            asyncio.run_coroutine_threadsafe(_ensure_connected_and_type(), loop)
            return jsonify({"status": "ok", "typed": text, "dels": dels, "queued": True}), 200
        except Exception:
            return jsonify({"status": "ok", "typed": text, "dels": dels, "live": False, "reconnecting": True, "queued": True}), 200

    local_ip = get_local_ip()
    print(f"\n🚀 Premium Remote running! (Fixed concurrency + WOL + Last IP + Trackpad + Idle Fix + Status UI)")
    print(f"   Mac: http://localhost:{PORT}")
    print(f"   iPhone: http://{local_ip}:{PORT}")
    print(f"   TV: {tv_ip} MAC: {tv_mac or 'unknown - WOL disabled'}")
    print(f"   Trackpad: Swipe for direction, Tap for OK")
    print(f"   Keepalive: 10s to prevent 16s idle disconnect")
    print(f"   Status: /status endpoint for UI polling")
    print(f"   Keyboard: /type endpoint for phone keyboard input")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)

if __name__ == "__main__":
    main()

import argparse
import time
import serial
import serial.tools.list_ports
import threading
import re
import collections
import html
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

# --- Serial connection (guarded by serial_lock) --------------------------------
serial_conn = None
target_port = None
serial_lock = threading.Lock()

# --- Device state / logs (guarded by state_lock) -------------------------------
# Flask serves requests on multiple worker threads while the serial reader runs
# on its own thread, so every access to the shared state below must be locked.
state_lock = threading.Lock()

# Pure Python native types (None, True/False, Ints)
state = {
    "type": None,
    "fw_version": None,
    "power": None,
    "temperature": None,
    "auto_switch": None,
    "auto_mode": None,
    "earc": None,
    "in_source": None,
    "debug_log": None
}
state_timestamps = {}
device_logs = collections.deque(maxlen=5000)
log_counter = 0

input_names = {
    1: "Unnamed 1",
    2: "Unnamed 2",
    3: "Unnamed 3",
    4: "Unnamed 4"
}

# Precompiled once, reused on every parsed line.
TEMP_REGEX = re.compile(r"gsv chip temperature\D*(\d+)")
SOURCE_REGEX = re.compile(r"output->input(\d+)")
WHITESPACE_REGEX = re.compile(r"\s+")

# Canonical read commands for every queryable status field. Keeping this as a
# single mapping removes the old bytes/decode round-trip and the duplicated
# special-casing of "type" / "fw_version".
STATUS_COMMANDS = {
    "type": "r type!",
    "fw_version": "r fw version!",
    "temperature": "r temperature!",
    "power": "r power!",
    "auto_switch": "r auto switch!",
    "auto_mode": "r auto mode!",
    "earc": "r earc!",
    "in_source": "r in source!",
}
# Order used when the client asks for "all" (debug_log intentionally excluded:
# the device only reports it when the debug mode is toggled).
ALL_STATUS_ORDER = [
    "type", "fw_version", "temperature", "power",
    "auto_switch", "auto_mode", "earc", "in_source",
]


def add_device_log(raw_text, log_type="rx"):
    global log_counter
    with state_lock:
        log_counter += 1
        device_logs.append({
            "id": log_counter,
            "raw": raw_text,
            "html": html.escape(raw_text),
            "type": log_type
        })


def _update_state(key, value):
    with state_lock:
        state[key] = value
        state_timestamps[key] = time.time()


def parse_line(line):
    # Normalise so that write-command echoes and read-command responses parse
    # identically. The device has two quirks we neutralise here:
    #   1. Write echoes carry a trailing "!"  ->  "auto switch on!"
    #      Read responses do not              ->  "auto switch: on"
    #   2. Read responses put a colon between label and value ("earc: off"),
    #      write echoes use a plain space      ("earc off").
    # Stripping any trailing "!", removing colons and collapsing whitespace
    # reduces BOTH forms to a canonical "<label> <value>" string, so every
    # branch below matches regardless of read/write origin.
    raw = line.strip()
    while raw.endswith("!"):
        raw = raw[:-1].strip()
    canonical = WHITESPACE_REGEX.sub(" ", raw.replace(":", " ")).strip()
    line_lower = canonical.lower()

    if "gsv chip temperature" in line_lower:
        match = TEMP_REGEX.search(line_lower)
        if match:
            _update_state("temperature", int(match.group(1)))
    elif line_lower.startswith("mcu fw version"):
        _update_state("fw_version", line_lower.split("version", 1)[-1].strip())
    elif line_lower.startswith("auto switch mode"):
        val = line_lower.split("auto switch mode", 1)[-1].strip()
        if "5v" in val or "1" in val:
            _update_state("auto_mode", 1)
        elif "clock" in val or "0" in val:
            _update_state("auto_mode", 0)
    elif line_lower.startswith("auto switch"):
        val = line_lower.split("auto switch", 1)[-1].strip()
        _update_state("auto_switch", val in ["on", "1"])
    elif line_lower.startswith("earc"):
        val = line_lower.split("earc", 1)[-1].strip()
        _update_state("earc", val in ["on", "1"])
    elif "power on" in line_lower:
        _update_state("power", True)
    elif "power off" in line_lower:
        _update_state("power", False)
    elif "output->input" in line_lower:
        match = SOURCE_REGEX.search(line_lower)
        if match:
            _update_state("in_source", int(match.group(1)))
    elif "debug log on" in line_lower:
        _update_state("debug_log", True)
    elif "debug log off" in line_lower:
        _update_state("debug_log", False)
    elif "8k 4x1 earc hdmi switcher" in line_lower:
        _update_state("type", raw)


# Formats the raw Python state into an exact rendering payload for JS
def generate_ui_state():
    with state_lock:
        s = dict(state)  # cheap consistent snapshot; render outside the lock
    return {
        "texts": {
            "type": s["type"] if s["type"] is not None else "-",
            "fw_version": s["fw_version"] if s["fw_version"] is not None else "-",
            "temperature": f"{s['temperature']} °C" if s["temperature"] is not None else "-",
            "power": "On" if s["power"] is True else "Off" if s["power"] is False else "-",
            "auto_switch": "On" if s["auto_switch"] is True else "Off" if s["auto_switch"] is False else "-",
            "auto_mode": "1: 5V" if s["auto_mode"] == 1 else "0: Clock" if s["auto_mode"] == 0 else "-",
            "earc": "On" if s["earc"] is True else "Off" if s["earc"] is False else "-",
            "in_source": f"Input {s['in_source']}" if s["in_source"] is not None else "-",
            "debug_log": "On" if s["debug_log"] is True else "Off" if s["debug_log"] is False else "-"
        },
        "active_buttons": [
            "btn-power-on" if s["power"] is True else None,
            "btn-power-off" if s["power"] is False else None,
            "btn-autoswitch-on" if s["auto_switch"] is True else None,
            "btn-autoswitch-off" if s["auto_switch"] is False else None,
            "btn-automode-1" if s["auto_mode"] == 1 else None,
            "btn-automode-0" if s["auto_mode"] == 0 else None,
            "btn-earc-on" if s["earc"] is True else None,
            "btn-earc-off" if s["earc"] is False else None,
            "btn-debuglog-on" if s["debug_log"] is True else None,
            "btn-debuglog-off" if s["debug_log"] is False else None,
            f"btn-{s['in_source']}" if s["in_source"] is not None else None
        ],
        "show_terminal": s["debug_log"] is True
    }


def serial_reader_loop():
    global serial_conn, target_port
    while True:
        try:
            if serial_conn is None or not serial_conn.is_open:
                print(f"Attempting to connect to {target_port}...")
                conn = serial.Serial(target_port, 115200, timeout=1.0, write_timeout=2.0)
                try:
                    conn.reset_input_buffer()
                except Exception:
                    pass
                with serial_lock:
                    serial_conn = conn
                print(f"Connected to {target_port}")

            line = serial_conn.readline()
            if line:
                decoded = line.decode("utf-8", errors="ignore").strip()
                if decoded:
                    print(f"[Device] {decoded}")
                    add_device_log(decoded, "rx")
                    parse_line(decoded)

        except serial.SerialException as e:
            print(f"Serial connection error: {e}. Retrying in 5s...")
            with serial_lock:
                if serial_conn:
                    try:
                        serial_conn.close()
                    except Exception:
                        pass
                serial_conn = None
            time.sleep(5)
        except Exception as e:
            print(f"Unexpected read error: {e}")
            time.sleep(5)


def send_serial_cmd(cmd_str):
    # Serialise all writes (and the connection check) so a reconnect on the
    # reader thread can never swap the port out from under an in-flight write.
    with serial_lock:
        conn = serial_conn
        if not (conn and conn.is_open):
            return False
        add_device_log(cmd_str, "tx")
        try:
            conn.write(cmd_str.encode("utf-8"))
            return True
        except Exception as e:
            print(f"Write error: {e}")
            return False


def is_serial_connected():
    with serial_lock:
        return serial_conn is not None and serial_conn.is_open


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>SW411 Source Switch</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root {
            --bg-dark: #020617;
            --bg-light: #0f172a;
            --container-bg: rgba(30, 41, 59, 0.4);
            --container-border: rgba(255, 255, 255, 0.08);
            --btn-bg: rgba(255, 255, 255, 0.03);
            --btn-border: rgba(255, 255, 255, 0.1);
            --btn-hover-bg: rgba(56, 189, 248, 0.1);
            --btn-hover-border: rgba(255, 255, 255, 0.5);
            --btn-active-bg: rgba(56, 189, 248, 0.2);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --success: #10b981;
            --error: #ef4444;
        }

        body {
            font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
            background: radial-gradient(circle at top, var(--bg-light) 0%, var(--bg-dark) 100%);
            color: var(--text-main);
            margin: 0;
            min-height: 100vh;
            min-height: 100dvh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            box-sizing: border-box;
        }

        .container { width: 100%; max-width: 480px; background: var(--container-bg); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border: 1px solid var(--container-border); border-radius: 24px; padding: 40px 30px; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5); text-align: center; position: relative; overflow: hidden; }
        .blob { position: absolute; filter: blur(120px); z-index: -1; opacity: 0.6; pointer-events: none; }
        .blob-1 { width: 400px; height: 400px; background: #60a5fa; top: -200px; left: -200px; border-radius: 40% 60% 70% 30% / 40% 50% 60% 50%; animation: move-1 30s infinite cubic-bezier(0.4, 0, 0.2, 1) alternate; }
        .blob-2 { width: 350px; height: 350px; background: #c084fc; top: -100px; right: -150px; border-radius: 60% 40% 30% 70% / 50% 60% 40% 50%; animation: move-2 35s infinite cubic-bezier(0.4, 0, 0.2, 1) alternate-reverse; }
        .blob-3 { width: 380px; height: 380px; background: #22d3ee; bottom: -200px; left: -100px; border-radius: 50% 50% 40% 60% / 60% 40% 50% 50%; animation: move-3 40s infinite cubic-bezier(0.4, 0, 0.2, 1) alternate; }

        @keyframes move-1 { 0% { transform: translate(0px, 0px) scale(1) rotate(0deg); } 33% { transform: translate(500px, 150px) scale(1.2) rotate(90deg); border-radius: 60% 40% 30% 70% / 50% 60% 40% 50%; } 66% { transform: translate(150px, 450px) scale(0.8) rotate(180deg); border-radius: 50% 50% 40% 60% / 60% 40% 50% 50%; } 100% { transform: translate(450px, 500px) scale(1.1) rotate(270deg); border-radius: 40% 60% 70% 30% / 40% 50% 60% 50%; } }
        @keyframes move-2 { 0% { transform: translate(0px, 0px) scale(1) rotate(0deg); } 33% { transform: translate(-450px, 200px) scale(1.1) rotate(120deg); border-radius: 40% 60% 70% 30% / 40% 50% 60% 50%; } 66% { transform: translate(-200px, 500px) scale(0.9) rotate(240deg); border-radius: 50% 50% 40% 60% / 60% 40% 50% 50%; } 100% { transform: translate(-500px, 100px) scale(1.2) rotate(360deg); border-radius: 60% 40% 30% 70% / 50% 60% 40% 50%; } }
        @keyframes move-3 { 0% { transform: translate(0px, 0px) scale(1) rotate(0deg); } 33% { transform: translate(450px, -200px) scale(1.15) rotate(-90deg); border-radius: 60% 40% 30% 70% / 50% 60% 40% 50%; } 66% { transform: translate(100px, -500px) scale(0.85) rotate(-180deg); border-radius: 40% 60% 70% 30% / 40% 50% 60% 50%; } 100% { transform: translate(500px, -450px) scale(1.1) rotate(-270deg); border-radius: 50% 50% 40% 60% / 60% 40% 50% 50%; } }

        @media (max-width: 480px) {
            body { padding: 15px; align-items: flex-start; padding-top: 5vh; }
            .container { padding: 30px 20px; border-radius: 20px; }
            h1 { font-size: 1.8rem; }
            .source-btn { padding: 15px 10px; }
            .source-grid { gap: 12px; }
            .info-grid { grid-template-columns: 1fr !important; }
        }

        h1 { margin: 0 0 5px 0; font-weight: 700; font-size: 2.2rem; letter-spacing: -0.5px; background: linear-gradient(135deg, #fff 0%, #94a3b8 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        p.subtitle { margin: 0 0 35px 0; color: var(--text-muted); font-size: 0.95rem; font-weight: 400; }

        .source-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; }
        .source-btn { background: var(--btn-bg); border: 1px solid var(--btn-border); color: var(--text-main); padding: 25px 15px; border-radius: 16px; cursor: pointer; transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); display: flex; flex-direction: column; align-items: center; gap: 12px; position: relative; overflow: hidden; }
        .source-btn svg { width: 32px; height: 32px; fill: none; stroke: var(--text-muted); stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; transition: all 0.3s ease; }
        .source-btn:hover { background: var(--btn-hover-bg); border-color: var(--btn-hover-border); transform: translateY(-4px); box-shadow: 0 10px 25px -5px rgba(56, 189, 248, 0.15), 0 0 10px rgba(56, 189, 248, 0.1) inset; }
        .source-btn:hover svg { stroke: var(--accent); filter: drop-shadow(0 0 8px rgba(56, 189, 248, 0.5)); }
        .source-btn.active { border-color: var(--accent); background: var(--btn-active-bg); box-shadow: 0 0 15px rgba(56, 189, 248, 0.3); }
        .source-btn.active svg { stroke: var(--accent); filter: drop-shadow(0 0 10px rgba(56, 189, 248, 0.8)); }
        .source-btn:active { transform: translateY(0); transition: all 0.1s; }
        .btn-content { display: flex; flex-direction: column; align-items: center; gap: 4px; z-index: 1; }
        .source-btn .label { font-size: 1.2rem; font-weight: 600; letter-spacing: 0.5px; }
        .source-btn .sub-label { font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; font-weight: 500; }

        #status-msg { margin-top: 20px; min-height: 24px; font-size: 0.9rem; font-weight: 500; opacity: 0; transform: translateY(10px); transition: all 0.3s ease; text-align: center; }
        #status-msg.show { opacity: 1; transform: translateY(0); }
        .status-loading { color: var(--text-muted); }
        .status-success { color: var(--success); }
        .status-error { color: var(--error); }

        @keyframes pulse { 0% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.1); opacity: 0.7; } 100% { transform: scale(1); opacity: 1; } }
        .switching svg { animation: pulse 1s infinite; stroke: var(--accent); }
        .switching { border-color: var(--btn-hover-border); background: var(--btn-hover-bg); }

        .advanced-section { margin-top: 30px; text-align: left; border-top: 1px solid var(--container-border); padding-top: 20px; }
        .advanced-summary { cursor: pointer; font-weight: 600; color: var(--text-muted); display: flex; align-items: center; gap: 10px; font-size: 1.1rem; list-style: none; transition: color 0.3s ease; user-select: none; }
        .advanced-summary::-webkit-details-marker { display: none; }
        .advanced-summary::before { content: '▶'; font-size: 0.8em; transition: transform 0.3s ease; }
        details[open] .advanced-summary::before { transform: rotate(90deg); }
        .advanced-summary:hover { color: var(--accent); }
        .advanced-content { margin-top: 20px; display: flex; flex-direction: column; gap: 20px; animation: fadeIn 0.4s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }

        .info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; background: rgba(0, 0, 0, 0.2); padding: 15px; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.05); }
        .info-item { display: flex; flex-direction: column; gap: 4px; }
        .info-item .label { font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; }
        .info-item .val { font-weight: 600; font-size: 1.05rem; }

        .status-grid { background: rgba(0, 0, 0, 0.2); padding: 15px; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.05); }
        .status-row { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05); }
        .status-row:last-child { border-bottom: none; }
        .status-label { color: var(--text-muted); font-size: 0.95rem; flex: 1; }
        .status-val { font-weight: 600; margin-right: 8px; text-align: right; text-transform: capitalize; white-space: nowrap; }
        .status-ts { font-size: 0.7rem; color: var(--text-muted); font-style: italic; white-space: nowrap; margin-right: 6px; }
        .btn-update { background: var(--btn-bg); border: 1px solid var(--btn-border); color: var(--text-main); padding: 6px 12px; border-radius: 6px; font-size: 0.8rem; cursor: pointer; transition: all 0.2s; }
        .btn-update:hover { background: var(--btn-hover-bg); border-color: var(--accent); color: var(--accent); }
        .btn-update:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-icon { background: transparent; border: none; color: var(--text-muted); cursor: pointer; padding: 4px; border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; transition: all 0.2s; }
        .btn-icon:hover { color: var(--accent); background: rgba(255,255,255,0.05); }

        .btn-update-all { width: 100%; margin-top: 15px; padding: 12px; font-size: 1rem; background: var(--btn-bg); border: 1px solid var(--btn-border); color: var(--text-main); border-radius: 8px; cursor: pointer; transition: all 0.2s; }
        .btn-update-all:hover { background: var(--btn-hover-bg); border-color: var(--accent); }

        .controls-grid { display: flex; flex-direction: column; gap: 15px; background: rgba(0, 0, 0, 0.2); padding: 15px; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.05); }
        .control-group { display: flex; flex-direction: column; gap: 8px; }
        .control-group .label { font-size: 0.95rem; color: var(--text-muted); }
        .btn-row { display: flex; gap: 10px; flex-wrap: wrap; }
        .control-btn { background: var(--btn-bg); border: 1px solid var(--btn-border); color: var(--text-main); padding: 8px 16px; border-radius: 8px; cursor: pointer; transition: all 0.2s; font-size: 0.9rem; flex: 1; }
        .control-btn:hover { background: var(--btn-hover-bg); border-color: var(--btn-hover-border); }
        .control-btn.active { background: var(--btn-active-bg); border-color: var(--accent); color: var(--accent); box-shadow: 0 0 10px rgba(56, 189, 248, 0.2); }
        .control-btn.danger { border-color: rgba(239, 68, 68, 0.3); }
        .control-btn.danger:hover { background: rgba(239, 68, 68, 0.2); border-color: var(--error); color: var(--error); }

        .terminal-container { background: rgba(0, 0, 0, 0.4); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 12px; padding: 15px; margin-top: 10px; display: none; }
        .terminal-output { height: 200px; overflow-y: auto; font-family: monospace; font-size: 0.85rem; white-space: pre-wrap; word-break: break-all; margin-bottom: 15px; padding-right: 5px; }
        .terminal-output::-webkit-scrollbar { width: 8px; }
        .terminal-output::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.2); border-radius: 4px; }
        .terminal-input-row { display: flex; gap: 10px; }
        .terminal-input { flex-grow: 1; background: rgba(0, 0, 0, 0.2); border: 1px solid rgba(255, 255, 255, 0.1); color: white; padding: 8px 12px; border-radius: 6px; font-family: monospace; outline: none; }
        .terminal-input:focus { border-color: var(--accent); }
    </style>
</head>
<body>

<div class="container" id="main-container">
    <div class="blob blob-1"></div>
    <div class="blob blob-2"></div>
    <div class="blob blob-3"></div>
    <h1>SW411</h1>
    <p class="subtitle">HDMI Matrix Control</p>

    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; padding: 0 5px;">
        <span id="last-synced" style="color: var(--text-muted); font-size: 0.85rem; font-weight: 500;">Last synced: Never</span>
        <button class="btn-icon" title="Update Source" onclick="fetchStatus('in_source', this)"><svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
    </div>

    <div class="source-grid">
        <button class="source-btn" id="btn-1" onclick="sendCommand('s in source 1!', this)">
            <svg viewBox="0 0 24 24"><rect x="2" y="6" width="20" height="12" rx="6" ry="6"></rect><circle cx="17.5" cy="10.5" r="1"></circle><circle cx="14.5" cy="13.5" r="1"></circle><path d="M6 12h4m-2-2v4"></path></svg>
            <div class="btn-content"><span class="label">{{ input1 }}</span><span class="sub-label">Input 1</span></div>
        </button>
        <button class="source-btn" id="btn-2" onclick="sendCommand('s in source 2!', this)">
            <svg viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><path d="M8 21h8m-4-4v4"></path></svg>
            <div class="btn-content"><span class="label">{{ input2 }}</span><span class="sub-label">Input 2</span></div>
        </button>
        <button class="source-btn" id="btn-3" onclick="sendCommand('s in source 3!', this)">
            <svg viewBox="0 0 24 24"><rect x="2" y="7" width="20" height="15" rx="2" ry="2"></rect><path d="M17 2l-5 5-5-5"></path></svg>
            <div class="btn-content"><span class="label">{{ input3 }}</span><span class="sub-label">Input 3</span></div>
        </button>
        <button class="source-btn" id="btn-4" onclick="sendCommand('s in source 4!', this)">
            <svg viewBox="0 0 24 24"><path d="M12 22v-5"></path><path d="M9 8V2"></path><path d="M15 8V2"></path><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"></path></svg>
            <div class="btn-content"><span class="label">{{ input4 }}</span><span class="sub-label">Input 4</span></div>
        </button>
    </div>

    <div id="status-msg"></div>

    <details class="advanced-section">
        <summary class="advanced-summary">Advanced</summary>
        <div class="advanced-content">

            <div class="info-grid">
                <div class="info-item">
                    <div style="display:flex; align-items:center; gap: 5px;">
                        <span class="label">Device</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('type', this)"><svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                        <span id="ts-type" class="status-ts"></span>
                    </div>
                    <span id="val-type" class="val">-</span>
                </div>
                <div class="info-item">
                    <div style="display:flex; align-items:center; gap: 5px;">
                        <span class="label">Firmware</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('fw_version', this)"><svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                        <span id="ts-fw_version" class="status-ts"></span>
                    </div>
                    <span id="val-fw_version" class="val">-</span>
                </div>
            </div>

            <div class="status-grid">
                <div class="status-row">
                    <span class="status-label">Power<br><span style="font-size: 0.7rem; font-style: italic;">May report ON when LED indicates OFF.</span><span id="ts-power" class="status-ts"></span></span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-power" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('power', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">Active Source<span id="ts-in_source" class="status-ts"></span></span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-in_source" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('in_source', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">Temperature<span id="ts-temperature" class="status-ts"></span></span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-temperature" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('temperature', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">Auto Switch<span id="ts-auto_switch" class="status-ts"></span></span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-auto_switch" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('auto_switch', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">Auto Mode<span id="ts-auto_mode" class="status-ts"></span></span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-auto_mode" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('auto_mode', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">eARC<span id="ts-earc" class="status-ts"></span></span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-earc" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('earc', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">Debug Log<br><span style="font-size: 0.7rem; font-style: italic;">Only reports state when changing debug mode.</span><span id="ts-debug_log" class="status-ts"></span></span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-debug_log" class="status-val">-</span>
                    </div>
                </div>
                <button class="btn-update-all" onclick="fetchStatus('all', this)">Update All Statuses</button>
            </div>

            <div class="controls-grid">
                <div class="control-group">
                    <span class="label">Power</span>
                    <div class="btn-row">
                        <button class="control-btn" id="btn-power-on" onclick="sendCommand('power 1!', this)">On</button>
                        <button class="control-btn" id="btn-power-off" onclick="if(confirm('Warning: Powering off via serial may not work reliably. Continue?')) sendCommand('power 0!', this)">Off</button>
                    </div>
                </div>
                <div class="control-group">
                    <span class="label">Auto Switch</span>
                    <div class="btn-row">
                        <button class="control-btn" id="btn-autoswitch-on" onclick="sendCommand('s auto switch 1!', this)">On</button>
                        <button class="control-btn" id="btn-autoswitch-off" onclick="sendCommand('s auto switch 0!', this)">Off</button>
                    </div>
                </div>
                <div class="control-group">
                    <span class="label">Auto Mode</span>
                    <div class="btn-row">
                        <button class="control-btn" id="btn-automode-1" onclick="sendCommand('s auto mode 1!', this)">1: 5V Mode</button>
                        <button class="control-btn" id="btn-automode-0" onclick="sendCommand('s auto mode 0!', this)">0: Clock</button>
                    </div>
                </div>
                <div class="control-group">
                    <span class="label">eARC</span>
                    <div class="btn-row">
                        <button class="control-btn" id="btn-earc-on" onclick="sendCommand('s earc 1!', this)">On</button>
                        <button class="control-btn" id="btn-earc-off" onclick="sendCommand('s earc 0!', this)">Off</button>
                    </div>
                </div>
                <div class="control-group">
                    <span class="label">Debug Log</span>
                    <div class="btn-row">
                        <button class="control-btn" id="btn-debuglog-on" onclick="sendCommand('s debug log 1!', this)">On</button>
                        <button class="control-btn" id="btn-debuglog-off" onclick="sendCommand('s debug log 0!', this)">Off</button>
                    </div>
                </div>
                <div class="control-group">
                    <span class="label">System</span>
                    <div class="btn-row">
                        <button class="control-btn danger" onclick="if(confirm('Reboot device?')) sendCommand('reboot!', this)">Reboot</button>
                        <button class="control-btn danger" onclick="if(confirm('Factory reset device?')) sendCommand('reset!', this)">Reset</button>
                    </div>
                </div>
            </div>

            <div class="terminal-container" id="terminal-section">
                <div class="label" style="margin-bottom: 10px; color: var(--text-muted); font-size: 0.95rem;">Terminal Logs</div>
                <div class="terminal-output" id="terminal-output"></div>
                <div class="terminal-input-row">
                    <input type="text" id="terminal-input" class="terminal-input" placeholder="Custom cmd..." onkeypress="if(event.key === 'Enter') sendCustomCommand()">
                    <button class="btn-update" style="padding: 8px 16px;" onclick="sendCustomCommand()">Send</button>
                    <button class="btn-update" id="btn-record-logs" style="padding: 8px 16px; background: #10b981; border-color: #10b981; color: white;" onclick="toggleRecording()">Start Recording</button>
                </div>
            </div>

        </div>
    </details>
</div>

<script>
    let statusTimeout;
    let isRecording = false;
    let recordedLogs = [];
    let lastLogId = -1;
    let lastRecordedId = -1;
    let stateTimestamps = {};
    let pollIntervalId = null;
    let logsIntervalId = null;
    let tsIntervalId = null;
    let terminalVisible = false;
    const ALL_KEYS = ['type','fw_version','temperature','power','auto_switch','auto_mode','earc','in_source','debug_log'];

    function showStatus(message, type) {
        const statusEl = document.getElementById('status-msg');
        statusEl.innerText = message;
        statusEl.className = `show status-${type}`;
        clearTimeout(statusTimeout);
        if (type !== 'loading') {
            statusTimeout = setTimeout(() => { statusEl.classList.remove('show'); }, 3000);
        }
    }

    // Single helper to restore a button to its idle look, used by every
    // request handler so the reset logic lives in exactly one place.
    function resetButton(btn) {
        if (!btn) return;
        btn.classList.remove('switching');
        btn.disabled = false;
        btn.style.opacity = '1';
        btn.style.pointerEvents = 'auto';
    }

    function formatTimeDiff(epochSecs) {
        if (!epochSecs) return '';
        const now = Date.now() / 1000;
        const diffSecs = Math.floor(now - epochSecs);
        if (diffSecs < 60) return `${diffSecs}s ago`;
        const diffMins = Math.floor(diffSecs / 60);
        if (diffMins < 60) return `${diffMins}m ago`;
        const diffHrs = Math.floor(diffMins / 60);
        if (diffHrs < 24) return `${diffHrs}h ago`;
        return ">1d ago";
    }

    function refreshAllTimestamps() {
        ALL_KEYS.forEach(key => {
            const tsEl = document.getElementById(`ts-${key}`);
            if (tsEl && stateTimestamps[key]) {
                tsEl.innerHTML = (["type", "fw_version"].includes(key) ? "" : "<br>") + formatTimeDiff(stateTimestamps[key]);
            }
        });
        const syncEl = document.getElementById('last-synced');
        if (stateTimestamps['in_source']) {
            syncEl.innerText = 'Last synced: ' + formatTimeDiff(stateTimestamps['in_source']);
        }
    }

    // Completely "Dumb" UI rendering - relies 100% on Python's logic
    function updateUIState(ui, timestamps) {
        if (timestamps) Object.assign(stateTimestamps, timestamps);

        // Render all text strings supplied by Python
        for (const [key, val] of Object.entries(ui.texts)) {
            const el = document.getElementById(`val-${key}`);
            if (el) el.innerText = val;
        }

        // Toggle terminal display + remember state so log polling can be
        // fully suppressed while debug/terminal is disabled.
        terminalVisible = !!ui.show_terminal;
        const terminal = document.getElementById('terminal-section');
        terminal.style.display = terminalVisible ? 'block' : 'none';

        // Clear all buttons, then activate the ones Python told us to
        document.querySelectorAll('.source-btn, .control-btn').forEach(btn => btn.classList.remove('active'));
        ui.active_buttons.forEach(id => {
            if (id) {
                const el = document.getElementById(id);
                if (el) el.classList.add('active');
            }
        });

        refreshAllTimestamps();
    }

    function sendCommand(cmd, btnElement) {
        showStatus('Sending...', 'loading');
        if (btnElement) {
            if (btnElement.classList.contains('source-btn')) {
                document.querySelectorAll('.source-btn').forEach(btn => btn.classList.remove('switching'));
                btnElement.classList.add('switching');
            } else {
                btnElement.disabled = true;
                btnElement.style.opacity = '0.5';
            }
        }

        fetch('/api/command', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: cmd})
        })
        .then(response => response.json())
        .then(data => {
            resetButton(btnElement);
            if (data.error) showStatus(data.error, 'error');
            else showStatus(data.message || 'Sent', 'success');
        })
        .catch(e => {
            resetButton(btnElement);
            showStatus('Network error occurred.', 'error');
        });
    }

    function fetchStatus(type, btnElement) {
        if (btnElement) {
            btnElement.style.opacity = '0.5';
            btnElement.style.pointerEvents = 'none';
        }

        fetch(`/api/status?type=${type}`)
        .then(r => r.json())
        .then(data => {
            resetButton(btnElement);
            if (data.error) showStatus(data.error, 'error');
            else showStatus('Status requested', 'success');
        })
        .catch(e => {
            resetButton(btnElement);
            showStatus('Failed to fetch status', 'error');
        });
    }

    function sendCustomCommand() {
        const input = document.getElementById('terminal-input');
        const cmd = input.value.trim();
        if (cmd) {
            sendCommand(cmd);
            input.value = '';
        }
    }

    function toggleRecording() {
        const btn = document.getElementById('btn-record-logs');
        if (!isRecording) {
            isRecording = true;
            recordedLogs = [];
            btn.innerText = 'Stop Recording';
            btn.style.background = '#ef4444';
            btn.style.borderColor = '#ef4444';
        } else {
            isRecording = false;
            btn.innerText = 'Start Recording';
            btn.style.background = '#10b981';
            btn.style.borderColor = '#10b981';
            downloadLogs();
        }
    }

    function downloadLogs() {
        if (recordedLogs.length === 0) {
            alert('No logs recorded.');
            return;
        }
        const blob = new Blob([recordedLogs.join(String.fromCharCode(10))], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        a.download = `sw411-logs-${timestamp}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    // Highly efficient terminal DOM updater
    function updateLogs() {
        // Do not poll/fetch device logs at all while the terminal is hidden.
        if (!terminalVisible) return;

        fetch(`/api/logs?since_id=${lastLogId}`)
            .then(r => r.json())
            .then(logs => {
                if (!logs || logs.length === 0) return;

                const term = document.getElementById('terminal-output');
                const isScrolledToBottom = term.scrollHeight - term.clientHeight <= term.scrollTop + 2;
                const frag = document.createDocumentFragment();

                logs.forEach(log => {
                    const div = document.createElement('div');
                    if (log.type === 'rx') {
                        div.style.color = '#f8fafc';
                        div.innerHTML = log.html;
                    } else {
                        div.style.color = '#38bdf8';
                        div.style.fontWeight = 'bold';
                        div.innerHTML = '> ' + log.html;
                    }
                    frag.appendChild(div);

                    if (isRecording && log.id > lastRecordedId) {
                        recordedLogs.push((log.type === 'tx' ? '> ' : '') + log.raw);
                    }
                    lastLogId = log.id;
                    lastRecordedId = log.id;
                });

                term.appendChild(frag);

                // Cap DOM at 5000 lines to match the server-side log buffer
                while (term.childNodes.length > 5000) {
                    term.removeChild(term.firstChild);
                }

                if (isScrolledToBottom) {
                    term.scrollTop = term.scrollHeight;
                }
            })
            .catch(e => console.error('Error fetching logs:', e));
    }

    function pollCachedState() {
        fetch('/api/cached_state')
            .then(r => r.json())
            .then(data => {
                if (data.ui) updateUIState(data.ui, data.timestamps);
            })
            .catch(() => {});
    }

    function startPolling() {
        // Normal UI/state refresh every 1s
        if (!pollIntervalId) pollIntervalId = setInterval(pollCachedState, 1000);
        // Terminal logs poll more frequently, every 0.5s
        if (!logsIntervalId) logsIntervalId = setInterval(updateLogs, 500);
        // "x ago" labels tick once per second, but only while visible
        if (!tsIntervalId) tsIntervalId = setInterval(refreshAllTimestamps, 1000);
    }

    function stopPolling() {
        if (pollIntervalId) { clearInterval(pollIntervalId); pollIntervalId = null; }
        if (logsIntervalId) { clearInterval(logsIntervalId); logsIntervalId = null; }
        if (tsIntervalId) { clearInterval(tsIntervalId); tsIntervalId = null; }
    }

    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
            pollCachedState();
            updateLogs();
            startPolling();
        } else {
            stopPolling();
        }
    });

    if (document.visibilityState === "visible") {
        pollCachedState();
        updateLogs();
        startPolling();
    }
</script>
</body>
</html>"""


def find_sw411_port():
    for port in serial.tools.list_ports.comports():
        if port.vid == 0x1a86 and port.pid == 0x7523:
            return port.device
    return None


@app.route('/')
def index():
    return render_template_string(
        HTML_TEMPLATE,
        input1=input_names[1],
        input2=input_names[2],
        input3=input_names[3],
        input4=input_names[4]
    )


@app.route('/api/cached_state', methods=['GET'])
def cached_state():
    ui = generate_ui_state()
    with state_lock:
        ts = dict(state_timestamps)
    return jsonify({"ui": ui, "timestamps": ts})


@app.route('/api/status', methods=['GET'])
def get_status():
    status_type = request.args.get('type', 'all')

    if not is_serial_connected():
        return jsonify({"error": "Serial disconnected"}), 500

    queries = ALL_STATUS_ORDER if status_type == 'all' else [status_type]

    for q in queries:
        cmd = STATUS_COMMANDS.get(q)
        if cmd:
            send_serial_cmd(cmd)
            time.sleep(0.05)

    return jsonify({"message": "Status requested"})


@app.route('/api/command', methods=['POST'])
def handle_command():
    data = request.get_json(silent=True) or {}
    cmd_str = data.get('command')

    if not cmd_str:
        return jsonify({"error": "No command provided"}), 400

    cmd_str = cmd_str.rstrip('!') + '!'

    if not is_serial_connected():
        return jsonify({"error": "Serial port disconnected"}), 500

    if send_serial_cmd(cmd_str):
        return jsonify({"message": "Command sent"})
    return jsonify({"error": "Failed to write to device"}), 500


@app.route('/api/logs', methods=['GET'])
def get_logs():
    try:
        since_id = int(request.args.get('since_id', -1))
    except (TypeError, ValueError):
        since_id = -1

    with state_lock:
        new_logs = [log for log in device_logs if log['id'] > since_id]
    return jsonify(new_logs)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Feintech SW411 Simple Web Remote")
    parser.add_argument('--port', type=str, default='/dev/ttyUSB0', help='Serial port')
    parser.add_argument('--auto-port', action='store_true', help='Auto detect based on VID/PID')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind')
    parser.add_argument('--web-port', type=int, default=5000, help='Port to bind')

    parser.add_argument('--input-1', type=str, default='Unnamed 1', help='Label for Input 1')
    parser.add_argument('--input-2', type=str, default='Unnamed 2', help='Label for Input 2')
    parser.add_argument('--input-3', type=str, default='Unnamed 3', help='Label for Input 3')
    parser.add_argument('--input-4', type=str, default='Unnamed 4', help='Label for Input 4')

    args = parser.parse_args()

    input_names[1] = args.input_1
    input_names[2] = args.input_2
    input_names[3] = args.input_3
    input_names[4] = args.input_4

    target_port = args.port
    if args.auto_port:
        print("Auto-port mode enabled. Searching for SW411...")
        detected_port = find_sw411_port()
        if detected_port:
            target_port = detected_port
            print(f"Success! Found SW411 on {target_port}")
        else:
            print(f"Could not auto-detect, falling back to default port: {target_port}")

    reader_thread = threading.Thread(target=serial_reader_loop, daemon=True)
    reader_thread.start()

    print(f"Starting simple web server on http://{args.host}:{args.web_port}")
    app.run(host=args.host, port=args.web_port, use_reloader=False)

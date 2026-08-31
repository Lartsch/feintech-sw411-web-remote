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
serial_lock = threading.Lock()
target_port = None

state = {
    "type": "Unknown",
    "fw_version": "Unknown",
    "power": "Unknown",
    "temperature": "Unknown",
    "auto_switch": "Unknown",
    "auto_mode": "Unknown",
    "earc": "Unknown",
    "in_source": "Unknown",
    "debug_log": "Unknown"
}

state_timestamps = {}

device_logs = collections.deque(maxlen=1000)
log_counter = 0

# Global dictionary to store input names
input_names = {
    1: "Unnamed 1",
    2: "Unnamed 2",
    3: "Unnamed 3",
    4: "Unnamed 4"
}

def add_device_log(raw_text, log_type="rx"):
    global log_counter
    log_counter += 1
    device_logs.append({
        "id": log_counter, 
        "raw": raw_text,
        "html": html.escape(raw_text),
        "type": log_type
    })

TEMP_REGEX = re.compile(r"gsv chip temperature:\s*(\d+)")
SOURCE_REGEX = re.compile(r"output->input(\d+)")

def _update_state(key, value):
    global state, state_timestamps
    state[key] = value
    state_timestamps[key] = time.time()

def parse_line(line):
    global state
    line_lower = line.lower()
    
    if "gsv chip temperature:" in line_lower:
        match = TEMP_REGEX.search(line_lower)
        if match:
            _update_state("temperature", match.group(1))
    elif "mcu fw version:" in line_lower:
        _update_state("fw_version", line_lower.split("version:")[-1].strip())
    elif "earc:" in line_lower:
        _update_state("earc", line_lower.split("earc:")[-1].strip())
    elif "power on" in line_lower:
        _update_state("power", "on")
    elif "power off" in line_lower:
        _update_state("power", "off")
    elif "auto switch:" in line_lower:
        _update_state("auto_switch", line_lower.split("auto switch:")[-1].strip())
    elif "auto switch mode:" in line_lower:
        _update_state("auto_mode", line_lower.split("auto switch mode:")[-1].strip())
    elif "output->input" in line_lower:
        match = SOURCE_REGEX.search(line_lower)
        if match:
            _update_state("in_source", match.group(1))
    elif "debug log on" in line_lower:
        _update_state("debug_log", "on")
    elif "debug log off" in line_lower:
        _update_state("debug_log", "off")
    elif "8k 4x1 earc hdmi switcher" in line_lower:
        _update_state("type", line.strip())

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

        .container {
            width: 100%;
            max-width: 480px;
            background: var(--container-bg);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--container-border);
            border-radius: 24px;
            padding: 40px 30px;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
            text-align: center;
            position: relative;
            overflow: hidden;
        }

        .blob {
            position: absolute;
            filter: blur(120px);
            z-index: -1;
            opacity: 0.6;
            pointer-events: none;
        }

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

        /* Advanced Section */
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
        .btn-icon:disabled { opacity: 0.5; cursor: not-allowed; }
        .hint-icon { color: var(--text-muted); margin-left: 5px; cursor: help; display: inline-flex; align-items: center; vertical-align: middle; }
        .hint-icon:hover { color: var(--accent); }
        
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

    <details class="advanced-section" ontoggle="toggleAdvanced(this)">
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
                    <span class="status-label">
                        Power
                        <br><span style="font-size: 0.7rem; font-style: italic;">May report ON when LED indicates OFF.</span>
                        <span id="ts-power" class="status-ts"></span>
                    </span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-power" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('power', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">
                        Active Source
                        <span id="ts-in_source" class="status-ts"></span>
                    </span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-in_source" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('in_source', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                        
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">
                        Temperature
                        <span id="ts-temperature" class="status-ts"></span>
                    </span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-temperature" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('temperature', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">
                        Auto Switch
                        <span id="ts-auto_switch" class="status-ts"></span>
                    </span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-auto_switch" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('auto_switch', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">
                        Auto Mode
                        <span id="ts-auto_mode" class="status-ts"></span>
                    </span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-auto_mode" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('auto_mode', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">
                        eARC
                        <span id="ts-earc" class="status-ts"></span>
                    </span>
                    <div style="display:flex; align-items:center;">
                        <span id="val-earc" class="status-val">-</span>
                        <button class="btn-icon" title="Update" onclick="fetchStatus('earc', this)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg></button>
                    </div>
                </div>
                <div class="status-row">
                    <span class="status-label">
                        Debug Log
                        <br><span style="font-size: 0.7rem; font-style: italic;">Only reports state when changing debug mode.</span>
                        <span id="ts-debug_log" class="status-ts"></span>
                    </span>
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
    let lastRecordedId = -1;
    let stateTimestamps = {};
    let isCommandExecuting = false;
    let pollIntervalId = null;
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

    function toggleAdvanced(details) {
        // No width expansion anymore
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
                tsEl.innerHTML =
                    (["type", "fw_version"].includes(key) ? "" : "<br>") +
                    formatTimeDiff(stateTimestamps[key]);
            }
        });
        // Update the source selector sync label
        const syncEl = document.getElementById('last-synced');
        if (stateTimestamps['in_source']) {
            syncEl.innerText = 'Last synced: ' + formatTimeDiff(stateTimestamps['in_source']);
        }
    }
    setInterval(refreshAllTimestamps, 1000);

    function updateUIState(state, timestamps) {
        if (timestamps) {
            Object.assign(stateTimestamps, timestamps);
        }

        if (state.type !== 'Unknown') document.getElementById('val-type').innerText = state.type;
        if (state.fw_version !== 'Unknown') document.getElementById('val-fw_version').innerText = state.fw_version;
        if (state.temperature !== 'Unknown') document.getElementById('val-temperature').innerText = state.temperature + ' °C';
        if (state.power !== 'Unknown') document.getElementById('val-power').innerText = state.power;
        if (state.auto_switch !== 'Unknown') document.getElementById('val-auto_switch').innerText = state.auto_switch;
        
        let modeText = state.auto_mode;
        if (modeText !== 'Unknown') {
            if (modeText.toLowerCase().includes('5v')) modeText = '1: 5V';
            else if (modeText.toLowerCase().includes('clock')) modeText = '0: Clock';
        }
        if (state.auto_mode !== 'Unknown') document.getElementById('val-auto_mode').innerText = modeText;
        
        if (state.earc !== 'Unknown') document.getElementById('val-earc').innerText = state.earc;
        if (state.debug_log !== 'Unknown') {
            document.getElementById('val-debug_log').innerText = state.debug_log;
            const terminal = document.getElementById('terminal-section');
            if (state.debug_log === 'on') {
                terminal.style.display = 'block';
                updateLogs();
            } else {
                terminal.style.display = 'none';
            }
        }
        if (state.in_source !== 'Unknown') {
            const inSourceEl = document.getElementById('val-in_source');
            if (inSourceEl) inSourceEl.innerText = 'Input ' + state.in_source;
        }

        refreshAllTimestamps();

        // Active States for Source Buttons
        document.querySelectorAll('.source-btn').forEach(btn => btn.classList.remove('active'));
        if (state.in_source !== 'Unknown') {
            const activeBtn = document.getElementById(`btn-${state.in_source}`);
            if (activeBtn) activeBtn.classList.add('active');
        }

        // Active States for Controls
        const btnMappings = [
            { id: 'btn-power-on', val: state.power === 'on' },
            { id: 'btn-power-off', val: state.power === 'off' },
            { id: 'btn-autoswitch-on', val: state.auto_switch === 'on' },
            { id: 'btn-autoswitch-off', val: state.auto_switch === 'off' },
            { id: 'btn-automode-1', val: modeText === '1: 5V' },
            { id: 'btn-automode-0', val: modeText === '0: Clock' },
            { id: 'btn-earc-on', val: state.earc === 'on' },
            { id: 'btn-earc-off', val: state.earc === 'off' },
            { id: 'btn-debuglog-on', val: state.debug_log === 'on' },
            { id: 'btn-debuglog-off', val: state.debug_log === 'off' }
        ];
        btnMappings.forEach(m => {
            const el = document.getElementById(m.id);
            if (el) {
                if (m.val) el.classList.add('active');
                else el.classList.remove('active');
            }
        });
    }

    function sendCommand(cmd, btnElement) {
        isCommandExecuting = true;
        if(btnElement && btnElement.classList.contains('source-btn')) {
            document.querySelectorAll('.source-btn').forEach(btn => btn.classList.remove('switching'));
            btnElement.classList.add('switching');
            showStatus(`Executing command...`, 'loading');
        } else {
            showStatus(`Sending...`, 'loading');
            if(btnElement) {
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
            if(btnElement) {
                btnElement.classList.remove('switching');
                btnElement.disabled = false;
                btnElement.style.opacity = '1';
            }
            if (data.error) {
                showStatus(data.error, 'error');
            } else {
                showStatus(data.message || 'Success', 'success');
                if (data.state) updateUIState(data.state, data.timestamps);
            }
            updateLogs();
            isCommandExecuting = false;
        })
        .catch(e => {
            if(btnElement) {
                btnElement.classList.remove('switching');
                btnElement.disabled = false;
                btnElement.style.opacity = '1';
            }
            showStatus('Network error occurred.', 'error');
            isCommandExecuting = false;
        });
    }

    function fetchStatus(type, btnElement) {
        isCommandExecuting = true;
        if(btnElement) {
            btnElement.style.opacity = '0.5';
            btnElement.style.pointerEvents = 'none';
        }

        fetch(`/api/status?type=${type}`)
        .then(r => r.json())
        .then(data => {
            if(btnElement) {
                btnElement.style.opacity = '1';
                btnElement.style.pointerEvents = 'auto';
            }
            if (data.error) {
                showStatus(data.error, 'error');
            } else {
                updateUIState(data.state, data.timestamps);
            }
            updateLogs();
            isCommandExecuting = false;
        })
        .catch(e => {
            if(btnElement) {
                btnElement.style.opacity = '1';
                btnElement.style.pointerEvents = 'auto';
            }
            showStatus('Failed to fetch status', 'error');
            isCommandExecuting = false;
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

    function updateLogs() {
        if (document.getElementById('terminal-section').style.display === 'none') return;
        fetch('/api/logs')
            .then(r => r.json())
            .then(logs => {
                const term = document.getElementById('terminal-output');
                const isScrolledToBottom = term.scrollHeight - term.clientHeight <= term.scrollTop + 2;
                
                let htmlContent = [];
                logs.forEach(log => {
                    if (log.type === 'rx') {
                        htmlContent.push(`<div style="color: #f8fafc;">${log.html}</div>`);
                    } else {
                        htmlContent.push(`<div style="color: #38bdf8; font-weight: bold;">> ${log.html}</div>`);
                    }
                    
                    if (isRecording && log.id > lastRecordedId) {
                        recordedLogs.push((log.type === 'tx' ? '> ' : '') + log.raw);
                    }
                });
                
                if (logs.length > 0) {
                    lastRecordedId = logs[logs.length - 1].id;
                }
                
                term.innerHTML = htmlContent.join('');
                if (isScrolledToBottom) {
                    term.scrollTop = term.scrollHeight;
                }
            })
            .catch(e => console.error('Error fetching logs:', e));
    }

    function pollCachedState() {
        if (isCommandExecuting) return;
        
        fetch('/api/cached_state')
            .then(r => r.json())
            .then(data => {
                if (data.state) updateUIState(data.state, data.timestamps);
            })
            .catch(() => {});
    }

    function startPolling() {
        if (!pollIntervalId) {
            pollIntervalId = setInterval(pollCachedState, 3000);
        }
    }

    function stopPolling() {
        if (pollIntervalId) {
            clearInterval(pollIntervalId);
            pollIntervalId = null;
        }
    }

    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
            pollCachedState(); // Instantly poll on return to focus
            startPolling();
        } else {
            stopPolling();
        }
    });

    // Initial setup on page load
    if (document.visibilityState === "visible") {
        pollCachedState();
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

def send_and_wait(ser, cmd_bytes, expected_patterns=None, timeout=1.0):
    if not expected_patterns:
        expected_patterns = []
        
    ser.reset_input_buffer()
    ser.write(cmd_bytes)
    
    if not expected_patterns:
        time.sleep(0.15)
        while ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(f"[Device] {line}")
                add_device_log(line, "rx")
                parse_line(line)
        return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(f"[Device] {line}")
                add_device_log(line, "rx")
                parse_line(line)
                if any(p in line.lower() for p in expected_patterns):
                    return line
        time.sleep(0.02)
    return None

def get_status_patterns(status_type):
    if status_type == 'temperature': return [b"r temperature!", ["gsv chip temperature:"]]
    if status_type == 'power': return [b"r power!", ["power on", "power off"]]
    if status_type == 'auto_switch': return [b"r auto switch!", ["auto switch:"]]
    if status_type == 'auto_mode': return [b"r auto mode!", ["auto switch mode:"]]
    if status_type == 'earc': return [b"r earc!", ["earc:"]]
    if status_type == 'in_source': return [b"r in source!", ["output->input"]]
    return None, None

@app.route('/')
def index():
    # Pass the stored names as keyword arguments to be parsed by Jinja
    return render_template_string(
        HTML_TEMPLATE,
        input1=input_names[1],
        input2=input_names[2],
        input3=input_names[3],
        input4=input_names[4]
    )

@app.route('/api/cached_state', methods=['GET'])
def cached_state():
    return jsonify({"state": state, "timestamps": state_timestamps})

@app.route('/api/status', methods=['GET'])
def get_status():
    global target_port, state
    status_type = request.args.get('type', 'all')
    
    if not target_port:
        return jsonify({"error": "Serial port not configured"}), 500

    with serial_lock:
        ser = None
        try:
            ser = serial.Serial(target_port, 115200, timeout=0.1)
            
            queries = []
            if status_type == 'all':
                queries = ['type', 'fw_version', 'temperature', 'power', 'auto_switch', 'auto_mode', 'earc', 'in_source']
            else:
                queries = [status_type]
                
            for q in queries:
                if q == 'type':
                    send_and_wait(ser, b"r type!", ["switcher"], 0.5)
                elif q == 'fw_version':
                    send_and_wait(ser, b"r fw version!", ["mcu fw version:"], 0.5)
                else:
                    cmd_bytes, patterns = get_status_patterns(q)
                    if cmd_bytes:
                        add_device_log(cmd_bytes.decode('utf-8').strip(), "tx")
                        send_and_wait(ser, cmd_bytes, patterns, 0.5)
                        
            return jsonify({"state": state, "timestamps": state_timestamps})
        except Exception as e:
            print(f"Serial Error: {e}")
            return jsonify({"error": str(e)}), 500
        finally:
            if ser and ser.is_open:
                ser.close()

@app.route('/api/command', methods=['POST'])
def handle_command():
    global target_port, state
    data = request.json
    cmd_str = data.get('command')
    
    if not cmd_str:
        return jsonify({"error": "No command provided"}), 400

    cmd_str = cmd_str.rstrip('!') + '!'
    cmd_bytes = cmd_str.encode('utf-8')

    if not target_port:
        return jsonify({"error": "Serial port not configured"}), 500

    with serial_lock:
        ser = None
        try:
            ser = serial.Serial(target_port, 115200, timeout=0.1)
            
            # Poll power state ONCE before executing action commands (unless it's a power command)
            if not cmd_str.lower().startswith("power"):
                print(f"Checking power state before command: {cmd_str}")
                add_device_log("r power!", "tx")
                power_response = send_and_wait(ser, b"r power!", ["power on", "power off"], timeout=1.0)
                
                if not power_response or "power off" in power_response.lower():
                    return jsonify({"error": "Device is powered off"}), 400

            # Execute actual command
            add_device_log(cmd_str, "tx")
            
            # Find expected patterns for parsing
            patterns = []
            c = cmd_str.lower()
            if "auto mode" in c: patterns = ["auto switch mode:"]
            elif "auto switch" in c: patterns = ["auto switch:"]
            elif "source" in c: patterns = ["output->input"]
            elif "earc" in c: patterns = ["earc:"]
            elif "debug log" in c: patterns = ["debug log"]
            elif "power" in c: patterns = ["power on", "power off"]
            
            send_and_wait(ser, cmd_bytes, patterns, timeout=1.5)
            
            # Allow trailing output to be processed
            time.sleep(0.1)
            while ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    print(f"[Device] {line}")
                    add_device_log(line, "rx")
                    parse_line(line)

            return jsonify({"message": "Command executed", "state": state, "timestamps": state_timestamps})
            
        except Exception as e:
            print(f"Serial Error: {e}")
            return jsonify({"error": str(e)}), 500
        finally:
            if ser and ser.is_open:
                ser.close()

@app.route('/api/logs', methods=['GET'])
def get_logs():
    try:
        logs_list = list(device_logs.copy())
    except RuntimeError:
        logs_list = []
    return jsonify(logs_list)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Feintech SW411 Simple Web Remote")
    parser.add_argument('--port', type=str, default='/dev/ttyUSB0', help='Serial port')
    parser.add_argument('--auto-port', action='store_true', help='Auto detect based on VID/PID')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind')
    parser.add_argument('--web-port', type=int, default=5000, help='Port to bind')
    
    # New Arguments for Input Sources
    parser.add_argument('--input-1', type=str, default='Unnamed 1', help='Label for Input 1')
    parser.add_argument('--input-2', type=str, default='Unnamed 2', help='Label for Input 2')
    parser.add_argument('--input-3', type=str, default='Unnamed 3', help='Label for Input 3')
    parser.add_argument('--input-4', type=str, default='Unnamed 4', help='Label for Input 4')
    
    args = parser.parse_args()

    # Load custom input names into the global dictionary
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

    print(f"Starting simple web server on http://{args.host}:{args.web_port}")
    app.run(host=args.host, port=args.web_port)

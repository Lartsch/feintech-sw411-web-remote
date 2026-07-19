import argparse
import time
import threading
import serial
import serial.tools.list_ports
import re
import collections
import html
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

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

command_queue = []
device_logs = collections.deque(maxlen=5000)

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>SW411 Remote</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root {
            --bg-color: #0f172a;
            --container-bg: #1e293b;
            --card-bg: #334155;
            --primary: #3b82f6;
            --primary-hover: #60a5fa;
            --primary-active: #2563eb;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #fff;
        }
        body { font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: var(--bg-color); color: var(--text-main); margin: 0; padding: 20px; display: flex; justify-content: center; }
        .device-status-card.off .row:not(.power-row) { opacity: 0.3; pointer-events: none; }
        .container { width: 100%; max-width: 800px; background: var(--container-bg); border-radius: 12px; padding: 25px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        h1 { text-align: center; color: var(--accent); margin-top: 0; font-weight: 700; letter-spacing: 1px; }
        .info-bar { display: flex; flex-wrap: wrap; justify-content: space-between; background: var(--card-bg); padding: 15px 20px; border-radius: 8px; margin-bottom: 25px; box-shadow: inset 0 2px 4px rgba(0,0,0,0.15); border: 1px solid rgba(255,255,255,0.05); }
        .info-item { margin: 5px 0; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 25px; }
        .card { background: var(--card-bg); padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.05); }
        h2 { font-size: 1.3rem; color: var(--accent); margin-top: 0; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 8px; margin-bottom: 15px; }
        .row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
        .label { font-weight: 600; color: var(--text-muted); }
        .val { font-size: 1.1rem; font-weight: 500; }
        .btn-group { display: flex; gap: 10px; flex-wrap: wrap; }
        button { background: var(--primary); border: none; color: white; padding: 8px 16px; border-radius: 6px; cursor: pointer; transition: all 0.2s; font-weight: 600; font-size: 0.95rem; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
        button:hover:not(:disabled) { background: var(--primary-hover); transform: translateY(-1px); box-shadow: 0 4px 8px rgba(0,0,0,0.3); }
        button:active:not(:disabled) { transform: translateY(1px); box-shadow: 0 1px 2px rgba(0,0,0,0.2); }
        button:disabled { opacity: 0.4; cursor: not-allowed; }
        button:disabled.active { background: var(--primary-active); opacity: 1; cursor: default; transform: none; box-shadow: inset 0 3px 6px rgba(0,0,0,0.4); border: 1px solid var(--accent); color: #fff; }
        .btn-danger { background: #ef4444; }
        .btn-danger:hover:not(:disabled) { background: #f87171 !important; }
        .source-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-top: 15px; }
        .source-btn { padding: 20px 15px; font-size: 1.2rem; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 5px; }
        .active-source-text { color: var(--accent); font-weight: 700; font-size: 1.2rem; }
    </style>
</head>
<body>
<div class="container">
    <h1>Feintech SW411</h1>
    <div class="info-bar">
        <div class="info-item"><span class="label">Device:</span> <span id="type" class="val">-</span></div>
        <div class="info-item"><span class="label">Firmware:</span> <span id="fw_version" class="val">-</span></div>
    </div>
    
    <div class="grid">
        <div class="card">
            <h2>Source Selection</h2>
            <div class="source-grid">
                <button id="btn-source-1" class="source-btn" onclick="sendCommand('s in source 1!')">Input 1</button>
                <button id="btn-source-2" class="source-btn" onclick="sendCommand('s in source 2!')">Input 2</button>
                <button id="btn-source-3" class="source-btn" onclick="sendCommand('s in source 3!')">Input 3</button>
                <button id="btn-source-4" class="source-btn" onclick="sendCommand('s in source 4!')">Input 4</button>
            </div>
        </div>
        
        <div class="card" id="device-status-card">
            <h2>Device Status</h2>
            <div class="row"><span class="label">Temperature:</span> <span id="temperature" class="val">-</span></div>
            <div class="row power-row"><span class="label">Power:</span> <span id="power" class="val">-</span></div>
            <div class="row"><span class="label">Auto Switch:</span> <span id="auto_switch" class="val">-</span></div>
            <div class="row"><span class="label">Auto Mode:</span> <span id="auto_mode" class="val">-</span></div>
            <div class="row"><span class="label">eARC:</span> <span id="earc" class="val">-</span></div>
            <div class="row"><span class="label">Debug Log:</span> <span id="debug_log_status" class="val">-</span></div>
            <div class="row"><span class="label">Active Source:</span> <span id="active_source_status" class="val active-source-text">-</span></div>
        </div>
        
        <div class="card" style="grid-column: 1 / -1;">
            <h2>Controls</h2>
            <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px 30px;">
                <div class="row">
                    <span class="label">Power</span>
                    <div class="btn-group">
                        <button id="btn-power-1" onclick="sendCommand('power 1!')">On</button>
                    </div>
                </div>
                <div class="row">
                    <span class="label">Auto Switch</span>
                    <div class="btn-group">
                        <button id="btn-auto_switch-1" onclick="sendCommand('s auto switch 1!')">On</button>
                        <button id="btn-auto_switch-0" onclick="sendCommand('s auto switch 0!')">Off</button>
                    </div>
                </div>
                <div class="row">
                    <span class="label">Auto Mode</span>
                    <div class="btn-group" style="flex-wrap: nowrap;">
                        <button id="btn-auto_mode-1" onclick="sendCommand('s auto mode 1!')">1: 5V Mode</button>
                        <button id="btn-auto_mode-0" onclick="sendCommand('s auto mode 0!')">0: Clock Mode</button>
                    </div>
                </div>
                <div class="row">
                    <span class="label">eARC</span>
                    <div class="btn-group">
                        <button id="btn-earc-1" onclick="sendCommand('s earc 1!')">On</button>
                        <button id="btn-earc-0" onclick="sendCommand('s earc 0!')">Off</button>
                    </div>
                </div>
                <div class="row">
                    <span class="label">Debug Log</span>
                    <div class="btn-group">
                        <button id="btn-debug_log-1" onclick="sendCommand('s debug log 1!')">On</button>
                        <button id="btn-debug_log-0" onclick="sendCommand('s debug log 0!')">Off</button>
                    </div>
                </div>
                <div class="row">
                    <span class="label">System</span>
                    <div class="btn-group">
                        <button id="btn-system-reboot" class="btn-danger" onclick="sendCommand('reboot!')">Reboot</button>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="card" id="terminal-section" style="grid-column: 1 / -1; display: none;">
            <h2>Terminal</h2>
            <div id="terminal-output" style="background: #0f172a; padding: 15px; height: 250px; overflow-y: auto; font-family: monospace; border-radius: 6px; border: 1px solid rgba(255,255,255,0.1); margin-bottom: 15px; white-space: pre-wrap; font-size: 0.9rem; word-break: break-all;"></div>
            <div style="display: flex; gap: 10px;">
                <input type="text" id="terminal-input" placeholder="Enter custom command (e.g. r type!)" style="flex-grow: 1; padding: 10px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.2); background: #1e293b; color: white; font-family: monospace; outline: none;" onkeypress="if(event.key === 'Enter') sendCustomCommand()">
                <button onclick="sendCustomCommand()">Send</button>
            </div>
        </div>
    </div>
</div>

<script>
    function setBtnActive(group, val, isPoweredOff) {
        const btns = document.querySelectorAll(`[id^="btn-${group}-"]`);
        btns.forEach(btn => {
            btn.classList.remove('active');
            if (isPoweredOff && btn.id !== 'btn-power-1') {
                btn.disabled = true;
            } else {
                btn.disabled = false;
            }
        });
        
        if (isPoweredOff) return;
        
        if (val !== '' && val !== 'Unknown') {
            const activeBtn = document.getElementById(`btn-${group}-${val}`);
            if (activeBtn) {
                activeBtn.disabled = true;
                activeBtn.classList.add('active');
            }
        }
    }

    function updateStatus() {
        fetch('/api/status')
            .then(r => r.json())
            .then(data => {
                const isPoweredOff = data.power === 'off';
                
                document.getElementById('type').innerText = data.type;
                document.getElementById('fw_version').innerText = data.fw_version;
                document.getElementById('temperature').innerText = data.temperature !== 'Unknown' ? data.temperature + ' °C' : '-';
                document.getElementById('power').innerText = data.power.toUpperCase();
                document.getElementById('auto_switch').innerText = data.auto_switch.toUpperCase();
                
                let modeText = data.auto_mode;
                let modeVal = '';
                if (data.auto_mode !== 'Unknown') {
                    if (data.auto_mode.toLowerCase().includes('5v')) {
                        modeVal = '1';
                        modeText = '1: 5V Mode';
                    } else if (data.auto_mode.toLowerCase().includes('clock')) {
                        modeVal = '0';
                        modeText = '0: Clock Mode';
                    }
                }
                document.getElementById('auto_mode').innerText = modeText;
                
                document.getElementById('earc').innerText = data.earc.toUpperCase();
                document.getElementById('debug_log_status').innerText = data.debug_log.toUpperCase();
                document.getElementById('active_source_status').innerText = data.in_source !== 'Unknown' ? 'INPUT ' + data.in_source : '-';
                
                const rebootBtn = document.getElementById('btn-system-reboot');
                if (rebootBtn) rebootBtn.disabled = isPoweredOff;
                
                const deviceStatusCard = document.getElementById('device-status-card');
                if (isPoweredOff) {
                    deviceStatusCard.classList.add('off');
                } else {
                    deviceStatusCard.classList.remove('off');
                }
                
                const terminal = document.getElementById('terminal-section');
                if (data.debug_log === 'on') {
                    terminal.style.display = 'block';
                } else {
                    terminal.style.display = 'none';
                }
                
                // Set Button States
                setBtnActive('source', data.in_source, isPoweredOff);
                setBtnActive('power', data.power === 'on' ? '1' : (data.power === 'off' ? '0' : ''), isPoweredOff);
                setBtnActive('auto_switch', data.auto_switch === 'on' ? '1' : (data.auto_switch === 'off' ? '0' : ''), isPoweredOff);
                setBtnActive('auto_mode', modeVal, isPoweredOff);
                setBtnActive('earc', data.earc === 'on' ? '1' : (data.earc === 'off' ? '0' : ''), isPoweredOff);
                setBtnActive('debug_log', data.debug_log === 'on' ? '1' : (data.debug_log === 'off' ? '0' : ''), isPoweredOff);
            })
            .catch(e => console.error('Error fetching status:', e));
    }
    
    function sendCustomCommand() {
        const input = document.getElementById('terminal-input');
        const cmd = input.value.trim();
        if (cmd) {
            fetch('/api/terminal_command', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: cmd})
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    alert("Error: " + data.error);
                } else {
                    setTimeout(updateStatus, 800);
                }
            })
            .catch(e => console.error('Error sending custom command:', e));
            input.value = '';
        }
    }

    function sendCommand(cmd) {
        fetch('/api/command', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: cmd})
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert("Error: " + data.error);
            } else {
                setTimeout(updateStatus, 800); // Give device time to respond
            }
        })
        .catch(e => console.error('Error sending command:', e));
    }

    function updateLogs() {
        if (document.getElementById('terminal-section').style.display === 'none') return;
        fetch('/api/logs')
            .then(r => r.json())
            .then(logs => {
                const term = document.getElementById('terminal-output');
                const isScrolledToBottom = term.scrollHeight - term.clientHeight <= term.scrollTop + 2;
                term.innerHTML = logs.join('');
                if (isScrolledToBottom) {
                    term.scrollTop = term.scrollHeight;
                }
            })
            .catch(e => console.error('Error fetching logs:', e));
    }

    updateStatus();
    setInterval(updateStatus, 5000); // Poll every 5s
    setInterval(updateLogs, 1000); // Poll logs every 1s
</script>
</body>
</html>"""

def find_sw411_port():
    """Scans all active ports for the SW411 VID and PID"""
    for port in serial.tools.list_ports.comports():
        if port.vid == 0x1a86 and port.pid == 0x7523:
            return port.device
    return None

def parse_line(line):
    global state
    line_lower = line.lower()
    
    if "gsv chip temperature:" in line_lower:
        match = re.search(r"gsv chip temperature:\s*(\d+)", line_lower)
        if match:
            state["temperature"] = match.group(1)
    elif "mcu fw version:" in line_lower:
        state["fw_version"] = line_lower.split("version:")[-1].strip()
    elif "earc:" in line_lower:
        state["earc"] = line_lower.split("earc:")[-1].strip()
    elif "power on" in line_lower:
        state["power"] = "on"
    elif "power off" in line_lower:
        state["power"] = "off"
    elif "auto switch:" in line_lower:
        state["auto_switch"] = line_lower.split("auto switch:")[-1].strip()
    elif "auto switch mode:" in line_lower:
        state["auto_mode"] = line_lower.split("auto switch mode:")[-1].strip()
    elif "output->input" in line_lower:
        match = re.search(r"output->input(\d+)", line_lower)
        if match:
            state["in_source"] = match.group(1)
    elif "debug log on" in line_lower:
        state["debug_log"] = "on"
    elif "debug log off" in line_lower:
        state["debug_log"] = "off"
    elif "8k 4x1 earc hdmi switcher" in line_lower:
        state["type"] = line.strip()

def serial_worker(port, baudrate=115200):
    global state, command_queue
    try:
        ser = serial.Serial(port, baudrate, timeout=0.1)
        print(f"Successfully connected to {port}")
    except serial.SerialException as e:
        print(f"Error opening serial port: {e}")
        return

    # Initial boot sequence commands
    init_commands = [
        "s debug log 0!",
        "r type!", "r fw version!", "r power!", "r temperature!",
        "r auto switch!", "r auto mode!", "r earc!", "r in source!"
    ]
    
    for cmd in init_commands:
        ser.write(cmd.encode('utf-8'))
        time.sleep(0.2)
        
    time.sleep(0.5)
    while ser.in_waiting:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if line:
            parse_line(line)
            
    startup_complete = True
        
    last_temp_poll = time.time()

    while True:
        try:
            # Process queued commands from web UI
            while command_queue:
                cmd = command_queue.pop(0)
                ser.write(cmd.encode('utf-8'))
                time.sleep(0.1) 
            
            # Background polling for dynamic states every 5 seconds
            if time.time() - last_temp_poll > 5:
                if state.get("power") == "off":
                    poll_cmds = [b"r power!"]
                else:
                    poll_cmds = [b"r temperature!", b"r power!", b"r auto switch!", b"r auto mode!", b"r earc!", b"r in source!"]
                
                for poll_cmd in poll_cmds:
                    ser.write(poll_cmd)
                    time.sleep(0.1)
                last_temp_poll = time.time()
                
            # Read all available lines
            while ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    print(f"[Device] {line}")
                    if startup_complete:
                        device_logs.append(f'<div style="color: #f8fafc;">{html.escape(line)}</div>')
                    parse_line(line)
                    
            time.sleep(0.05)
        except Exception as e:
            print(f"Serial communication error: {e}")
            time.sleep(1)

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/status', methods=['GET'])
def get_status():
    return jsonify(state)

@app.route('/api/logs', methods=['GET'])
def get_logs():
    return jsonify(list(device_logs))

@app.route('/api/terminal_command', methods=['POST'])
def send_terminal_command():
    data = request.json
    if not data or 'command' not in data:
        return jsonify({"error": "No command provided"}), 400
        
    cmd = data['command']
    cmd = cmd.rstrip('!') + '!'
    device_logs.append(f'<div style="color: #38bdf8; font-weight: bold;">> {html.escape(cmd)}</div>')
    command_queue.append(cmd)
    
    return jsonify({"status": "queued"})

@app.route('/api/command', methods=['POST'])
def send_command():
    data = request.json
    if not data or 'command' not in data:
        return jsonify({"error": "No command provided"}), 400
        
    cmd = data['command']
    cmd_lower = cmd.lower()
    
    # Enforce constraints
    if "reset" in cmd_lower or "help" in cmd_lower or "hdcp" in cmd_lower:
        return jsonify({"error": "Command not permitted"}), 403
        
    # Ensure command ends with exactly one !
    cmd = cmd.rstrip('!') + '!'
    device_logs.append(f'<div style="color: #38bdf8; font-weight: bold;">> {html.escape(cmd)}</div>')
        
    command_queue.append(cmd)
    
    return jsonify({"status": "queued"})

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Feintech SW411 Web Remote")
    parser.add_argument('--port', type=str, default='/dev/ttyUSB0', help='Serial port for SW411 (used as fallback if auto-port fails)')
    parser.add_argument('--auto-port', action='store_true', help='Automatically detect the SW411 port based on VID 1a86 and PID 7523')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind the web server')
    parser.add_argument('--web-port', type=int, default=5000, help='Port to bind the web server')
    args = parser.parse_args()

    target_port = args.port

    # Execute auto-port logic if the flag is passed
    if args.auto_port:
        print("Auto-port mode enabled. Searching for SW411 (VID: 0x1a86, PID: 0x7523)...")
        detected_port = find_sw411_port()
        
        if detected_port:
            print(f"Success! Found SW411 on {detected_port}")
            target_port = detected_port
        else:
            print(f"Could not auto-detect SW411. Falling back to default port: {args.port}")

    # Start serial polling thread in background using the final target_port
    t = threading.Thread(target=serial_worker, args=(target_port,), daemon=True)
    t.start()

    print(f"Starting web server on http://{args.host}:{args.web_port}")
    app.run(host=args.host, port=args.web_port)
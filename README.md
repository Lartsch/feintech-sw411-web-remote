# feintech-sw411-web-remote
A basic Python web remote based on serial port commands.

## What is this?
A Python script that provides a web UI to remote control your Feintech SW411 (4x 8k HDMI switch with audio extractor and eARC/CEC support). The script communicates with the device via its serial port.

## Why use this?
- In my home theater setup, I got most devices in a cabinet. Among these are the SW411 and my homeserver.
- Because the SW411 remote is based on infrared, the cabinet blocks its signals.
- While the SW411 does the source switching mostly by itself, in my specific setup, there are cases where I need to manually switch the input source. And of course I dont want to move my butt from the couch to do that!
- That was the motivation to find a way to remote control the SW411 without the physical remote.

## How does it work?
- Simple Web UI with basically all features of the physical remote (and some more) is provided using `Flask`
- Serial communication is done via `pyserial`
- The serial commands were extracted from the SW411 firmware. These are not officially documented or supported.

## Features?
- See screenshots:
<img width="600" height="auto" alt="image" src="https://github.com/user-attachments/assets/c703b9a5-60c6-4b5d-84a4-0634900c866d" />
<img width="600" height="auto" alt="image" src="https://github.com/user-attachments/assets/0f89bc78-864d-4b6d-9b62-9fc133481d12" />

- Enabling the debug log feature will show an additional UI element providing the full log of data send to and received from the device. You can also enter your own commands there! Try `help!` for a start! You can also hit the "Start Recording" button and "Stop Recording" will instantly download a log file of the record:
<img width="600" height="auto" alt="image" src="https://github.com/user-attachments/assets/c7056b6e-b77b-47e0-b9a0-f5614f1f27af" />

- There is no automated polling. This was a conscious decision to prevent issues in timing etc. (for when the device is busy with other things, it could become confused when at the same time multiple read commands are emitted). It would be very easy to add though, if anyone needs it. I would not recommend it.
    - You can manually poll all states at once or single states by using the respective buttons. State is also updated when using some of the settings (debug mode, changing input source, etc).
    - A last-update-timestamp is shown for each state
    - The states are cached by the script as long as it runs. The cached values will be loaded on page load. If no values are cached on page load, it will show no value until states are updated.
- The tool could easily be enhanced to support control via smart home apps likes Google Home, for example using Sinric.
- **Unsupported features:**
    - The `power 0!` (power off) command is not supported in the UI. Why? Because apparently the implementation of this commands leads to some kind of loop and the device doesnt turn off, as can be seen when enabling debug logs.
    - I could not find any serial commands to change the audio mode. Therefore, this physical remote feature is not supported in the web remote.
    - The HDCP-related serial commands also seem to be dysfunctional. Not included therefore.


## How to use it?
- The script was tested on Linux (Debian) and Windows. Depending on your Windows installation, you might need to install additional dependencies. Was not needed in my case.
- Set up:
```
git clone https://github.com/Lartsch/feintech-sw411-web-remote
cd feintech-sw411-web-remote
python3 -m venv venv
source ./venv/bin/activate
pip install -r requirements.txt
```
- Connect your host to the SW411 USB-C port
- Run the tool:
```
python3 web_remote.py --auto-port
```
- The `--auto-port` flag will make the script auto-detect the correct port on Linux and Windows based on the VID/PID known to me.
- You can also use `--port` instead to specify the serial port yourself
- Use `--input-X` where is from range 1-4 to update the label for the corresponding input source. Defaults to "Unnamed #X"
    - No support to change / disable the input source icons as of now. Just change in source if you want to.
- The URL of the UI is shown at startup. You can also change the address/port.
- Use `--help` for more information:
    ```
    usage: web_remote.py [-h] [--port PORT] [--auto-port] [--host HOST] [--web-port WEB_PORT] [--input-1 INPUT_1]
                     [--input-2 INPUT_2] [--input-3 INPUT_3] [--input-4 INPUT_4]

    Feintech SW411 Simple Web Remote
    
    options:
      -h, --help           show this help message and exit
      --port PORT          Serial port
      --auto-port          Auto detect based on VID/PID
      --host HOST          Host to bind
      --web-port WEB_PORT  Port to bind
      --input-1 INPUT_1    Label for Input 1
      --input-2 INPUT_2    Label for Input 2
      --input-3 INPUT_3    Label for Input 3
      --input-4 INPUT_4    Label for Input 4
    ```
- This should preferably be setup as a system service (systemd/systemctl, Windows service, ...) and put it behind a reverse proxy like `nginx`

## Disclaimer and other notes
- This works pretty well but is experimental! Use it at your own risk.
- There *might* be a bug, either with the firmware OR the way the script affects it. It happens regularly that the SW411 looks like it is turned off (red LED) but the command `r power!` still reports it to be on. I don't know why this happens. It isn't really an issue though, as far as I can tell. 
- I have not tested this with ALL available firmware versions. I have tested it on versions 2.10.13 and 2.10.14.
- In my experience, you do not need to use the debug variant of the firmware for this to work.
- There is a bug in the firmware where in some setups, when using CEC and the PASS audio mode, after a CEC shutdown sequence, the SW411 and the device connected to the audio port turn on again. This does not happen when using any of the other audio modes and is unrelated to this tool.

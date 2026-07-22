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
- See screenshot:
<img width="600" height="auto" alt="image" src="https://github.com/user-attachments/assets/75c98f4b-0ae3-4175-a0b2-ffa41a7cb9ea" />

- Enabling the debug log feature will show an additional UI element providing the full log of data send to and received from the device. You can also enter your own commands there! Try `help!` for a start:
<img width="600" height="auto" alt="image" src="https://github.com/user-attachments/assets/f34bae7f-89df-439d-8500-5d3c49a5ee1b" />

- The state values are polled every 5s. If SW411 is detected as turned off, only the power state is polled.
- The `power 0!` (power off) command is not supported in the UI. Why? Because apparently the implementation of this commands leads to some kind of loop and the device doesnt turn off, as can be seen when enabling debug logs.
- It could easily be enhanced to support control via smart home apps likes Google Home.

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
- The URL of the UI is shown at startup. You can also change the address/port.
- Use `--help` for more information
- This should preferably be setup as a system service (systemd/systemctl, Windows service, ...) and put it behind a reverse proxy like `nginx`

## Disclaimer and other notes
- This works pretty well but is experimental! Use it at your own risk.
- There *might* be a bug, either with the firmware OR the way the script affects it. It happens regularly that the SW411 looks like it is turned off (red LED) but the command `r power!` still reports it to be on. I don't know why this happens. It isn't really an issue though, as far as I can tell. 
- I have not tested this with ALL available firmware versions. I have tested it on versions 2.10.13 and 2.10.14.
- In my experience, you do not need to use the debug variant of the firmware for this to work.
- There is a bug in the firmware where in some setups, when using CEC and the PASS audio mode, after a CEC shutdown sequence, the SW411 and the device connected to the audio port turn on again. This does not happen when using any of the other audio modes.

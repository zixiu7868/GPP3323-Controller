# GPP-3323 Controller

Windows Python GUI for controlling and monitoring a GW Instek GPP-3323 DC
power supply over LAN SCPI.

## Functions

- Connection tab: TCP test, connect/disconnect, `*IDN?` model validation and
  SCPI communication log
- Channel tab: voltage/current settings, setting readback, per-channel output
  control and emergency all-output-off
- Monitor tab: background voltage/current/power acquisition, live plots and
  UTF-8 CSV export
- Default endpoint: `10.0.0.123:1026`
- No output is enabled automatically at startup or connection time

## Start

Double-click `run.bat`. On first use it creates `.venv` and installs
Matplotlib. Python 3.11 or newer is recommended.

Alternatively:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Instrument setup

1. Confirm that the GPP-3323 has the optional LAN interface installed.
2. Configure its manual IP as `10.0.0.123` and ensure the PC is on the same
   subnet.
3. Connect with TCP port `1026`.
4. The application sends `*IDN?` and accepts the connection only when the
   response contains `GPP-3323`.

The program uses newline-terminated ASCII SCPI over a raw TCP socket. Only one
operation is sent at a time; the driver lock prevents GUI commands and monitor
queries from interleaving.

## Channel limits

| Channel | Voltage | Current setting | Measurement |
|---|---:|---:|---|
| CH1 | 0-32 V | 0-3 A | Voltage/current/power |
| CH2 | 0-32 V | 0-3 A | Voltage/current/power |
| CH3 | 1.8/2.5/3.3/5.0 V | Not programmable | Voltage is set value; current/power reported as zero |

Always confirm wiring, polarity, voltage, current limit and DUT rating before
turning an output on. Closing the application does not inherently guarantee
that hardware outputs are off; the application warns when its last known state
contains an active output and can send `ALLOUTOFF` before exit.

## CSV columns

```text
timestamp,elapsed_s,channel,voltage_V,current_A,power_W
```

CSV files are written with a UTF-8 BOM for convenient opening in Microsoft
Excel.

## Tests

Tests use a local fake instrument and never contact the real power supply:

```powershell
python -m unittest discover -v
```

## Key SCPI commands

```text
*IDN?
:SOURce1:VOLTage 5.000
:SOURce1:CURRent 0.5000
:OUTPut1 ON
:MEASure1:ALL?
ALLOUTOFF
```

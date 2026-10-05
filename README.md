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
- Electronic load settings tab: independent CH1/CH2 CV, CC and CR settings,
  synchronized settings, verified individual/shared input ON/OFF and live V/I/P
- Load Mode tab: single-channel load-test recording, automatic stop conditions,
  voltage/current/power plots and CSV export
- Automatic engineering units: values at or below 1 V/A are displayed using
  mV/mA on the charts for better readability
- Per-channel latest, minimum, maximum and average voltage/current statistics
- Default endpoint: `10.0.0.123:1026`
- No output is enabled automatically at startup or connection time
- CC 階梯測試 tab: start/end current, equally spaced step count (including both
  endpoints), dwell seconds and sample interval. Supports ascending/descending
  sweeps, voltage on the left axis and measured current on the right, CC setpoint
  overlay, actual transition markers, cursor readout, zoom and CSV export.
  Prepare CC mode with the external source disconnected, then connect the source
  and start. Completion, stop and acquisition errors attempt to turn input off.
  Each dwell starts after its current command; communication time can extend the
  total duration. This is a software-timed sweep, not a transient oscilloscope.

## Start

Use **顯示語言 / Language** at the top of the window to choose **繁體中文**
or **English**. The choice is saved in `config.json` and applies after restarting
the application. Changing this preference does not interrupt an active test.
UI labels, status messages, confirmations and validation errors are translated;
user notes, folder names, instrument replies and CSV column names are preserved.

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

## Electronic load mode

Use **電子負載設定 / Electronic load settings** to edit CH1 and CH2 independently.
Each channel has mode, setpoint, Apply, Read settings, ON and OFF controls.
Instrument readback is displayed separately from edited settings; unapplied
edits are marked and are preserved during the background refresh (every 2 s).
Live power uses measured V × I. Applying settings does not turn the load on.

Enable **同步設定 CH1 / CH2 / Synchronize CH1 / CH2 settings** and select a
settings source to edit one set of mode/value fields. Changes are mirrored to
the other channel; applying uses both channels. The CH1 → CH2 and CH2 → CH1
buttons also copy settings without requiring synchronization. Synchronization
does not link input ON/OFF: each input remains independently controllable.

**套用兩通道 / Apply both channels** validates both setpoints and checks mode
switches for terminal voltage before sending setting commands. Per-channel
results report readback verification and partial failures. **兩通道 ON / Both
channels ON** verifies both are in Load Mode before enabling them sequentially.
If enabling fails, newly enabled/attempted channels are turned back off and the
rollback result is reported; an input already ON before the operation is left
ON. **兩通道 OFF / Both channels OFF** attempts both channels even if one fails.
These commands are sequential, not simultaneous hardware triggers. Load OFF
does not turn off an active power-mode output; use Channel settings for that.

While Load Mode recording or a CC step test is running, the affected channel's
settings and ON control are locked; OFF remains available and stops the related
test. The other channel can still be configured independently when settings are
not synchronized. Shared Apply/ON and synchronization changes are locked during
a test. Load Mode retains its single-channel recorder: select CH1 or CH2 there
and configure the hardware in the new settings tab. Dual-channel test recording,
independent cutoff rules and separate test exports are not part of this change.

CH1 and CH2 can operate as an electronic load in CV (1.5-33 V), CC (0-3.2 A)
or CR (1-1000 ohm) mode. The single-channel overload protection is fixed at
50 W. Applying a load setting does not turn the load input on; enabling it is a
separate confirmed action. Verify polarity, wiring and the external source
limits before enabling the load input.

Before switching from power mode to load mode, the application measures the
selected terminal. If 0.1 V or more is present, no load-mode command is sent
and the user is asked to disconnect the external source first.

Load monitoring can stop after a selected test duration in minutes or when the measured
voltage falls to a selected cutoff voltage. Reaching either condition stops
the chart and turns the selected load input off. The elapsed test time remains
visible while sampling. Every 30 seconds, the application uses a linear trend
of all Vout samples collected in the current test to update the estimated total
elapsed time required to reach 2.0 V. If Vout is flat or rising, it reports that
an estimate is not available. In CC Load Mode, the same update also estimates the
total and remaining battery capacity to 2.0 V in mAh from the average measured
current. The display shows total time, remaining time, total capacity, remaining
capacity and the average current together so the values can be checked directly.
The application screenshot button captures the entire
GUI window and saves a PNG in `data` with a timestamp and three-digit sequence
number.

On connection, CH1/CH2 inputs that are already in Load Mode are forced off;
power-mode outputs are left unchanged. Starting a load chart verifies the
selected channel is in Load Mode and then turns its load input on automatically.

Always confirm wiring, polarity, voltage, current limit and DUT rating before
turning an output on. Closing the application does not inherently guarantee
that hardware outputs are off; the application warns when its last known state
contains an active output and can send `ALLOUTOFF` before exit.

## CSV columns

### Load Mode export and curve review

Load Mode and the **曲線 Review** tab provide Voltage / Current / Power
checkboxes. A single selected quantity fills the plot area; multiple quantities
use vertically stacked plots. The toolbar supports zoom, pan and restoring the
original view. Hiding a quantity does not stop its acquisition or CSV export.
Power readings below 1 W use mW; plots select one unit from the maximum absolute
power across the plotted data. CSV power values always remain in watts.

Live Load Mode power and monitoring power displays use measured voltage ×
measured current. Load power plots also default to this calculation. In Curve
Review, **功率來源 / Power source** switches between **計算值 (V×I)** and
**儀器讀回值 / Instrument readback**. Legacy CSV files are recalculated from
their voltage/current columns without modifying the files. Calculating power
provides finer numerical resolution but retains the voltage/current measurement
errors.

Load CSV export asks for a parent directory and an editable folder name inferred
from the notes. For example, notes containing `Toshiba`, `CR2032`, `#1`,
`15mA` and `1st run` produce a name such as
`Toshiba_CR2032_no1_15mA-1-20261001_143025`. English ordinals (`2nd run`,
`3rd run`, `10th run`) and Chinese counts (`第二次`, `第十次`) are supported.
Missing counts default to 1. Names are suggestions: review and edit them before
saving. The timestamp is the export time. Each folder contains `measurements.csv`
and the full original notes in `notes.txt`. Existing folders are preserved by
adding a numeric suffix. Export snapshots the available measurements when clicked.

In **曲線 Review**, choose a root directory, then Ctrl/Shift-select multiple
test folders or legacy CSV files. Subdirectories are discovered recursively;
CSV files directly in the root are listed individually. Each quantity has its
own graph, with test curves aligned to zero elapsed seconds. Channels are drawn
separately. Invalid CSV files are reported in the sidebar. Use **重新整理** to
discover newly exported files. Select one test folder and choose **重新命名選取的資料夾**
to rename the actual directory and update the list and legends. Duplicate and
invalid names are rejected. Review is available without connecting an instrument.

```text
timestamp,elapsed_s,channel,voltage_V,current_A,power_W,power_calculated_W
```

CSV files are written with a UTF-8 BOM for convenient opening in Microsoft
Excel. `power_W` retains the original instrument readback for compatibility;
`power_calculated_W` contains measured voltage × measured current. Both columns
are included in new Load Mode and Monitor exports, regardless of the Review
display selection.

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

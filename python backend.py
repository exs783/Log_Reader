"""
Pixhawk Log Reader - Flask Backend
Processes .BIN log files and serves analyzed data to the dashboard
Supports: Quad, Hex, Octo, X8, Tri, Y3, Y6, and all ArduCopter frame types
Enhanced with MAVExplorer-like analysis capabilities & proper vibration FFT analysis
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from pymavlink import mavutil
import traceback
import json
from scipy import signal, stats
from scipy.spatial.distance import euclidean

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'bin', 'log'}
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# ── ArduCopter frame type map ──────────────────────────────────────────────
FRAME_TYPES = {
    0:  ('QUAD',    4, 'X'),
    1:  ('QUAD',    4, '+'),
    2:  ('QUAD',    4, 'V'),
    3:  ('QUAD',    4, 'H'),
    4:  ('QUAD',    4, 'V'),
    5:  ('QUAD',    4, 'A'),
    10: ('TRI',     3, 'Y'),
    11: ('Y6',      6, 'Y6'),
    12: ('HEX',     6, 'X'),
    13: ('HEX',     6, '+'),
    14: ('Y6',      6, 'Y6B'),
    15: ('OCTO',    8, 'X'),
    16: ('OCTO',    8, '+'),
    17: ('OCTO',    8, 'V'),
    18: ('OCTO',    8, 'H'),
    19: ('X8',      8, 'X8'),
    20: ('OCTO',    8, '+'),
    21: ('DECA',   10, 'X'),
    22: ('DECA',   10, '+'),
    29: ('DECA',   10, 'H'),
    30: ('DODECA', 12, 'X'),
}

# Motor channel column patterns (MOT and RCOU message fields)
MOT_COLUMN_PATTERNS = [
    lambda cols: sorted([c for c in cols if c.startswith('Mot') and c[3:].isdigit()],
                        key=lambda x: int(x[3:])),
    lambda cols: sorted([c for c in cols if c.startswith('M') and c[1:].isdigit() and len(c) <= 3],
                        key=lambda x: int(x[1:])),
    lambda cols: sorted([c for c in cols if c.startswith('C') and c[1:].isdigit()],
                        key=lambda x: int(x[1:])),
]

# Vibration severity bands (ArduPilot thresholds)
VIBRATION_THRESHOLDS = {
    'green': 30,     # < 30 m/s² = good
    'yellow': 60,    # 30-60 m/s² = warning
    'red': float('inf')  # > 60 m/s² = critical
}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def detect_frame(dfs):
    """
    Detect frame type from PARM messages (FRAME_TYPE / FRAME_CLASS params).
    Falls back to counting active motor channels if params aren't present.
    Returns dict: { name, motor_count, layout, source }
    """
    frame_type_val  = None
    frame_class_val = None

    if 'PARM' in dfs:
        parm = dfs['PARM']
        name_col  = 'Name'  if 'Name'  in parm.columns else None
        value_col = 'Value' if 'Value' in parm.columns else None
        if name_col and value_col:
            for _, row in parm.iterrows():
                n = str(row[name_col]).strip().upper()
                if n == 'FRAME_TYPE':
                    try: frame_type_val  = int(float(row[value_col]))
                    except: pass
                elif n == 'FRAME_CLASS':
                    try: frame_class_val = int(float(row[value_col]))
                    except: pass

    CLASS_MAP = {1:4, 2:6, 3:8, 4:8, 5:6, 6:3, 7:1, 8:2, 9:2, 10:10, 12:12}

    if frame_class_val is not None and frame_class_val in CLASS_MAP:
        motor_count = CLASS_MAP[frame_class_val]
        layout = FRAME_TYPES.get(frame_type_val, ('UNKNOWN', motor_count, '?'))[2] if frame_type_val is not None else '?'
        name   = FRAME_TYPES.get(frame_type_val, (f'CLASS{frame_class_val}', motor_count, layout))[0]
        return {'name': name, 'motor_count': motor_count, 'layout': layout, 'source': 'PARM'}

    if frame_type_val is not None and frame_type_val in FRAME_TYPES:
        name, motor_count, layout = FRAME_TYPES[frame_type_val]
        return {'name': name, 'motor_count': motor_count, 'layout': layout, 'source': 'PARM'}

    for src in ['MOT', 'RCOU']:
        if src not in dfs:
            continue
        mot = dfs[src]
        motor_cols = _find_motor_cols(mot)
        if not motor_cols:
            continue
        active = []
        for col in motor_cols:
            mean_val = float(mot[col].mean())
            if mean_val > 1050 or (mean_val > 0.05 and mean_val < 10):
                active.append(col)
        n = len(active) if active else len(motor_cols)
        name = {3:'TRI', 4:'QUAD', 6:'HEX', 8:'OCTO', 10:'DECA', 12:'DODECA'}.get(n, f'{n}-MOTOR')
        return {'name': name, 'motor_count': n, 'layout': '?', 'source': 'channel_count'}

    return {'name': 'UNKNOWN', 'motor_count': 0, 'layout': '?', 'source': 'none'}


def _find_motor_cols(df):
    """Return motor output column names from a MOT or RCOU dataframe, sorted by index."""
    for pattern in MOT_COLUMN_PATTERNS:
        cols = pattern(df.columns.tolist())
        if cols:
            return cols
    return []


def load_log(filepath):
    """Load and parse Pixhawk .BIN log file."""
    print(f"Loading log file: {filepath}")
    mlog = mavutil.mavlink_connection(filepath)

    target_msgs = ['IMU', 'IMU2', 'IMU3', 'GYR', 'GYR2', 'GYR3', 'ACC', 'ACC2', 'ACC3',
                   'ATT', 'BAT', 'MOT', 'RATE', 'RCOU', 'GPS', 'PARM', 'AIRSPEED', 'BARO',
                   'EKF1', 'NKF1', 'MODE', 'MSG', 'ERR', 'MAG', 'MAG2', 'NKF2', 'XKF1', 'ESC', 'RPM']
    data = {msg: [] for msg in target_msgs}

    msg = mlog.recv_match(type=target_msgs, blocking=False)
    count = 0
    while msg:
        data[msg.get_type()].append(msg.to_dict())
        msg = mlog.recv_match(type=target_msgs, blocking=False)
        count += 1
        if count % 10000 == 0:
            print(f"Processed {count} messages...")

    print(f"Total messages processed: {count}")

    dfs = {}
    for msg_type, msg_list in data.items():
        if msg_list:
            dfs[msg_type] = pd.DataFrame(msg_list)
            print(f"  {msg_type}: {len(msg_list)} records")

    return dfs


def estimate_sample_rate(timestamps_us):
    """Estimate log sample rate from TimeUS differences (in Hz)."""
    if len(timestamps_us) < 2:
        return 100.0
    diffs = np.diff(timestamps_us)
    median_dt_us = np.median(diffs)
    if median_dt_us <= 0:
        return 100.0
    sample_rate = 1e6 / median_dt_us
    return float(sample_rate)


def downsample_for_plotting(timestamps, values, max_points=5000):
    """
    Downsample time-series data for frontend plotting while preserving peaks.
    Uses min/max envelope method to avoid hiding important spikes.
    """
    if len(values) <= max_points:
        return timestamps, values

    # Group data into bins
    n_bins = max_points
    bin_size = len(values) // n_bins

    downsampled_t = []
    downsampled_v = []

    for i in range(n_bins):
        start_idx = i * bin_size
        end_idx = min((i + 1) * bin_size, len(values))

        if start_idx >= len(values):
            break

        bin_vals = values[start_idx:end_idx]
        bin_ts = timestamps[start_idx:end_idx]

        # Use min and max within bin (envelope preservation)
        min_idx = np.argmin(bin_vals)
        max_idx = np.argmax(bin_vals)

        # Add both min and max points, sorted by timestamp
        points = [
            (bin_ts[min_idx], bin_vals[min_idx]),
            (bin_ts[max_idx], bin_vals[max_idx])
        ]
        points.sort(key=lambda x: x[0])

        for t, v in points:
            downsampled_t.append(t)
            downsampled_v.append(v)

    # Add final point
    if len(values) > 0:
        downsampled_t.append(timestamps[-1])
        downsampled_v.append(values[-1])

    return downsampled_t, downsampled_v


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS - IMU & VIBRATION
# ══════════════════════════════════════════════════════════════════════════════

def analyze_imu(dfs):
    """Analyze IMU data: accelerometer and gyroscope metrics."""
    try:
        if 'IMU' not in dfs:
            return {}
        imu = dfs['IMU']
        analysis = {
            'timestamps': imu['TimeUS'].tolist() if 'TimeUS' in imu else list(range(len(imu))),
            'accX': imu['AccX'].tolist() if 'AccX' in imu else [],
            'accY': imu['AccY'].tolist() if 'AccY' in imu else [],
            'accZ': imu['AccZ'].tolist() if 'AccZ' in imu else [],
            'gyrX':  imu['GyrX'].tolist() if 'GyrX' in imu else [],
            'gyrY':  imu['GyrY'].tolist() if 'GyrY' in imu else [],
            'gyrZ':  imu['GyrZ'].tolist() if 'GyrZ' in imu else [],
        }
        if analysis['accX']:
            acc_x = np.array(analysis['accX'])
            acc_y = np.array(analysis['accY'])
            acc_z = np.array(analysis['accZ'])
            gyr_x = np.array(analysis['gyrX'])
            gyr_y = np.array(analysis['gyrY'])
            gyr_z = np.array(analysis['gyrZ'])

            analysis['stats'] = {
                'accX_max': float(np.max(np.abs(acc_x))),
                'accY_max': float(np.max(np.abs(acc_y))),
                'accZ_max': float(np.max(np.abs(acc_z))),
                'accX_rms': float(np.sqrt(np.mean(np.square(acc_x)))),
                'accY_rms': float(np.sqrt(np.mean(np.square(acc_y)))),
                'accZ_rms': float(np.sqrt(np.mean(np.square(acc_z)))),
                'accX_mean': float(np.mean(acc_x)),
                'accY_mean': float(np.mean(acc_y)),
                'accZ_mean': float(np.mean(acc_z)),
                'gyrX_max': float(np.max(np.abs(gyr_x))),
                'gyrY_max': float(np.max(np.abs(gyr_y))),
                'gyrZ_max': float(np.max(np.abs(gyr_z))),
                'gyrX_rms': float(np.sqrt(np.mean(np.square(gyr_x)))),
                'gyrY_rms': float(np.sqrt(np.mean(np.square(gyr_y)))),
                'gyrZ_rms': float(np.sqrt(np.mean(np.square(gyr_z)))),
            }
        return analysis
    except Exception as e:
        print(f"Error analyzing IMU: {e}")
        traceback.print_exc()
        return {}


def analyze_vibration(dfs):
    """
    Analyze vibration using Welch's method PSD + proper FFT with real Hz frequencies.
    Returns frequency spectrum for frontend plotting + notch recommendations.
    """
    try:
        if 'IMU' not in dfs:
            return {}

        imu = dfs['IMU']
        if 'AccX' not in imu or 'AccY' not in imu or 'AccZ' not in imu:
            return {}

        acc_x = np.array(imu['AccX'].values)
        acc_y = np.array(imu['AccY'].values)
        acc_z = np.array(imu['AccZ'].values)
        timestamps_us = np.array(imu['TimeUS'].values) if 'TimeUS' in imu else np.arange(len(acc_x))

        # Calculate vibration magnitude (resultant acceleration)
        vibration_mag = np.sqrt(acc_x**2 + acc_y**2 + acc_z**2)

        # Downsample time-domain data for plotting (5000 points max)
        ts_plot, mag_plot = downsample_for_plotting(timestamps_us.tolist(), vibration_mag.tolist(), max_points=5000)

        # Estimate actual sample rate in Hz from TimeUS differences
        sample_rate = estimate_sample_rate(timestamps_us)
        print(f"  Vibration analysis: estimated sample rate = {sample_rate:.1f} Hz")

        # Compute Welch PSD (smoother, more robust than raw FFT)
        # nperseg should be ~1-2 seconds of data
        nperseg = min(int(sample_rate * 2), len(acc_z) // 4)
        nperseg = max(256, nperseg)

        freqs_x, psd_x = signal.welch(acc_x, fs=sample_rate, nperseg=nperseg)
        freqs_y, psd_y = signal.welch(acc_y, fs=sample_rate, nperseg=nperseg)
        freqs_z, psd_z = signal.welch(acc_z, fs=sample_rate, nperseg=nperseg)

        # Also compute raw FFT for peak finding
        fft_x = np.abs(np.fft.rfft(acc_x))
        fft_y = np.abs(np.fft.rfft(acc_y))
        fft_z = np.abs(np.fft.rfft(acc_z))
        fft_freqs = np.fft.rfftfreq(len(acc_x), d=1.0/sample_rate)

        # Find dominant frequencies in the motor harmonic range (20–500 Hz typical)
        motor_range_mask = (fft_freqs >= 20) & (fft_freqs <= 500)
        if np.any(motor_range_mask):
            dom_idx_x = np.argmax(fft_x[motor_range_mask]) + np.argmax(fft_x[:motor_range_mask.argmax()+1] == 0) if np.any(fft_x[motor_range_mask]) else 0
            dom_freq_x = float(fft_freqs[motor_range_mask][dom_idx_x]) if np.any(motor_range_mask) else 0.0

            # Cleaner approach: just find max in range
            motor_fft_x = fft_x[motor_range_mask]
            motor_fft_y = fft_y[motor_range_mask]
            motor_fft_z = fft_z[motor_range_mask]

            if len(motor_fft_x) > 0:
                dom_freq_x = float(fft_freqs[motor_range_mask][np.argmax(motor_fft_x)])
                dom_freq_y = float(fft_freqs[motor_range_mask][np.argmax(motor_fft_y)])
                dom_freq_z = float(fft_freqs[motor_range_mask][np.argmax(motor_fft_z)])
            else:
                dom_freq_x = dom_freq_y = dom_freq_z = 0.0
        else:
            dom_freq_x = dom_freq_y = dom_freq_z = 0.0

        # Vibration severity classification
        vib_rms = float(np.sqrt(np.mean(np.square(vibration_mag))))
        if vib_rms < VIBRATION_THRESHOLDS['green']:
            vib_severity = 'GOOD'
        elif vib_rms < VIBRATION_THRESHOLDS['yellow']:
            vib_severity = 'ELEVATED'
        else:
            vib_severity = 'CRITICAL'

        analysis = {
            'timestamps': ts_plot,
            'vibration_magnitude': mag_plot,
            'psd': {
                'frequencies': freqs_z.tolist(),  # Use Z-axis as primary (gravity reference)
                'psd_x': np.sqrt(psd_x).tolist(),  # Convert PSD to acceleration RMS for easier interpretation
                'psd_y': np.sqrt(psd_y).tolist(),
                'psd_z': np.sqrt(psd_z).tolist(),
                'sample_rate': float(sample_rate),
            },
            'fft': {
                'frequencies': fft_freqs.tolist(),
                'magnitude_x': fft_x.tolist(),
                'magnitude_y': fft_y.tolist(),
                'magnitude_z': fft_z.tolist(),
            },
            'stats': {
                'vibration_max': float(np.max(vibration_mag)),
                'vibration_mean': float(np.mean(vibration_mag)),
                'vibration_rms': vib_rms,
                'dominant_freq_x': dom_freq_x,
                'dominant_freq_y': dom_freq_y,
                'dominant_freq_z': dom_freq_z,
                'severity': vib_severity,
                'sample_rate': float(sample_rate),
                'notch_freq_suggestion': dom_freq_z if dom_freq_z > 0 else None,
            }
        }
        return analysis
    except Exception as e:
        print(f"Error analyzing vibration: {e}")
        traceback.print_exc()
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS - ATTITUDE & STABILITY
# ══════════════════════════════════════════════════════════════════════════════

def analyze_attitude(dfs):
    """Analyze attitude control: roll, pitch, yaw errors."""
    try:
        if 'ATT' not in dfs:
            return {}
        att = dfs['ATT']

        # Downsample for plotting
        timestamps = att['TimeUS'].tolist() if 'TimeUS' in att else list(range(len(att)))

        analysis = {
            'timestamps': timestamps,
            'desRoll':  att['DesRoll'].tolist()  if 'DesRoll'  in att else [],
            'roll':     att['Roll'].tolist()      if 'Roll'     in att else [],
            'desPitch': att['DesPitch'].tolist()  if 'DesPitch' in att else [],
            'pitch':    att['Pitch'].tolist()     if 'Pitch'    in att else [],
            'desYaw':   att['DesYaw'].tolist()    if 'DesYaw'   in att else [],
            'yaw':      att['Yaw'].tolist()       if 'Yaw'      in att else [],
        }
        if analysis['roll']:
            re = np.array(analysis['roll'])  - np.array(analysis['desRoll'])
            pe = np.array(analysis['pitch']) - np.array(analysis['desPitch'])
            ye = np.array(analysis['yaw'])   - np.array(analysis['desYaw'])

            analysis['stats'] = {
                'roll_error_rms':   float(np.sqrt(np.mean(np.square(re)))),
                'pitch_error_rms':  float(np.sqrt(np.mean(np.square(pe)))),
                'yaw_error_rms':    float(np.sqrt(np.mean(np.square(ye)))),
                'roll_error_max':   float(np.max(np.abs(re))),
                'pitch_error_max':  float(np.max(np.abs(pe))),
                'yaw_error_max':    float(np.max(np.abs(ye))),
                'roll_error_mean':  float(np.mean(re)),
                'pitch_error_mean': float(np.mean(pe)),
                'yaw_error_mean':   float(np.mean(ye)),
            }
        return analysis
    except Exception as e:
        print(f"Error analyzing attitude: {e}")
        traceback.print_exc()
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS - BATTERY & POWER
# ══════════════════════════════════════════════════════════════════════════════

def analyze_battery(dfs):
    """Analyze battery voltage, current, and power consumption."""
    try:
        if 'BAT' not in dfs:
            return {}
        bat = dfs['BAT']

        timestamps = bat['TimeUS'].tolist() if 'TimeUS' in bat else list(range(len(bat)))
        voltage = bat['Volt'].tolist() if 'Volt' in bat else []
        current = bat['Curr'].tolist() if 'Curr' in bat else []

        # Downsample for plotting
        ts_plot, v_plot = downsample_for_plotting(timestamps, voltage, max_points=5000) if voltage else (timestamps, voltage)
        _, i_plot = downsample_for_plotting(timestamps, current, max_points=5000) if current else (timestamps, current)

        analysis = {
            'timestamps': ts_plot,
            'voltage': v_plot,
            'current': i_plot,
        }
        if voltage and current:
            voltage_arr = np.array(voltage)
            current_arr = np.array(current)
            power = voltage_arr * current_arr

            analysis['stats'] = {
                'voltage_min':  float(np.min(voltage_arr)),
                'voltage_max':  float(np.max(voltage_arr)),
                'voltage_mean': float(np.mean(voltage_arr)),
                'current_max':  float(np.max(current_arr)),
                'current_mean': float(np.mean(current_arr)),
                'power_max':    float(np.max(power)),
                'power_avg':    float(np.mean(power)),
                'power_total':  float(np.sum(power)),
                'voltage_sag':  float(np.max(voltage_arr) - np.min(voltage_arr)),
            }
        return analysis
    except Exception as e:
        print(f"Error analyzing battery: {e}")
        traceback.print_exc()
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS - MOTORS & MOTOR BALANCE
# ══════════════════════════════════════════════════════════════════════════════

def analyze_motors(dfs, frame_info):
    """
    Dynamically detect and return ALL motor channels for any frame type.
    Includes detailed motor balance and sync analysis.
    """
    try:
        motor_source = None
        for src in ['MOT', 'RCOU']:
            if src in dfs:
                motor_source = src
                break
        if not motor_source:
            return {}

        mot = dfs[motor_source]
        motor_cols = _find_motor_cols(mot)
        if not motor_cols:
            return {}

        expected = frame_info.get('motor_count', 0)

        if expected > 0 and len(motor_cols) > expected:
            active_cols = []
            for col in motor_cols:
                mean_val = float(mot[col].mean())
                if mean_val > 1050 or (0.05 < mean_val < 10):
                    active_cols.append(col)
            if len(active_cols) == expected:
                motor_cols = active_cols
            else:
                motor_cols = motor_cols[:expected]

        n = len(motor_cols)
        timestamps = mot['TimeUS'].tolist() if 'TimeUS' in mot else list(range(len(mot)))

        analysis = {
            'timestamps':   timestamps,
            'motor_count':  n,
            'frame_name':   frame_info.get('name', 'UNKNOWN'),
            'frame_layout': frame_info.get('layout', '?'),
            'motors': {}
        }

        motor_data_cols = []
        motor_arrays = {}
        for i, col in enumerate(motor_cols):
            key = f'motor{i + 1}'
            vals = mot[col].tolist()
            motor_arrays[key] = np.array(vals)
            analysis[key] = vals
            analysis['motors'][key] = {
                'channel': col,
                'values':  vals,
                'mean':    float(np.mean(mot[col])),
                'max':     float(np.max(mot[col])),
                'min':     float(np.min(mot[col])),
                'std':     float(np.std(mot[col])),
            }
            motor_data_cols.append(col)

        # Motor balance and sync statistics
        motor_df = mot[motor_data_cols]
        means    = motor_df.mean()
        overall_mean = float(means.mean())
        imbalance_pct = float((means.max() - means.min()) / overall_mean * 100) if overall_mean > 0 else 0.0

        # Cross-correlation analysis for sync
        sync_errors = []
        if n > 1:
            for i in range(n - 1):
                for j in range(i + 1, n):
                    m1 = motor_arrays[f'motor{i+1}']
                    m2 = motor_arrays[f'motor{j+1}']
                    # Normalized cross-correlation
                    corr = np.corrcoef(m1, m2)[0, 1]
                    sync_errors.append(float(corr))

        avg_correlation = float(np.mean(sync_errors)) if sync_errors else 1.0

        analysis['stats'] = {
            'motor_count':     n,
            'frame_name':      frame_info.get('name', 'UNKNOWN'),
            'source':          motor_source,
            'avg_outputs':     {f'motor{i+1}': float(means[col]) for i, col in enumerate(motor_data_cols)},
            'max_outputs':     {f'motor{i+1}': float(mot[col].max()) for i, col in enumerate(motor_data_cols)},
            'min_outputs':     {f'motor{i+1}': float(mot[col].min()) for i, col in enumerate(motor_data_cols)},
            'imbalance_pct':   imbalance_pct,
            'sync_error':      float(np.std(motor_df.mean(axis=1))),
            'motor_correlation': avg_correlation,
        }

        print(f"  Motors: detected {n} channels from {motor_source} "
              f"(frame={frame_info.get('name','?')}, expected={expected})")
        return analysis

    except Exception as e:
        print(f"Error analyzing motors: {e}")
        traceback.print_exc()
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS - RATE CONTROLLER & PID TUNING
# ══════════════════════════════════════════════════════════════════════════════

def analyze_rate_controller(dfs):
    """Analyze rate controller performance: desired vs actual rates."""
    try:
        if 'RATE' not in dfs:
            return {}
        rate = dfs['RATE']

        timestamps = rate['TimeUS'].tolist() if 'TimeUS' in rate else list(range(len(rate)))

        analysis = {
            'timestamps': timestamps,
            'desRoll':  rate['RDes'].tolist() if 'RDes' in rate else [],
            'roll':     rate['R'].tolist()    if 'R'    in rate else [],
            'desPitch': rate['PDes'].tolist() if 'PDes' in rate else [],
            'pitch':    rate['P'].tolist()    if 'P'    in rate else [],
            'desYaw':   rate['YDes'].tolist() if 'YDes' in rate else [],
            'yaw':      rate['Y'].tolist()    if 'Y'    in rate else [],
        }
        if analysis['roll']:
            re = np.array(analysis['roll'])  - np.array(analysis['desRoll'])
            pe = np.array(analysis['pitch']) - np.array(analysis['desPitch'])
            ye = np.array(analysis['yaw'])   - np.array(analysis['desYaw'])

            analysis['stats'] = {
                'roll_rate_error_rms':   float(np.sqrt(np.mean(np.square(re)))),
                'pitch_rate_error_rms':  float(np.sqrt(np.mean(np.square(pe)))),
                'yaw_rate_error_rms':    float(np.sqrt(np.mean(np.square(ye)))),
                'roll_rate_error_max':   float(np.max(np.abs(re))),
                'pitch_rate_error_max':  float(np.max(np.abs(pe))),
                'yaw_rate_error_max':    float(np.max(np.abs(ye))),
                'roll_rate_error_mean':  float(np.mean(re)),
                'pitch_rate_error_mean': float(np.mean(pe)),
                'yaw_rate_error_mean':   float(np.mean(ye)),
            }
        return analysis
    except Exception as e:
        print(f"Error analyzing rate controller: {e}")
        traceback.print_exc()
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS - GPS & NAVIGATION
# ══════════════════════════════════════════════════════════════════════════════

def analyze_gps(dfs):
    """Analyze GPS data: position, velocity, satellites, accuracy."""
    try:
        if 'GPS' not in dfs:
            return {}
        gps = dfs['GPS']

        analysis = {
            'timestamps': gps['TimeUS'].tolist() if 'TimeUS' in gps else list(range(len(gps))),
        }

        # Extract GPS fields
        if 'Lat' in gps and 'Lng' in gps:
            analysis['latitude'] = gps['Lat'].tolist()
            analysis['longitude'] = gps['Lng'].tolist()

        if 'Alt' in gps:
            analysis['altitude'] = gps['Alt'].tolist()

        if 'Spd' in gps:
            analysis['speed'] = gps['Spd'].tolist()

        if 'NSats' in gps:
            analysis['num_satellites'] = gps['NSats'].tolist()

        if 'HDop' in gps:
            analysis['hdop'] = gps['HDop'].tolist()

        if 'VDop' in gps:
            analysis['vdop'] = gps['VDop'].tolist()

        # Calculate stats
        if 'Lat' in gps and 'Lng' in gps and 'Alt' in gps:
            lat = np.array(gps['Lat'].values)
            lng = np.array(gps['Lng'].values)
            alt = np.array(gps['Alt'].values)

            # Calculate position changes
            lat_diff = np.diff(lat)
            lng_diff = np.diff(lng)
            alt_diff = np.diff(alt)

            # Approximate distance in meters (rough conversion at equator)
            lat_dist = lat_diff * 111000
            lng_dist = lng_diff * 111000 * np.cos(np.radians(np.mean(lat)))

            analysis['stats'] = {
                'lat_min': float(np.min(lat)),
                'lat_max': float(np.max(lat)),
                'lng_min': float(np.min(lng)),
                'lng_max': float(np.max(lng)),
                'alt_min': float(np.min(alt)),
                'alt_max': float(np.max(alt)),
                'alt_gain': float(np.max(alt) - np.min(alt)),
            }

            if 'NSats' in gps:
                analysis['stats']['avg_satellites'] = float(np.mean(gps['NSats']))
                analysis['stats']['min_satellites'] = int(np.min(gps['NSats']))

        return analysis
    except Exception as e:
        print(f"Error analyzing GPS: {e}")
        traceback.print_exc()
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS - BAROMETER & ALTITUDE HOLD
# ══════════════════════════════════════════════════════════════════════════════

def analyze_barometer(dfs):
    """Analyze barometer data: altitude hold performance."""
    try:
        if 'BARO' not in dfs:
            return {}
        baro = dfs['BARO']

        timestamps = baro['TimeUS'].tolist() if 'TimeUS' in baro else list(range(len(baro)))

        analysis = {
            'timestamps': timestamps,
        }

        if 'Alt' in baro:
            altitude = baro['Alt'].tolist()
            # Downsample for plotting
            ts_plot, alt_plot = downsample_for_plotting(timestamps, altitude, max_points=5000)
            analysis['altitude'] = alt_plot

            alt = np.array(baro['Alt'].values)
            analysis['stats'] = {
                'alt_min': float(np.min(alt)),
                'alt_max': float(np.max(alt)),
                'alt_mean': float(np.mean(alt)),
                'alt_std': float(np.std(alt)),
                'alt_range': float(np.max(alt) - np.min(alt)),
            }

        if 'Press' in baro:
            analysis['pressure'] = baro['Press'].tolist()

        if 'Temp' in baro:
            analysis['temperature'] = baro['Temp'].tolist()

        return analysis
    except Exception as e:
        print(f"Error analyzing barometer: {e}")
        traceback.print_exc()
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS - EVENTS & MODE CHANGES
# ══════════════════════════════════════════════════════════════════════════════

def analyze_events(dfs):
    """Extract and analyze flight events: mode changes, errors, failsafes."""
    try:
        events = []

        # MODE changes
        if 'MODE' in dfs:
            mode = dfs['MODE']
            if 'Mode' in mode and 'TimeUS' in mode:
                for i, (time, m) in enumerate(zip(mode['TimeUS'], mode['Mode'])):
                    events.append({
                        'timestamp': float(time),
                        'type': 'MODE_CHANGE',
                        'value': str(m),
                        'index': i,
                    })

        # ERR messages (errors)
        if 'ERR' in dfs:
            err = dfs['ERR']
            if 'TimeUS' in err and 'Subsys' in err and 'ECode' in err:
                for time, subsys, ecode in zip(err['TimeUS'], err['Subsys'], err['ECode']):
                    events.append({
                        'timestamp': float(time),
                        'type': 'ERROR',
                        'subsystem': int(subsys),
                        'error_code': int(ecode),
                    })

        # MSG messages (generic messages)
        if 'MSG' in dfs:
            msg = dfs['MSG']
            if 'TimeUS' in msg and 'Message' in msg:
                for time, message in zip(msg['TimeUS'], msg['Message']):
                    if 'failsafe' in str(message).lower():
                        events.append({
                            'timestamp': float(time),
                            'type': 'FAILSAFE',
                            'message': str(message),
                        })

        # Sort by timestamp
        events = sorted(events, key=lambda x: x['timestamp'])

        return {
            'events': events,
            'event_count': len(events),
            'event_types': list(set([e['type'] for e in events])),
        }
    except Exception as e:
        print(f"Error analyzing events: {e}")
        traceback.print_exc()
        return {'events': [], 'event_count': 0, 'event_types': []}


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS - MAGNETOMETER & COMPASS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_magnetometer(dfs):
    """Analyze magnetometer data for compass health and interference."""
    try:
        if 'MAG' not in dfs:
            return {}

        mag = dfs['MAG']
        analysis = {
            'timestamps': mag['TimeUS'].tolist() if 'TimeUS' in mag else list(range(len(mag))),
        }

        fields = ['MagX', 'MagY', 'MagZ']
        for field in fields:
            if field in mag:
                analysis[field.lower()] = mag[field].tolist()

        if 'MagX' in mag and 'MagY' in mag and 'MagZ' in mag:
            mag_x = np.array(mag['MagX'].values)
            mag_y = np.array(mag['MagY'].values)
            mag_z = np.array(mag['MagZ'].values)

            # Calculate field magnitude
            mag_mag = np.sqrt(mag_x**2 + mag_y**2 + mag_z**2)

            analysis['magnitude'] = mag_mag.tolist()
            analysis['stats'] = {
                'magx_mean': float(np.mean(mag_x)),
                'magy_mean': float(np.mean(mag_y)),
                'magz_mean': float(np.mean(mag_z)),
                'magnitude_mean': float(np.mean(mag_mag)),
                'magnitude_std': float(np.std(mag_mag)),
                'interference_level': 'LOW' if np.std(mag_mag) < 50 else ('MEDIUM' if np.std(mag_mag) < 100 else 'HIGH'),
            }

        return analysis
    except Exception as e:
        print(f"Error analyzing magnetometer: {e}")
        traceback.print_exc()
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY & SCORING
# ══════════════════════════════════════════════════════════════════════════════

def generate_summary(dfs, analyses, frame_info):
    """Generate overall flight quality score and summary."""
    try:
        summary = {
            'timestamp':       datetime.now().isoformat(),
            'messages_loaded': {t: len(df) for t, df in dfs.items()},
            'frame':           frame_info,
        }

        score = 100
        issues = []

        # IMU check (ArduPilot threshold ~15 m/s²)
        if 'imu' in analyses and 'stats' in analyses['imu']:
            s = analyses['imu']['stats']
            if s['accX_rms'] > 15:
                score -= 10
                issues.append('High X-axis acceleration noise')
            if s['accY_rms'] > 15:
                score -= 10
                issues.append('High Y-axis acceleration noise')
            if s['accZ_rms'] > 15:
                score -= 10
                issues.append('High Z-axis acceleration noise')

        # Vibration check
        if 'vibration' in analyses and 'stats' in analyses['vibration']:
            s = analyses['vibration']['stats']
            if s['vibration_rms'] > 60:
                score -= 20
                issues.append('Critical vibration detected')
            elif s['vibration_rms'] > 30:
                score -= 10
                issues.append('Elevated vibration levels')

        # Attitude check
        if 'attitude' in analyses and 'stats' in analyses['attitude']:
            s = analyses['attitude']['stats']
            if s['roll_error_rms']  > 5:
                score -= 5
                issues.append('Poor roll stability')
            if s['pitch_error_rms'] > 5:
                score -= 5
                issues.append('Poor pitch stability')
            if s['yaw_error_rms']   > 5:
                score -= 5
                issues.append('Poor yaw stability')

        # Battery check
        if 'battery' in analyses and 'stats' in analyses['battery']:
            s = analyses['battery']['stats']
            if s['voltage_sag'] > 2:
                score -= 10
                issues.append('High voltage sag detected')
            if s['voltage_min'] < 10:
                score -= 15
                issues.append('Low battery voltage')

        # Motor check
        if 'motors' in analyses and 'stats' in analyses['motors']:
            s = analyses['motors']['stats']
            if s.get('imbalance_pct', 0) > 15:
                score -= 10
                issues.append('High motor imbalance')
            if s.get('sync_error', 0) > 5:
                score -= 5
                issues.append('Motor sync issues')
            if s.get('motor_correlation', 0) < 0.85:
                score -= 5
                issues.append('Poor motor correlation')

        # Rate controller check
        if 'pid' in analyses and 'stats' in analyses['pid']:
            s = analyses['pid']['stats']
            if s['roll_rate_error_rms']  > 10:
                score -= 5
                issues.append('High roll rate error')
            if s['pitch_rate_error_rms'] > 10:
                score -= 5
                issues.append('High pitch rate error')

        # Event check
        if 'events' in analyses:
            if analyses['events']['event_count'] > 5:
                score -= 10
                issues.append(f"Multiple events detected ({analyses['events']['event_count']})")

        summary['flight_quality_score'] = max(0, int(score))
        summary['issues'] = issues
        summary['verdict'] = (
            'Excellent' if score >= 90 else
            'Good' if score >= 75 else
            'Fair' if score >= 60 else
            'Poor' if score >= 40 else
            'Critical'
        )

        return summary
    except Exception as e:
        print(f"Error generating summary: {e}")
        traceback.print_exc()
        return {'flight_quality_score': 0, 'issues': ['Analysis error'], 'verdict': 'Unknown'}


def run_analysis(filepath, filename):
    """Run complete analysis on log file."""
    dfs       = load_log(filepath)
    frame     = detect_frame(dfs)
    print(f"  Frame detected: {frame}")

    analyses  = {
        'imu':         analyze_imu(dfs),
        'vibration':   analyze_vibration(dfs),
        'attitude':    analyze_attitude(dfs),
        'battery':     analyze_battery(dfs),
        'motors':      analyze_motors(dfs, frame),
        'pid':         analyze_rate_controller(dfs),
        'gps':         analyze_gps(dfs),
        'barometer':   analyze_barometer(dfs),
        'events':      analyze_events(dfs),
        'magnetometer': analyze_magnetometer(dfs),
    }

    summary = generate_summary(dfs, analyses, frame)

    return {
        'success':  True,
        'filename': filename,
        'summary':  summary,
        'analyses': analyses,
        'frame':    frame,
    }


# ══════════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(base_dir, 'log_dashboard.html')


@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type. Allowed: .bin, .log'}), 400
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filepath)
        print(f"File uploaded: {filepath}")
        return jsonify(run_analysis(filepath, file.filename)), 200
    except Exception as e:
        print(f"Upload error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/analyze/<filename>', methods=['GET'])
def analyze_file(filename):
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        return jsonify(run_analysis(filepath, filename)), 200
    except Exception as e:
        print(f"Analysis error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/files', methods=['GET'])
def list_files():
    try:
        files = []
        for f in os.listdir(app.config['UPLOAD_FOLDER']):
            if allowed_file(f):
                fp = os.path.join(app.config['UPLOAD_FOLDER'], f)
                st = os.stat(fp)
                files.append({'name': f, 'size': st.st_size,
                               'modified': datetime.fromtimestamp(st.st_mtime).isoformat()})
        return jsonify({'success': True,
                        'files': sorted(files, key=lambda x: x['modified'], reverse=True)}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()}), 200


@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File too large. Maximum size: 500MB'}), 413


if __name__ == '__main__':
    print("Starting Pixhawk Log Reader Backend...")
    print(f"Upload folder: {os.path.abspath(app.config['UPLOAD_FOLDER'])}")
    app.run(debug=True, host='0.0.0.0', port=5001)

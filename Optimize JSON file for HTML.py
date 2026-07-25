from pymavlink import mavutil
import pandas as pd
import numpy as np
import json

# 1. Load ArduPilot log
log_path = '/Users/ethansimon/Desktop/Flight_Logs/00000005.BIN'
print(f"Parsing log file: {log_path}...")
mlog = mavutil.mavlink_connection(log_path)

target_msgs = ['POS', 'GPS', 'ATT', 'BAT']
data = {msg: [] for msg in target_msgs}

while True:
    msg = mlog.recv_match(type=target_msgs, blocking=False)
    if msg is None:
        break
    data[msg.get_type()].append(msg.to_dict())

dfs = {k: pd.DataFrame(v) for k, v in data.items() if data[k]}
print("Loaded tables:", list(dfs.keys()))

# 2. Standardize timelines to TimeUS (Microseconds)
for key in dfs.keys():
    if 'TimeUS' in dfs[key].columns:
        dfs[key] = dfs[key].sort_values('TimeUS')
    elif 'TimeMS' in dfs[key].columns:
        dfs[key]['TimeUS'] = dfs[key]['TimeMS'] * 1000
        dfs[key] = dfs[key].sort_values('TimeUS')

# Use POS or GPS as the master geospatial timeline
master_key = 'POS' if 'POS' in dfs else 'GPS'
master_df = dfs[master_key].copy()

raw_points = []
print("Synchronizing telemetry fields...")

for _, row in master_df.iterrows():
    ts = row['TimeUS']

    # Extract position coordinates
    lat = row.get('Lat', 0)
    lng = row.get('Lng', row.get('Lon', 0))
    latF = lat / 1e7 if abs(lat) > 1000 else lat
    lngF = lng / 1e7 if abs(lng) > 1000 else lng

    if latF == 0 and lngF == 0:
        continue  # Skip uninitialized GPS spikes

    alt = row.get('RelAlt', row.get('RelativeAlt', row.get('RelHomeAlt', row.get('Alt', 0))))
    spd = row.get('Spd', row.get('GndSpd', 0))

    # Sync closest Attitude (Angles in degrees)
    roll, pitch, yaw = 0.0, 0.0, 0.0
    if 'ATT' in dfs:
        idx = (dfs['ATT']['TimeUS'] - ts).abs().idxmin()
        closest_att = dfs['ATT'].loc[idx]
        roll = float(closest_att.get('Roll', 0))
        pitch = float(closest_att.get('Pitch', 0))
        yaw = float(closest_att.get('Yaw', 0))

    # Sync closest Battery stats
    volt, curr = 0.0, 0.0
    if 'BAT' in dfs:
        idx = (dfs['BAT']['TimeUS'] - ts).abs().idxmin()
        closest_bat = dfs['BAT'].loc[idx]
        volt = float(closest_bat.get('Volt', 0))
        curr = float(closest_bat.get('Curr', 0))

    raw_points.append({
        "lat": float(latF), "lng": float(lngF), "alt": float(alt),
        "spd": float(spd), "ts": int(ts),
        "roll": roll, "pitch": pitch, "yaw": yaw,
        "volt": volt, "curr": curr
    })

# 3. Calculate Local 3D Coordinates (X, Y, Z) directly in Python
print("Calculating local 3D track coordinates...")
lats = [p['lat'] for p in raw_points]
lngs = [p['lng'] for p in raw_points]
lat0 = sum(lats) / len(lats)
lng0 = sum(lngs) / len(lngs)
R = 6371000  # Radius of Earth

final_points = []
for p in raw_points:
    # Mirroring your precise JS toLocal() math formulas:
    x = R * np.cos(np.radians(lat0)) * np.radians(p['lng'] - lng0)
    y = p['alt']
    z = -R * np.radians(p['lat'] - lat0)

    p['x'] = float(x)
    p['y'] = float(y)
    p['z'] = float(z)
    final_points.append(p)

# Save JSON file directly to your workspace folder
output_path = '/Users/ethansimon/Desktop/VTOL/Flight_Logs/flight_data.json'
with open(output_path, 'w') as f:
    json.dump(final_points, f)

print(f"Done! Created tracking file containing {len(final_points)} synced data frames.")
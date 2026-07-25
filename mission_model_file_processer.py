# ═══════════════════════════════════════════════════════
#  VTOL Mission Model – Configuration File
# ═══════════════════════════════════════════════════════

# ── Paths ───────────────────────────────────────────────
# Folder containing your motor CSV files
motors_folder:  r"/Users/ethansimon/Desktop/VTOL Coding files/Raw_Motor_Files"

# Folder where results (Excel + plot images) will be saved
results_folder: r"'/Users/ethansimon/Desktop/VTOL Coding files/Processed_Motor_FIles'"

# ── Motor Selection ─────────────────────────────────────
# Leave as null (~) to run ALL motors in motors_folder.
# Or list specific filenames (without .csv) to compare only those:
#
# enabled_motors:
#   - KDE2512435_18_5_305_37_6_245_155
#   - AnotherMotor_14_4_280_42_6_200_90
enabled_motors: ~

# ── Battery ─────────────────────────────────────────────
# Index into the battery table (0-4):
#   0 → 1532 g, 12000 mAh, $273
#   1 → 1988 g, 16000 mAh, $336
#   2 → 2030 g, 18000 mAh, $500
#   3 → 2530 g, 22000 mAh, $476
#   4 → 3500 g, 30000 mAh, $532
battery_index: 0

# ── Vehicle Base Masses (grams) ─────────────────────────
mass_frame_and_arms_g:   3000
mass_electronics_g:       700
mass_payload_mounting_g:  800

# ── Mission Profile ─────────────────────────────────────
takeoff_altitude_m:   15.24   # 50 ft
cruise_1_distance_m:  100.0
hover_time_s:          20.0
cruise_2_distance_m:  200.0
# Landing descends from takeoff_altitude_m back to ground

# ── Physics ─────────────────────────────────────────────
num_motors:   6
drag_coeff:   1.28
air_density:  1.225   # kg/m³ (sea level standard)

# ── Throttle ────────────────────────────────────────────
throttle_min:      30
throttle_max:      100
throttle_steps:    71
takeoff_throttle:  75   # % throttle used during takeoff
cruise_throttle:   80   # % throttle used during cruise

# ── Output ──────────────────────────────────────────────
show_plots:  true    # display plots in a window
save_excel:  true    # save ranked results to Excel
save_plots:  true    # save plot images alongside Excel
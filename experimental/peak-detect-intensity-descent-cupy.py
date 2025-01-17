import sys
import sqlite3
import pandas as pd
import argparse
import numpy as np
# import matplotlib.pyplot as plt
from scipy import signal
import peakutils
import time
import math
import collections
import json
import os
import cupy as cp


MIN_POINTS_IN_PEAK_TO_CHECK_FOR_TROUGHS = 10

def standard_deviation(mz):
    instrument_resolution = 40000.0
    return (mz / instrument_resolution) / 2.35482

# Source: https://stackoverflow.com/questions/2413522/weighted-standard-deviation-in-numpy/2415343
def weighted_avg_and_std(values, weights):
    """
    Return the weighted average and standard deviation.

    values, weights -- Numpy ndarrays with the same shape.
    """
    average = np.average(values, weights=weights)
    variance = np.average((values-average)**2, weights=weights)  # Fast and numerically precise
    return (average, math.sqrt(variance))

def findNearestGreaterThan(searchVal, inputData):
    diff = inputData - searchVal
    diff[diff<0] = sys.maxint
    idx = diff.argmin()
    return idx, inputData[idx]

def findNearestLessThan(searchVal, inputData):
    diff = inputData - searchVal
    diff[diff>0] = -sys.maxint
    idx = diff.argmax()
    return idx, inputData[idx]

parser = argparse.ArgumentParser(description='A tree descent method for peak detection.')
parser.add_argument('-db','--database_name', type=str, help='The name of the source database.', required=True)
parser.add_argument('-fl','--frame_lower', type=int, help='The lower frame number.', required=True)
parser.add_argument('-fu','--frame_upper', type=int, help='The upper frame number.', required=True)
parser.add_argument('-sl','--scan_lower', type=int, default=0, help='The lower scan number.', required=False)
parser.add_argument('-su','--scan_upper', type=int, default=183, help='The upper scan number.', required=False)
parser.add_argument('-es','--empty_scans', type=int, default=2, help='Maximum number of empty scans to tolerate.', required=False)
parser.add_argument('-sd','--standard_deviations', type=int, default=4, help='Number of standard deviations to look either side of a point.', required=False)
parser.add_argument('-ts','--temp_store', type=str, help='Name of the temporary database for the output.', required=True)

args = parser.parse_args()

# Store the arguments as metadata in the database for later reference
peak_detect_info = []
for arg in vars(args):
    peak_detect_info.append((arg, getattr(args, arg)))

source_conn = sqlite3.connect(database=args.database_name, timeout=60)

c = source_conn.cursor()
# c.execute("PRAGMA temp_store = MEMORY") # store temporary data in memory
# c.execute("PRAGMA synchronous = OFF") # https://www.sqlite.org/pragma.html#pragma_synchronous
# c.execute("PRAGMA journal_mode = WAL") # enable write-ahead logging mode
# print("WAL mode status: {}".format(c.fetchone()))

# Write out all the peaks to the database
if os.path.isfile(args.temp_store):
    os.remove(args.temp_store)

dest_conn = sqlite3.connect(database=args.temp_store)
d = dest_conn.cursor()
d.execute('''CREATE TABLE peaks (frame_id INTEGER, peak_id INTEGER, centroid_mz REAL, centroid_scan REAL, intensity_sum INTEGER, scan_upper INTEGER, scan_lower INTEGER, std_dev_mz REAL, std_dev_scan REAL, cluster_id INTEGER, 'rationale' TEXT, 'state' TEXT, intensity_max INTEGER, peak_max_mz REAL, peak_max_scan INTEGER, PRIMARY KEY (frame_id, peak_id))''')
d.execute('''CREATE TABLE peak_detect_info (item TEXT, value TEXT)''')
d.execute('''CREATE TABLE frames_updates (peak_id INTEGER, frame_id INTEGER, point_id INTEGER)''')

mono_peaks = []
point_updates = []
start_run = time.time()
for frame_id in range(args.frame_lower, args.frame_upper+1):
    peak_id = 1
    frame_df = pd.read_sql_query("select mz,scan,intensity,point_id,peak_id,frame_id from frames where frame_id={} order by mz, scan asc;"
        .format(frame_id), source_conn)
    print("Processing frame {}".format(frame_id))
    start_frame = time.time()
    frame_v = cp.array(frame_df.values)
    print("frame occupies {} bytes".format(frame_v.nbytes))
    # for i in range(1,61):
    while len(frame_v) > 0:
        peak_indices = np.empty(0, dtype=int)

        rationale = collections.OrderedDict()
        max_intensity_index = int(frame_v.argmax(axis=0)[2])
        mz = frame_v[max_intensity_index][0]
        scan = int(frame_v[max_intensity_index][1])
        intensity = int(frame_v[max_intensity_index][2])
        point_id = int(frame_v[max_intensity_index][3])
        peak_indices = np.append(peak_indices, max_intensity_index)
        rationale["highest intensity point id"] = point_id

        # Look for other points belonging to this peak
        std_dev_window = standard_deviation(mz) * args.standard_deviations
        # Look in the 'up' direction
        scan_offset = 1
        missed_scans = 0
        while (missed_scans < args.empty_scans) and (scan-scan_offset >= args.scan_lower):
            # print("looking in scan {}".format(scan-scan_offset))
            nearby_indices_up = cp.where((frame_v[:,1] == scan-scan_offset) & (frame_v[:,0] >= mz - std_dev_window) & (frame_v[:,0] <= mz + std_dev_window))[0]
            nearby_points_up = frame_v[nearby_indices_up]
            # print("nearby indices: {}".format(nearby_indices_up))
            if len(nearby_indices_up) == 0:
                missed_scans += 1
                # print("found no points")
            else:
                if len(nearby_indices_up) > 1:
                    # take the most intense point if there's more than one point found on this scan
                    frame_v_index_to_use = int(nearby_indices_up[cp.argmax(nearby_points_up[:,2])])
                else:
                    frame_v_index_to_use = int(nearby_indices_up[0])
                # Update the m/z window
                mz = frame_v[frame_v_index_to_use][0]
                std_dev_window = standard_deviation(mz) * args.standard_deviations
                missed_scans = 0
                # print("found {} points".format(len(nearby_indices_up)))
                peak_indices = np.append(peak_indices, frame_v_index_to_use)
            scan_offset += 1
        # Look in the 'down' direction
        scan_offset = 1
        missed_scans = 0
        mz = frame_v[max_intensity_index][0]
        std_dev_window = standard_deviation(mz) * args.standard_deviations
        while (missed_scans < args.empty_scans) and (scan+scan_offset <= args.scan_upper):
            # print("looking in scan {}".format(scan+scan_offset))
            nearby_indices_down = cp.where((frame_v[:,1] == scan+scan_offset) & (frame_v[:,0] >= mz - std_dev_window) & (frame_v[:,0] <= mz + std_dev_window))[0]
            nearby_points_down = frame_v[nearby_indices_down]
            if len(nearby_indices_down) == 0:
                missed_scans += 1
                # print("found no points")
            else:
                if len(nearby_indices_down) > 1:
                    # take the most intense point if there's more than one point found on this scan
                    frame_v_index_to_use = int(nearby_indices_down[cp.argmax(nearby_points_down[:,2])])
                else:
                    frame_v_index_to_use = int(nearby_indices_down[0])
                
                # Update the m/z window
                mz = frame_v[frame_v_index_to_use][0]
                std_dev_window = standard_deviation(mz) * args.standard_deviations
                missed_scans = 0
                peak_indices = np.append(peak_indices, frame_v_index_to_use)
            scan_offset += 1

        if len(peak_indices) > 1:
            if len(peak_indices) > MIN_POINTS_IN_PEAK_TO_CHECK_FOR_TROUGHS:
                # Check whether it has more than one peak
                # filter the intensity with a Gaussian filter
                sorted_peaks_indexes = cp.asnumpy(cp.argsort(frame_v[peak_indices][:,1]))
                peaks_sorted = cp.asnumpy(frame_v[cp.array(peak_indices[sorted_peaks_indexes])])
                rationale["point ids"] = peaks_sorted[:,3].astype(int).tolist()
                filtered = signal.savgol_filter(peaks_sorted[:,2], 9, 5)
                max_index = int(np.argmax(peaks_sorted[:,2]))

                # f = plt.figure()
                # ax1 = f.add_subplot(111)
                # ax1.plot(peaks_sorted[:,1], peaks_sorted[:,2], 'o', markerfacecolor='green', markeredgecolor='black', markeredgewidth=0.0, markersize=6)
                # ax1.plot(peaks_sorted[:,1], filtered, '-', markerfacecolor='blue', markeredgecolor='black', markeredgewidth=0.0, markersize=6)

                peak_maxima_indexes = peakutils.indexes(filtered, thres=0.05, min_dist=2)
                peak_minima_indexes = []
                if len(peak_maxima_indexes) > 1:
                    for idx,peak_maxima_index in enumerate(peak_maxima_indexes):
                        # ax1.plot(peaks_sorted[peak_maxima_index,1], peaks_sorted[peak_maxima_index,2], 'o', markerfacecolor='red', markeredgecolor='black', markeredgewidth=0.0, markersize=10, alpha=0.5)
                        if idx>0:
                            intensities_between_maxima = filtered[peak_maxima_indexes[idx-1]:peak_maxima_indexes[idx]+1]
                            minimum_intensity_index = int(cp.argmin(intensities_between_maxima)+peak_maxima_indexes[idx-1])
                            peak_minima_indexes.append(minimum_intensity_index)
                            # ax1.plot(peaks_sorted[minimum_intensity_index,1], peaks_sorted[minimum_intensity_index,2], 'x', markerfacecolor='purple', markeredgecolor='black', markeredgewidth=6.0, markersize=10, alpha=0.5)
                # print("peak maximum: {}".format(max_index))
                # print("peak minima: {}".format(peak_minima_indexes))
                indices_to_delete = cp.empty(0)
                if len(peak_minima_indexes) > 0:
                    idx,lower_snip = findNearestLessThan(max_index, peak_minima_indexes)
                    idx,upper_snip = findNearestGreaterThan(max_index, peak_minima_indexes)
                    if lower_snip < max_index:
                        # ax1.plot(peaks_sorted[lower_snip,1], peaks_sorted[lower_snip,2], '+', markerfacecolor='purple', markeredgecolor='red', markeredgewidth=2.0, markersize=20, alpha=1.0)
                        indices_to_delete = cp.concatenate((indices_to_delete,cp.arange(lower_snip)))
                    if upper_snip > max_index:
                        # ax1.plot(peaks_sorted[upper_snip,1], peaks_sorted[upper_snip,2], '+', markerfacecolor='purple', markeredgecolor='red', markeredgewidth=2.0, markersize=20, alpha=1.0)
                        indices_to_delete = cp.concatenate((indices_to_delete,cp.arange(upper_snip+1,len(peaks_sorted))))
                    sorted_peaks_indexes = cp.delete(sorted_peaks_indexes, indices_to_delete, 0)
                    peak_indices = peak_indices[sorted_peaks_indexes]
                    peaks_sorted = frame_v[peak_indices]
                    # ax1.plot(peaks_sorted[:,1], peaks_sorted[:,2], 'o', markerfacecolor='blue', markeredgecolor='black', markeredgewidth=0.0, markersize=6)
                    rationale["point ids after trimming"] = peaks_sorted[:,3].astype(int).tolist()

                # plt.title("Peak {}".format(peak_id))
                # plt.xlabel('scan')
                # plt.ylabel('intensity')
                # plt.margins(0.02)
                # plt.show()

            # Add the peak to the collection
            peak_mz = []
            peak_scan = []
            peak_intensity = []

            # Update database
            for p in peak_indices:
                # Collect all the points in the peak
                peak_mz.append(float(frame_v[p][0]))
                peak_scan.append(int(frame_v[p][1]))
                peak_intensity.append(int(frame_v[p][2]))

                # Assign this peak ID to all the points in the peak
                point_updates.append((peak_id, frame_id, int(frame_v[int(p)][3])))

            # Add the peak's details to the collection
            peak_intensity_sum = np.sum(peak_intensity)
            peak_intensity_max = np.max(peak_intensity)
            peak_scan_upper = np.max(peak_scan)
            peak_scan_lower = np.min(peak_scan)
            peak_mz_centroid, peak_std_dev_mz = weighted_avg_and_std(values=peak_mz, weights=peak_intensity)
            peak_scan_centroid, peak_std_dev_scan = weighted_avg_and_std(values=peak_scan, weights=peak_intensity)
            peak_points = frame_v[peak_indices]
            peak_max_index = peak_points[:,2].argmax()
            peak_max_mz = peak_points[peak_max_index][0]
            peak_max_scan = peak_points[peak_max_index][1].astype(int)
            mono_peaks.append((frame_id, peak_id, peak_mz_centroid, peak_scan_centroid, peak_intensity_sum, peak_scan_upper, peak_scan_lower, peak_std_dev_mz, peak_std_dev_scan, json.dumps(rationale), ' ', peak_intensity_max, peak_max_mz, peak_max_scan))

            peak_id += 1

        # remove the points we've processed from the frame
        frame_v = cp.delete(frame_v, peak_indices, 0)

    # Write out the peaks we found in this frame
    d.executemany("INSERT INTO peaks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)", mono_peaks)
    mono_peaks = []

    # Update the points in the frame table
    d.executemany("INSERT INTO frames_updates VALUES (?, ?, ?)", point_updates)
    point_updates = []

    stop_frame = time.time()
    print("{} seconds to process frame {} - {} peaks".format(stop_frame-start_frame, frame_id, peak_id))

source_conn.close()

stop_run = time.time()
print("{} seconds to process frames {} to {}".format(stop_run-start_run, args.frame_lower, args.frame_upper))

# write out the processing info
peak_detect_info.append(("run processing time (sec)", stop_run-start_run))
peak_detect_info.append(("processed", time.ctime()))
d.executemany("INSERT INTO peak_detect_info VALUES (?, ?)", peak_detect_info)
dest_conn.commit()
dest_conn.close()

# plt.close('all')

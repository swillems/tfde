import sys
import sqlite3
import pandas as pd
import argparse
import numpy as np
import matplotlib.pyplot as plt
import peakutils
import time

def standard_deviation(mz):
    instrument_resolution = 40000.0
    return (mz / instrument_resolution) / 2.35482


parser = argparse.ArgumentParser(description='A tree descent method for peak detection.')
parser.add_argument('-db','--database_name', type=str, help='The name of the source database.', required=True)
subparsers = parser.add_subparsers(dest='cmd', help='help for subcommand')

# create the parser for the "query" command
parser_a = subparsers.add_parser('query', help='Display the attributes of the frames in the database.')

# create the parser for the "process" command
parser_b = subparsers.add_parser('process', help='Process the database.')
parser_b.add_argument('-fl','--frame_lower', type=int, help='The lower frame number.', required=True)
parser_b.add_argument('-fu','--frame_upper', type=int, help='The upper frame number.', required=True)
parser_b.add_argument('-sl','--scan_lower', type=int, help='The lower scan number.', required=True)
parser_b.add_argument('-su','--scan_upper', type=int, help='The upper scan number.', required=True)
parser_b.add_argument('-es','--empty_scans', type=int, default=2, help='Maximum number of empty scans to tolerate.', required=False)
parser_b.add_argument('-sd','--standard_deviations', type=int, default=4, help='Number of standard deviations to look either side of a point.', required=False)

args = parser.parse_args()
print args

source_conn = sqlite3.connect(args.database_name)

if args.cmd == 'query':
    frame_df = pd.read_sql_query("select frame_id,scan,mz,intensity from frames;", source_conn)
    scan_min = frame_df.scan.min()
    scan_max = frame_df.scan.max()
    mz_min = frame_df.mz.min()
    mz_max = frame_df.mz.max()
    frame_min = frame_df.frame_id.min()
    frame_max = frame_df.frame_id.max()
    intensity_min = frame_df.intensity.min()
    intensity_max = frame_df.intensity.max()
    print("frames {}-{}, scans {}-{}, m/z {}-{}, intensity {}-{}".format(frame_min, frame_max, scan_min, scan_max, mz_min, mz_max, intensity_min, intensity_max))

elif args.cmd == 'process':
    c = source_conn.cursor()

    # Set up the table for detected peaks
    print("Setting up tables and indexes")
    c.execute('''DROP TABLE IF EXISTS peaks''')
    c.execute('''CREATE TABLE peaks (frame_id INTEGER, peak_id INTEGER, state TEXT, centroid_mz REAL, centroid_scan INTEGER, cluster_id INTEGER, PRIMARY KEY (frame_id, peak_id))''')

    c.execute('''DROP TABLE IF EXISTS peak_log''')
    c.execute('''CREATE TABLE peak_log (frame_id INTEGER, peak_id INTEGER, entry_id INTEGER PRIMARY KEY AUTOINCREMENT, entry TEXT, date TEXT)''')

    # Indexes
    c.execute('''DROP INDEX IF EXISTS idx_peaks''')
    c.execute('''CREATE INDEX idx_peaks ON peaks (frame_id,peak_id)''')

    c.execute('''DROP INDEX IF EXISTS idx_frame_point''')
    c.execute('''CREATE INDEX idx_frame_point ON frames (frame_id,point_id)''')

    c.execute("update frames set peak_id=0")

    mono_peaks = []
    start_run = time.time()
    for frame_id in range(args.frame_lower, args.frame_upper+1):
        peak_id = 1
        frame_df = pd.read_sql_query("select mz,scan,intensity,point_id,peak_id,frame_id from frames where frame_id={} order by mz, scan asc;"
            .format(frame_id), source_conn)
        print("Processing frame {}".format(frame_id))
        start_frame = time.time()
        frame_v = frame_df.values
        while len(frame_v) > 0:
            peak_indices = np.empty(0)

            max_intensity_index = frame_v.argmax(axis=0)[2]
            mz = frame_v[max_intensity_index][0]
            scan = int(frame_v[max_intensity_index][1])
            intensity = int(frame_v[max_intensity_index][2])
            point_id = int(frame_v[max_intensity_index][3])
            peak_indices = np.append(peak_indices, max_intensity_index)

            # Look for other points belonging to this peak
            std_dev_window = standard_deviation(mz) * args.standard_deviations
            # Look in the 'up' direction
            scan_offset = 1
            missed_scans = 0
            while (missed_scans < args.empty_scans) and (scan-scan_offset >= args.scan_lower):
                # print("looking in scan {}".format(scan-scan_offset))
                nearby_indices_up = np.where((frame_v[:,1] == scan-scan_offset) & (frame_v[:,0] >= mz - std_dev_window) & (frame_v[:,0] <= mz + std_dev_window))[0]
                nearby_points_up = frame_v[nearby_indices_up]
                # print("nearby indices: {}".format(nearby_indices_up))
                if len(nearby_indices_up) == 0:
                    missed_scans += 1
                    # print("found no points")
                else:
                    # Update the m/z window
                    mz = nearby_points_up[np.argsort(nearby_points_up[:,2])[::-1]][0][0] # find the m/z of the most intense
                    std_dev_window = standard_deviation(mz) * args.standard_deviations
                    missed_scans = 0
                    # print("found {} points".format(len(nearby_indices_up)))
                    peak_indices = np.append(peak_indices, nearby_indices_up)
                scan_offset += 1
            # Look in the 'down' direction
            scan_offset = 1
            missed_scans = 0
            mz = frame_v[max_intensity_index][0]
            std_dev_window = standard_deviation(mz) * args.standard_deviations
            while (missed_scans < args.empty_scans) and (scan+scan_offset <= args.scan_upper):
                # print("looking in scan {}".format(scan+scan_offset))
                nearby_indices_down = np.where((frame_v[:,1] == scan+scan_offset) & (frame_v[:,0] >= mz - std_dev_window) & (frame_v[:,0] <= mz + std_dev_window))[0]
                nearby_points_down = frame_v[nearby_indices_down]
                if len(nearby_indices_down) == 0:
                    missed_scans += 1
                    # print("found no points")
                else:
                    # Update the m/z window
                    mz = nearby_points_down[np.argsort(nearby_points_down[:,2])[::-1]][0][0] # find the m/z of the most intense
                    std_dev_window = standard_deviation(mz) * args.standard_deviations
                    missed_scans = 0
                    # print("found {} points".format(len(nearby_indices_down)))
                    peak_indices = np.append(peak_indices, nearby_indices_down)
                scan_offset += 1

            # print("peak indices: {}".format(peak_indices))
            if len(peak_indices) > 1:
                # Add the peak to the collection
                mono_peaks.append((frame_id, peak_id, "", mz, scan))

                # Update database
                for p in peak_indices:
                    values = (peak_id, frame_id, frame_v[int(p)][3])
                    c.execute("update frames set peak_id=? where frame_id=? and point_id=?", values)
    
                peak_id += 1

            # remove the points we've processed from the frame
            frame_v = np.delete(frame_v, peak_indices, 0)

        stop_frame = time.time()
        print("{} seconds to process frame - {} peaks".format(stop_frame-start_frame, peak_id))

    # Write out all the peaks to the database
    c.executemany("INSERT INTO peaks VALUES (?, ?, ?, ?, ?, 0)", mono_peaks)
    source_conn.commit()
    source_conn.close()
    stop_run = time.time()
    print("{} seconds to process run".format(stop_run-start_run))

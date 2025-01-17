import sys
import sqlite3
import pandas as pd
import argparse
import numpy as np
import cupy as cp
import peakutils
import time

def standard_deviation(mz):
    instrument_resolution = 40000.0
    return (mz / instrument_resolution) / 2.35482

def unique(arr):
    arr = arr.flatten()
    arr.sort()
    flags = cp.concatenate((cp.array([True]), arr[1:] != arr[:-1]))
    return arr[flags]

parser = argparse.ArgumentParser(description='An intensity descent method for summing frames.')
parser.add_argument('-sdb','--source_database_name', type=str, help='The name of the source database.', required=True)
parser.add_argument('-ddb','--destination_database_name', type=str, help='The name of the destination database.', required=True)
parser.add_argument('-fts','--frames_to_sum', type=int, default=150, help='The number of MS1 source frames to sum.', required=False)
parser.add_argument('-fso','--frame_summing_offset', type=int, default=25, help='The number of MS1 source frames to shift for each summation.', required=False)
parser.add_argument('-mf','--noise_threshold', type=int, default=2, help='Minimum number of frames a point must appear in to be processed.', required=False)
parser.add_argument('-ce','--collision_energy', type=int, help='Collision energy, in eV.', required=True)
parser.add_argument('-hn','--hostname', default='mscypher-004', type=str, help='The hostname of the database.', required=False)
parser.add_argument('-fl','--frame_lower', type=int, help='The lower frame number.', required=False)
parser.add_argument('-fu','--frame_upper', type=int, help='The upper frame number.', required=False)
args = parser.parse_args()

# Connect to the databases
source_conn = sqlite3.connect(args.source_database_name)
src_c = source_conn.cursor()

dest_conn = sqlite3.connect(args.destination_database_name)
dest_c = dest_conn.cursor()

print("Setting up tables...")

dest_c.execute("DROP TABLE IF EXISTS summed_frames")
dest_c.execute("DROP TABLE IF EXISTS summing_info")
dest_c.execute("DROP TABLE IF EXISTS elution_profile")

dest_c.execute("CREATE TABLE summed_frames (frame_id INTEGER, point_id INTEGER, mz REAL, scan INTEGER, intensity INTEGER, peak_id INTEGER)")
dest_c.execute("CREATE TABLE summing_info (item TEXT, value TEXT)")
dest_c.execute("CREATE TABLE elution_profile (frame_id INTEGER, intensity INTEGER)")

src_c.execute("SELECT value from convert_info where item=\"num_scans\"")
row = src_c.fetchone()
scan_lower = 0
scan_upper = int(row[0])
print("scan range {}-{}".format(scan_lower, scan_upper))

# Store the arguments as metadata in the database for later reference
summing_info = []
for arg in vars(args):
    summing_info.append((arg, getattr(args, arg)))

# Find the complete set of frame ids to be processed
frame_ids_df = pd.read_sql_query("select frame_id from frame_properties where collision_energy={} order by frame_id ASC;".format(args.collision_energy), source_conn)
frame_ids = tuple(frame_ids_df.values[:,0])
number_of_summed_frames = 1 + int(((len(frame_ids) - args.frames_to_sum) / args.frame_summing_offset))
print("summing {} source frames with collision energy {} to create {} summed frames".format(len(frame_ids), args.collision_energy, number_of_summed_frames))

if args.frame_lower is None:
    args.frame_lower = 1
    print("lower frame_id set to {} from the data".format(args.frame_lower))

if args.frame_upper is None:
    args.frame_upper = number_of_summed_frames
    print("upper frame_id set to {} from the data".format(args.frame_upper))

start_run = time.time()

# Step through the source frames and sum them
for summedFrameId in range(args.frame_lower,args.frame_upper+1):
    baseFrameIdsIndex = (summedFrameId-1)*args.frame_summing_offset
    frameIdsToSum = frame_ids[baseFrameIdsIndex:baseFrameIdsIndex+args.frames_to_sum]
    print("Processing {} frames ({}) to create summed frame {}".format(len(frameIdsToSum), frameIdsToSum, summedFrameId))
    frame_df = pd.read_sql_query("select frame_id,mz,scan,intensity from frames where frame_id in {} order by frame_id, mz, scan asc;".format(frameIdsToSum), source_conn)
    frame_v = cp.asarray(frame_df.values)

    frame_start = time.time()
    pointId = 1
    points = []
    for scan in range(scan_lower, scan_upper+1):
        points_v = frame_v[cp.where(frame_v[:,2] == scan)]
        points_to_process = len(points_v)
        while len(points_v) > 0:
            max_intensity_index = cp.argmax(points_v[:,3])
            point_mz = points_v[max_intensity_index, 1]
            # print("m/z {}, intensity {}".format(point_mz, points_v[max_intensity_index, 3]))
            delta_mz = standard_deviation(point_mz) * 4.0
            # Find all the points in this point's std dev window
            nearby_point_indices = cp.where((points_v[:,1] >= point_mz-delta_mz) & (points_v[:,1] <= point_mz+delta_mz))[0]
            nearby_points = points_v[nearby_point_indices]
            # How many distinct frames do the points come from?
            unique_frames = unique(nearby_points[:,0])
            if len(unique_frames) >= args.noise_threshold:
                # find the total intensity and centroid m/z
                centroid_intensity = nearby_points[:,3].sum()
                centroid_mz = peakutils.centroid(nearby_points[:,1], nearby_points[:,3])
                points.append((int(summedFrameId), int(pointId), float(centroid_mz), int(scan), int(round(centroid_intensity)), 0))
                pointId += 1

            # remove the points we've processed
            points_v_cpu = cp.asnumpy(points_v)
            points_v_cpu = np.delete(points_v_cpu, nearby_point_indices, 0)
            points_v = cp.asarray(points_v_cpu)

    if len(points) > 0:
        dest_c.executemany("INSERT INTO summed_frames VALUES (?, ?, ?, ?, ?, ?)", points)
        dest_c.executemany("INSERT INTO elution_profile VALUES (?, ?)", [(summedFrameId, sum(zip(*points)[4]))])
    else:
        print("no points for summed frame id {}".format(summedFrameId))
        dest_c.executemany("INSERT INTO elution_profile VALUES (?, ?)", [(summedFrameId, 0)])
    dest_conn.commit()

    frame_end = time.time()
    print("{} sec for frame {} ({} points)".format(frame_end-frame_start, summedFrameId, len(points)))

    # clean up
    del points[:]
    del elution_profile[:]

stop_run = time.time()
print("{} seconds to process run".format(stop_run-start_run))

summing_info.append(("scan_lower", scan_lower))
summing_info.append(("scan_upper", scan_upper))

summing_info.append(("run processing time (sec)", stop_run-start_run))
summing_info.append(("processed", time.ctime()))

summing_info_entry = []
summing_info_entry.append(("summed frames {}-{}".format(args.frame_lower, args.frame_upper), ' '.join(str(e) for e in summing_info)))

dest_c.executemany("INSERT INTO summing_info VALUES (?, ?)", summing_info_entry)

source_conn.close()

dest_conn.commit()
dest_conn.close()

import sys
import numpy as np
import pandas as pd
import math
import time
import sqlite3
import copy
import argparse
import os.path
import csv
# import numba
# from numba import jit

# @jit
def standard_deviation(mz):
    instrument_resolution = 40000.0
    return (mz / instrument_resolution) / 2.35482

# @jit
def find_feature(base_index):
    cluster = clusters_v[base_index]
    frame_id = int(cluster[CLUSTER_FRAME_ID_IDX])
    charge_state = int(cluster[CLUSTER_CHARGE_STATE_IDX])

    # Seed the search bounds by the properties of the base peaks
    base_max_point_mz = cluster[CLUSTER_BASE_MAX_POINT_MZ_IDX]
    base_max_point_scan = cluster[CLUSTER_BASE_MAX_POINT_SCAN_IDX]
    base_mz_std_dev_offset = standard_deviation(base_max_point_mz) * args.mz_std_dev
    base_scan_std_dev_offset = cluster[CLUSTER_BASE_SCAN_STD_DEV_IDX] * args.scan_std_dev

    # look for other clusters that belong to this feature
    nearby_indices = np.where(
        (clusters_v[:, CLUSTER_INTENSITY_SUM_IDX] >= 0) &
        (clusters_v[:, CLUSTER_CHARGE_STATE_IDX] == charge_state) &
        (abs(clusters_v[:, CLUSTER_BASE_MAX_POINT_MZ_IDX] - base_max_point_mz) <= base_mz_std_dev_offset) &
        (abs(clusters_v[:, CLUSTER_BASE_MAX_POINT_SCAN_IDX] - base_max_point_scan) <= base_scan_std_dev_offset))[0]
    nearby_clusters = clusters_v[nearby_indices]

    # make sure there is not more than one cluster from each frame
    frame_ids = nearby_clusters[:, CLUSTER_FRAME_ID_IDX]
    if len(np.unique(frame_ids)) > 1:
        frame_change_indices = np.where(np.roll(frame_ids,1)!=frame_ids)[0]     # for when there is more than one cluster found in a frame, the first cluster will be the most intense
        cluster_indices = nearby_indices[frame_change_indices]
    else:
        cluster_indices = nearby_indices[0]
    nearby_clusters = clusters_v[cluster_indices]
    cluster_indices_index_of_base = np.where(cluster_indices == base_index)[0][0]

    # find the frame gap closest to the base cluster
    frame_diffs = np.diff(nearby_clusters[:,CLUSTER_FRAME_ID_IDX])
    # print("frame diffs {}".format(frame_diffs))

    large_gaps_forward = np.nonzero(frame_diffs[cluster_indices_index_of_base:] > args.empty_frames)[0]
    if len(large_gaps_forward) > 0:
        nearby_clusters_index_upper = large_gaps_forward[0] + cluster_indices_index_of_base
    else:
        nearby_clusters_index_upper = len(nearby_clusters)-1

    large_gaps_backward = np.nonzero(frame_diffs[:cluster_indices_index_of_base] > args.empty_frames)[0]
    if len(large_gaps_backward) > 0:
        nearby_clusters_index_lower = cluster_indices_index_of_base - large_gaps_backward[::-1][0]
    else:
        nearby_clusters_index_lower = 0

    cluster_indices = cluster_indices[nearby_clusters_index_lower:nearby_clusters_index_upper]
    good_quality_feature = len(cluster_indices) > 3/NUMBER_OF_SECONDS_PER_FRAME

    results = {}
    results['base_cluster_frame_id'] = int(cluster[CLUSTER_FRAME_ID_IDX])
    results['base_cluster_id'] = int(cluster[CLUSTER_ID_IDX])
    results['cluster_indices'] = cluster_indices
    results['lower_frame'] = int(clusters_v[cluster_indices[0],CLUSTER_FRAME_ID_IDX])
    results['upper_frame'] = int(clusters_v[cluster_indices[len(cluster_indices)-1],CLUSTER_FRAME_ID_IDX])
    results['charge_state'] = charge_state
    results['quality'] = good_quality_feature
    return results


parser = argparse.ArgumentParser(description='A method for tracking features through frames.')
parser.add_argument('-db','--database_name', type=str, help='The name of the source database.', required=True)
parser.add_argument('-fl','--frame_lower', type=int, help='The lower frame number to process.', required=True)
parser.add_argument('-fu','--frame_upper', type=int, help='The upper frame number to process.', required=True)
parser.add_argument('-md','--mz_std_dev', type=int, default=4, help='Number of standard deviations to look either side of the base peak, in the m/z dimension.', required=False)
parser.add_argument('-sd','--scan_std_dev', type=int, default=4, help='Number of standard deviations to look either side of the base peak, in the scan dimension.', required=False)
parser.add_argument('-ef','--empty_frames', type=int, default=10, help='Maximum number of empty frames to tolerate.', required=False)
parser.add_argument('-nf','--number_of_features', type=int, help='Maximum number of features to find.', required=False)
parser.add_argument('-ns','--number_of_seconds', type=int, default=10, help='Number of seconds to look either side of the maximum cluster.', required=False)
args = parser.parse_args()

# Store the arguments as metadata in the database for later reference
feature_info = []
for arg in vars(args):
    feature_info.append((arg, getattr(args, arg)))

# Connect to the database file
source_conn = sqlite3.connect(args.database_name)
c = source_conn.cursor()

print("Setting up tables and indexes")

c.execute('''DROP TABLE IF EXISTS features''')
c.execute('''CREATE TABLE `features` ( `feature_id` INTEGER, 
                                        `base_cluster_id` INTEGER, 
                                        `charge_state` INTEGER, 
                                        PRIMARY KEY(`feature_id`) )''')
c.execute('''DROP INDEX IF EXISTS idx_features''')
c.execute('''CREATE INDEX idx_features ON features (feature_id)''')

c.execute('''DROP TABLE IF EXISTS feature_info''')
c.execute('''CREATE TABLE feature_info (item TEXT, value TEXT)''')

feature_id = 1
start_run = time.time()

# cluster array indices
CLUSTER_FRAME_ID_IDX = 0
CLUSTER_ID_IDX = 1
CLUSTER_CHARGE_STATE_IDX = 2
CLUSTER_BASE_SCAN_STD_DEV_IDX = 3
CLUSTER_BASE_MAX_POINT_MZ_IDX = 4
CLUSTER_BASE_MAX_POINT_SCAN_IDX = 5
CLUSTER_INTENSITY_SUM_IDX = 6

NUMBER_OF_SECONDS_PER_FRAME = 0.1
NUMBER_OF_FRAMES_TO_LOOK = int(args.number_of_seconds / NUMBER_OF_SECONDS_PER_FRAME)

print("Loading the clusters information")

c.execute("select frame_id, cluster_id, charge_state, base_peak_scan_std_dev, base_peak_max_point_mz, base_peak_max_point_scan, intensity_sum from clusters order by frame_id, cluster_id asc;")
clusters_v = np.array(c.fetchall(), dtype=np.float32)

print("clusters array occupies {} bytes".format(clusters_v.nbytes))
print("Finding features")

clusters_searched = 0

with open('discovery_rate.csv','wb') as discovery_rate_file:
    writer=csv.writer(discovery_rate_file, delimiter=',')

    # go through each cluster and see whether it belongs to an existing feature
    while len(np.where((clusters_v[:,CLUSTER_INTENSITY_SUM_IDX] > -1))[0]) > 0:
        # find the most intense cluster
        cluster_max_index = clusters_v.argmax(axis=0)[CLUSTER_INTENSITY_SUM_IDX]
        cluster = clusters_v[cluster_max_index]

        feature = find_feature(base_index=cluster_max_index)
        base_cluster_frame_id = feature['base_cluster_frame_id']
        base_cluster_id = feature['base_cluster_id']
        cluster_indices = feature['cluster_indices']
        charge_state = feature['charge_state']
        feature_quality = feature['quality']

        if feature_quality:
            # Assign this feature ID to all the clusters in the feature
            cluster_updates = []
            for cluster_idx in cluster_indices:
                values = (feature_id, int(clusters_v[cluster_idx][CLUSTER_FRAME_ID_IDX]), int(clusters_v[cluster_idx][CLUSTER_ID_IDX]))
                cluster_updates.append(values)
            c.executemany("UPDATE clusters SET feature_id=? WHERE frame_id=? AND cluster_id=?", cluster_updates)

            # Add the feature's details to the collection
            values = (feature_id, base_cluster_id, charge_state)
            c.execute("INSERT INTO features VALUES (?, ?, ?)", values)

            if args.number_of_features is not None:
                if feature_id == args.number_of_features:
                    break

            feature_id += 1

        clusters_searched += 1
        writer.writerow([clusters_searched, feature_id-1, int(cluster[CLUSTER_INTENSITY_SUM_IDX])])
        discovery_rate_file.flush()
        # remove the features we've processed from the run
        clusters_v[cluster_indices, CLUSTER_INTENSITY_SUM_IDX] = -1

    stop_run = time.time()
    feature_info.append(("run processing time (sec)", stop_run-start_run))
    feature_info.append(("processed", time.ctime()))
    c.executemany("INSERT INTO feature_info VALUES (?, ?)", feature_info)

    source_conn.commit()
    source_conn.close()

import pandas as pd
import numpy as np
from sklearn.cluster import DBSCAN
import peakutils
from scipy import signal
import math
import os
import time
import argparse
import ray
import sqlite3
import sys
import multiprocessing as mp
import pickle
import configparser
from configparser import ExtendedInterpolation

# set up the indexes we need for queries
def create_indexes(db_file_name):
    db_conn = sqlite3.connect(db_file_name)
    src_c = db_conn.cursor()
    src_c.execute("create index if not exists idx_extract_cuboids_1 on frames (frame_type,retention_time_secs,scan,mz)")
    db_conn.close()

# a distance metric for points within an isotope
def point_metric(r1, r2):
    # mz_1 = r1[0]
    # scan_1 = r1[1]
    # mz_2 = r2[0]
    # scan_2 = r2[1]
    return 0.5 if ((abs(r1[0] - r2[0]) <= 0.1) and (abs(r1[1] - r2[1]) <= 5)) else 10

# a distance metric for isotopes within a series
def isotope_metric(r1, r2):
    mz_1 = r1[0]
    scan_1 = r1[1]
    mz_2 = r2[0]
    scan_2 = r2[1]
    if (abs(mz_1 - mz_2) <= 0.8) and (abs(mz_1 - mz_2) > 0.1) and (abs(scan_1 - scan_2) <= 10):
        result = 0.5
    else:
        result = 10
    # print('r1={}, r2={}, result={}'.format(r1,r2,result))
    return result

# determine the number of workers based on the number of available cores and the proportion of the machine to be used
def number_of_workers():
    number_of_cores = mp.cpu_count()
    number_of_workers = int(args.proportion_of_cores_to_use * number_of_cores)
    return number_of_workers

# determine the maximum filter length for the number of points
def find_filter_length(number_of_points):
    filter_lengths = [51,11,5]  # must be a positive odd number, greater than the polynomial order, and less than the number of points to be filtered
    return filter_lengths[next(x[0] for x in enumerate(filter_lengths) if x[1] < number_of_points)]

# process a segment of this run's data, and return a list of precursor cuboids
# @ray.remote
def find_precursor_cuboids(segment_mz_lower, segment_mz_upper):
    isotope_cluster_retries = 0
    point_cluster_retries = 0
    precursor_cuboids_l = []

    # load the raw points for this m/z segment
    db_conn = sqlite3.connect(CONVERTED_DATABASE_NAME)
    raw_df = pd.read_sql_query("select frame_id,mz,scan,intensity,retention_time_secs from frames where frame_type == {} and retention_time_secs >= {} and retention_time_secs <= {} and mz >= {} and mz <= {}".format(FRAME_TYPE_MS1, args.rt_lower, args.rt_upper, segment_mz_lower, segment_mz_upper), db_conn)
    db_conn.close()

    if len(raw_df) > 0:
        # assign each point a unique identifier
        raw_df.reset_index(drop=True, inplace=True)  # just in case
        raw_df['point_id'] = raw_df.index

        # define bins
        rt_bins = pd.interval_range(start=raw_df.retention_time_secs.min(), end=raw_df.retention_time_secs.max()+RT_BIN_SIZE, freq=RT_BIN_SIZE)
        scan_bins = pd.interval_range(start=raw_df.scan.min(), end=raw_df.scan.max()+SCAN_BIN_SIZE, freq=SCAN_BIN_SIZE)
        mz_bins = pd.interval_range(start=raw_df.mz.min(), end=raw_df.mz.max()+MZ_BIN_SIZE, freq=MZ_BIN_SIZE)

        # assign raw points to their bins
        raw_df['rt_bin'] = pd.cut(raw_df.retention_time_secs, bins=rt_bins)
        raw_df['scan_bin'] = pd.cut(raw_df.scan, bins=scan_bins)
        raw_df['mz_bin'] = pd.cut(raw_df.mz, bins=mz_bins)

        # sum the intensities in each bin
        summary_df = raw_df.groupby(['mz_bin','scan_bin','rt_bin'], as_index=False, sort=False).intensity.sum()
        summary_df.dropna(subset = ['intensity'], inplace=True)
        summary_df.sort_values(by=['intensity'], ascending=False, inplace=True)

        for row in summary_df.itertuples():
            # get the raw points for this voxel
            voxel_mz_lower = row.mz_bin.left
            voxel_mz_upper = row.mz_bin.right
            voxel_scan_lower = row.scan_bin.left
            voxel_scan_upper = row.scan_bin.right
            voxel_rt_lower = row.rt_bin.left
            voxel_rt_upper = row.rt_bin.right
            voxel_df = raw_df[(raw_df.mz >= voxel_mz_lower) & (raw_df.mz <= voxel_mz_upper) & (raw_df.scan >= voxel_scan_lower) & (raw_df.scan <= voxel_scan_upper) & (raw_df.retention_time_secs >= voxel_rt_lower) & (raw_df.retention_time_secs <= voxel_rt_upper)]

            if len(voxel_df) > 0:
                # find the anchor point for the voxel
                anchor_point_s = voxel_df.loc[voxel_df.intensity.idxmax()]

                # define the search area in the m/z and scan dimensions
                mz_lower = anchor_point_s.mz - ANCHOR_POINT_MZ_LOWER_OFFSET
                mz_upper = anchor_point_s.mz + ANCHOR_POINT_MZ_UPPER_OFFSET
                scan_lower = anchor_point_s.scan - ANCHOR_POINT_SCAN_LOWER_OFFSET
                scan_upper = anchor_point_s.scan + ANCHOR_POINT_SCAN_UPPER_OFFSET

                # constrain the raw points to the search area for this anchor point
                candidate_region_df = raw_df[(raw_df.intensity >= INTENSITY_THRESHOLD) & (raw_df.frame_id == anchor_point_s.frame_id) & (raw_df.mz >= mz_lower) & (raw_df.mz <= mz_upper) & (raw_df.scan >= scan_lower) & (raw_df.scan <= scan_upper)].copy()
                visualise_d = {}
                visualise_d['anchor_point_s'] = anchor_point_s
                visualise_d['initial_candidate_region_df'] = candidate_region_df

                peak_mz_lower = anchor_point_s.mz-MS1_PEAK_DELTA
                peak_mz_upper = anchor_point_s.mz+MS1_PEAK_DELTA
                visualise_d['peak_mz_lower'] = peak_mz_lower
                visualise_d['peak_mz_upper'] = peak_mz_upper

                # constrain the points to the anchor point's m/z
                peak_df = candidate_region_df[(candidate_region_df.mz >= peak_mz_lower) & (candidate_region_df.mz <= peak_mz_upper)]

                # find the extent of the anchor point's peak in the mobility dimension
                scan_df = peak_df.groupby(['scan'], as_index=False).intensity.sum()
                scan_df.sort_values(by=['scan'], ascending=True, inplace=True)

                # apply a smoothing filter to the points
                scan_df['filtered_intensity'] = scan_df.intensity  # set the default
                try:
                    scan_df['filtered_intensity'] = signal.savgol_filter(scan_df.intensity, window_length=find_filter_length(number_of_points=len(scan_df)), polyorder=SCAN_FILTER_POLY_ORDER)
                except:
                    pass
                visualise_d['scan_df'] = scan_df

                # find the valleys nearest the anchor point
                valley_idxs = peakutils.indexes(-scan_df.filtered_intensity.values, thres=VALLEYS_THRESHOLD_SCAN, min_dist=VALLEYS_MIN_DIST_SCAN, thres_abs=False)
                valley_x_l = scan_df.iloc[valley_idxs].scan.to_list()
                valleys_df = scan_df[scan_df.scan.isin(valley_x_l)]
                visualise_d['scan_valleys_df'] = valleys_df

                upper_x = valleys_df[valleys_df.scan > anchor_point_s.scan].scan.min()
                if math.isnan(upper_x):
                    upper_x = scan_df.scan.max()
                lower_x = valleys_df[valleys_df.scan < anchor_point_s.scan].scan.max()
                if math.isnan(lower_x):
                    lower_x = scan_df.scan.min()

                scan_lower = lower_x
                scan_upper = upper_x
                visualise_d['scan_lower'] = scan_lower
                visualise_d['scan_upper'] = scan_upper

                # trim the candidate region to account for the selected peak in mobility
                candidate_region_df = candidate_region_df[(candidate_region_df.scan >= scan_lower) & (candidate_region_df.scan <= scan_upper)].copy()

                # segment the raw data to reveal the isotopes in the feature
                X = candidate_region_df[['mz','scan']].values

                # cluster the points
                dbscan = DBSCAN(eps=1, min_samples=3, metric=point_metric)
                clusters = dbscan.fit_predict(X)
                candidate_region_df['cluster'] = clusters
                visualise_d['candidate_region_with_isotope_clusters_df'] = candidate_region_df

                number_of_point_clusters = len(candidate_region_df[candidate_region_df.cluster >= 0].cluster.unique())
                if (number_of_point_clusters > 0):
                    anchor_point_cluster = candidate_region_df[candidate_region_df.point_id == anchor_point_s.point_id].iloc[0].cluster

                    # if we have more than one point clusters, and the anchor point is in one of them, carry on...
                    if (anchor_point_cluster >= 0):
                        # collect the points that are in the same point cluster as the anchor point
                        anchor_point_cluster_points_df = candidate_region_df[candidate_region_df.cluster == anchor_point_cluster]
                        # calculate the cluster centroids
                        centroids_l = []
                        for group_name,group_df in candidate_region_df.groupby(['cluster'], as_index=False):
                            if group_name >= 0:
                                mz_centroid = peakutils.centroid(group_df.mz, group_df.intensity)
                                scan_centroid = peakutils.centroid(group_df.scan, group_df.intensity)
                                centroids_l.append((group_name, mz_centroid, scan_centroid))
                        centroids_df = pd.DataFrame(centroids_l, columns=['cluster','mz','scan'])

                        X = centroids_df[['mz','scan']].values

                        # cluster the isotopes into series
                        dbscan = DBSCAN(eps=1, min_samples=2, metric=isotope_metric)  # minimum isotopes to form a series
                        clusters = dbscan.fit_predict(X)
                        centroids_df['isotope_cluster'] = clusters
                        visualise_d['centroids_df'] = centroids_df

                        number_of_isotope_clusters = len(centroids_df[centroids_df.isotope_cluster >= 0].isotope_cluster.unique())

                        if (number_of_isotope_clusters > 0):
                            anchor_point_isotope_cluster = centroids_df[(centroids_df.cluster == anchor_point_cluster)].iloc[0].isotope_cluster

                            # if we have at least one isotope series, and the anchor point is in one of them, carry on...
                            if (anchor_point_isotope_cluster >= 0):
                                candidate_region_df = pd.merge(candidate_region_df, centroids_df[['cluster','isotope_cluster']], how='left', left_on=['cluster'], right_on=['cluster'])
                                candidate_region_df.replace(to_replace=np.nan, value=-1, inplace=True)
                                candidate_region_df.isotope_cluster = candidate_region_df.isotope_cluster.astype(int)
                                visualise_d['candidate_region_with_isotope_series_clusters_df'] = candidate_region_df

                                # estimate the number of isotopes in the feature
                                number_of_point_clusters_in_anchor_isotope_cluster = len(centroids_df[(centroids_df.isotope_cluster == anchor_point_isotope_cluster)])

                                # we now have the 2D extent of the feature - take that extent through time and see if we can cluster the centroids in time

                                # get the extent of the isotope cluster in m/z and mobility
                                points_in_cluster_df = candidate_region_df[(candidate_region_df.isotope_cluster == anchor_point_isotope_cluster)]
                                mz_lower = points_in_cluster_df.mz.min()
                                mz_upper = points_in_cluster_df.mz.max()
                                scan_lower = points_in_cluster_df.scan.min()
                                scan_upper = points_in_cluster_df.scan.max()
                                visualise_d['isotope_cluster_mz_lower'] = mz_lower
                                visualise_d['isotope_cluster_mz_upper'] = mz_upper
                                visualise_d['isotope_cluster_scan_lower'] = scan_lower
                                visualise_d['isotope_cluster_scan_upper'] = scan_upper

                                # determine the feature's extent in RT by looking at the anchor point's peak
                                ap_raw_points_in_rt_df = raw_df[(raw_df.mz >= anchor_point_cluster_points_df.mz.min()) & (raw_df.mz <= anchor_point_cluster_points_df.mz.max()) & (raw_df.scan >= anchor_point_cluster_points_df.scan.min()) & (raw_df.scan <= anchor_point_cluster_points_df.scan.max()) & (raw_df.retention_time_secs >= anchor_point_s.retention_time_secs-RT_BASE_PEAK_WIDTH) & (raw_df.retention_time_secs <= anchor_point_s.retention_time_secs+RT_BASE_PEAK_WIDTH)]
                                rt_df = ap_raw_points_in_rt_df.groupby(['frame_id','retention_time_secs'], as_index=False).intensity.sum()
                                rt_df.sort_values(by=['retention_time_secs'], ascending=True, inplace=True)

                                # filter the points
                                rt_df['filtered_intensity'] = rt_df.intensity  # set the default
                                try:
                                    rt_df['filtered_intensity'] = signal.savgol_filter(rt_df.intensity, window_length=find_filter_length(number_of_points=len(rt_df)), polyorder=RT_FILTER_POLY_ORDER)
                                except:
                                    pass
                                visualise_d['rt_df'] = rt_df

                                # find the valleys nearest the anchor point
                                valley_idxs = peakutils.indexes(-rt_df.filtered_intensity.values, thres=VALLEYS_THRESHOLD_RT, min_dist=VALLEYS_MIN_DIST_RT, thres_abs=False)
                                valley_x_l = rt_df.iloc[valley_idxs].retention_time_secs.to_list()
                                valleys_df = rt_df[rt_df.retention_time_secs.isin(valley_x_l)]
                                visualise_d['rt_valleys_df'] = valleys_df

                                upper_x = valleys_df[valleys_df.retention_time_secs > anchor_point_s.retention_time_secs].retention_time_secs.min()
                                if math.isnan(upper_x):
                                    upper_x = rt_df.retention_time_secs.max()
                                lower_x = valleys_df[valleys_df.retention_time_secs < anchor_point_s.retention_time_secs].retention_time_secs.max()
                                if math.isnan(lower_x):
                                    lower_x = rt_df.retention_time_secs.min()

                                rt_lower = lower_x
                                rt_upper = upper_x
                                visualise_d['isotope_cluster_rt_lower'] = rt_lower
                                visualise_d['isotope_cluster_rt_upper'] = rt_upper

                                # make sure the RT extent isn't too extreme
                                if (rt_upper - rt_lower) > (RT_BASE_PEAK_WIDTH * 2):
                                    rt_lower = anchor_point_s.retention_time_secs - RT_BASE_PEAK_WIDTH
                                    rt_upper = anchor_point_s.retention_time_secs + RT_BASE_PEAK_WIDTH

                                # get the point ids for the feature in 3D
                                points_to_remove_l = raw_df[(raw_df.mz >= mz_lower) & (raw_df.mz <= mz_upper) & (raw_df.scan >= scan_lower) & (raw_df.scan <= scan_upper) & (raw_df.retention_time_secs >= rt_lower) & (raw_df.retention_time_secs <= rt_upper)].point_id.tolist()

                                # add this cuboid to the list
                                precursor_coordinates_d = {
                                    'mz_lower':mz_lower, 
                                    'mz_upper':mz_upper, 
                                    'wide_mz_lower':mz_lower - (CARBON_MASS_DIFFERENCE / 1), # just in case we missed the monoisotopic
                                    'wide_mz_upper':mz_upper, 
                                    'scan_lower':int(scan_lower), # same because we've already resolved its extent
                                    'scan_upper':int(scan_upper), 
                                    'wide_scan_lower':int(scan_lower), 
                                    'wide_scan_upper':int(scan_upper), 
                                    'rt_lower':rt_lower, 
                                    'rt_upper':rt_upper, 
                                    'wide_rt_lower':rt_lower, # same because we've already resolved its extent
                                    'wide_rt_upper':rt_upper, 
                                    'anchor_point_intensity':int(anchor_point_s.intensity), 
                                    'anchor_point_mz':anchor_point_s.mz, 
                                    'anchor_point_scan':int(anchor_point_s.scan), 
                                    'anchor_point_retention_time_secs':anchor_point_s.retention_time_secs, 
                                    'anchor_point_frame_id':int(anchor_point_s.frame_id), 
                                    'number_of_isotope_clusters':int(number_of_isotope_clusters), 
                                    'number_of_point_clusters_in_anchor_isotope_cluster':int(number_of_point_clusters_in_anchor_isotope_cluster)
                                    }
                                precursor_cuboids_l.append(precursor_coordinates_d)

                                print('.', end='', flush=True)
                                isotope_cluster_retries = 0

                                # remove the points we've used for this feature so we don't use them for another
                                raw_df.drop(points_to_remove_l, inplace=True)

                                if args.visualise:
                                    # save the visualisation info
                                    with open('visualise-three-d-{}.pkl'.format(int(anchor_point_s.intensity)), 'wb') as f:
                                        pickle.dump(visualise_d, f)
                            else:
                                # we could not form an isotopic series
                                print('_', end='', flush=True)
                                isotope_cluster_retries += 1
                                if isotope_cluster_retries >= MAX_ISOTOPE_CLUSTER_RETRIES:
                                    print('max isotope cluster retries reached for mz={} to {}'.format(segment_mz_lower, segment_mz_upper))
                                    break
                        else:
                            # we could not form an isotopic series
                            print('_', end='', flush=True)
                            isotope_cluster_retries += 1
                            if isotope_cluster_retries >= MAX_ISOTOPE_CLUSTER_RETRIES:
                                print('max isotope cluster retries reached for mz={} to {}'.format(segment_mz_lower, segment_mz_upper))
                                break
                    else:
                        # the anchor point is not within a point cluster
                        print('x', end='', flush=True)
                        point_cluster_retries += 1
                        if point_cluster_retries >= MAX_POINT_CLUSTER_RETRIES:
                            break
                else:
                    # could not form a point cluster
                    print('x', end='', flush=True)
                    point_cluster_retries += 1
                    if point_cluster_retries >= MAX_POINT_CLUSTER_RETRIES:
                        break
            else:
                print('*', end='', flush=True)

    # return what we found in this segment
    print('found {} cuboids for mz={} to {}'.format(len(precursor_cuboids_l), segment_mz_lower, segment_mz_upper))
    return precursor_cuboids_l



# move these constants to the INI file
ANCHOR_POINT_MZ_LOWER_OFFSET = 0.6   # one isotope for charge-2 plus a little bit more
ANCHOR_POINT_MZ_UPPER_OFFSET = 3.0   # six isotopes for charge-2 plus a little bit more

ANCHOR_POINT_SCAN_LOWER_OFFSET = 100
ANCHOR_POINT_SCAN_UPPER_OFFSET = 100

INTENSITY_THRESHOLD = 10
PROCESSED_INTENSITY_INDICATOR = -1

MAX_ISOTOPE_CLUSTER_RETRIES = 1000
MAX_POINT_CLUSTER_RETRIES = 10

# filter and peak detection parameters
VALLEYS_THRESHOLD_RT = 0.5    # only consider valleys that drop more than this proportion of the normalised maximum
VALLEYS_THRESHOLD_SCAN = 0.5

VALLEYS_MIN_DIST_RT = 2.0     # seconds
VALLEYS_MIN_DIST_SCAN = 10.0  # scans

SCAN_FILTER_POLY_ORDER = 3
RT_FILTER_POLY_ORDER = 3

# bin sizes
RT_BIN_SIZE = 5
SCAN_BIN_SIZE = 20
MZ_BIN_SIZE = 0.1


#######################
parser = argparse.ArgumentParser(description='Find all the features in a run with 3D intensity descent.')
parser.add_argument('-eb','--experiment_base_dir', type=str, default='./experiments', help='Path to the experiments directory.', required=False)
parser.add_argument('-en','--experiment_name', type=str, help='Name of the experiment.', required=True)
parser.add_argument('-rn','--run_name', type=str, help='Name of the run.', required=True)
parser.add_argument('-ml','--mz_lower', type=int, default='100', help='Lower limit for m/z.', required=False)
parser.add_argument('-mu','--mz_upper', type=int, default='1700', help='Upper limit for m/z.', required=False)
parser.add_argument('-mw','--mz_width_per_segment', type=int, default=20, help='Width in Da of the m/z processing window per segment.', required=False)
parser.add_argument('-rl','--rt_lower', type=int, default='1650', help='Lower limit for retention time.', required=False)
parser.add_argument('-ru','--rt_upper', type=int, default='2200', help='Upper limit for retention time.', required=False)
parser.add_argument('-ini','--ini_file', type=str, default='./otf-peak-detect/pipeline/pasef-process-short-gradient.ini', help='Path to the config file.', required=False)
parser.add_argument('-rm','--ray_mode', type=str, choices=['local','cluster'], help='The Ray mode to use.', required=True)
parser.add_argument('-pc','--proportion_of_cores_to_use', type=float, default=0.9, help='Proportion of the machine\'s cores to use for this program.', required=False)
parser.add_argument('-v','--visualise', action='store_true', help='Generate data for visualisation of the segmentation.')
args = parser.parse_args()

# Print the arguments for the log
info = []
for arg in vars(args):
    info.append((arg, getattr(args, arg)))
print(info)

start_run = time.time()

# check the experiment directory exists
EXPERIMENT_DIR = "{}/{}".format(args.experiment_base_dir, args.experiment_name)
if not os.path.exists(EXPERIMENT_DIR):
    print("The experiment directory is required but doesn't exist: {}".format(EXPERIMENT_DIR))
    sys.exit(1)

# check the converted databases directory exists
CONVERTED_DATABASE_NAME = "{}/converted-databases/exp-{}-run-{}-converted.sqlite".format(EXPERIMENT_DIR, args.experiment_name, args.run_name)
if not os.path.isfile(CONVERTED_DATABASE_NAME):
    print("The converted database is required but doesn't exist: {}".format(CONVERTED_DATABASE_NAME))
    sys.exit(1)

# check the INI file exists
if not os.path.isfile(args.ini_file):
    print("The configuration file doesn't exist: {}".format(args.ini_file))
    sys.exit(1)

# load the INI file
cfg = configparser.ConfigParser(interpolation=ExtendedInterpolation())
cfg.read(args.ini_file)

# set up constants
FRAME_TYPE_MS1 = cfg.getint('common','FRAME_TYPE_MS1')
MS1_PEAK_DELTA = cfg.getfloat('ms1','MS1_PEAK_DELTA')
RT_BASE_PEAK_WIDTH = cfg.getfloat('common','RT_BASE_PEAK_WIDTH_SECS')
CARBON_MASS_DIFFERENCE = cfg.getfloat('common','CARBON_MASS_DIFFERENCE')

# set up the indexes
print('setting up indexes on {}'.format(CONVERTED_DATABASE_NAME))
create_indexes(db_file_name=CONVERTED_DATABASE_NAME)

# set up the precursor cuboids
CUBOIDS_DIR = '{}/precursor-cuboids-3did'.format(EXPERIMENT_DIR)
if not os.path.exists(CUBOIDS_DIR):
    os.makedirs(CUBOIDS_DIR)

CUBOIDS_FILE = '{}/exp-{}-run-{}-precursor-cuboids-3did.pkl'.format(CUBOIDS_DIR, args.experiment_name, args.run_name)

# set up Ray
# print("setting up Ray")
# if not ray.is_initialized():
#     if args.ray_mode == "cluster":
#         ray.init(num_cpus=number_of_workers())
#     else:
#         ray.init(local_mode=True)

# calculate the segments
mz_range = args.mz_upper - args.mz_lower
NUMBER_OF_MZ_SEGMENTS = (mz_range // args.mz_width_per_segment) + (mz_range % args.mz_width_per_segment > 0)  # thanks to https://stackoverflow.com/a/23590097/1184799

# find the precursors
print('finding precursor cuboids')
# cuboids_l = ray.get([find_precursor_cuboids.remote(segment_mz_lower=args.mz_lower+(i*args.mz_width_per_segment), segment_mz_upper=args.mz_lower+(i*args.mz_width_per_segment)+args.mz_width_per_segment) for i in range(NUMBER_OF_MZ_SEGMENTS)])
cuboids_l = [find_precursor_cuboids(segment_mz_lower=args.mz_lower+(i*args.mz_width_per_segment), segment_mz_upper=args.mz_lower+(i*args.mz_width_per_segment)+args.mz_width_per_segment) for i in range(NUMBER_OF_MZ_SEGMENTS)]
cuboids_l = [item for sublist in cuboids_l for item in sublist]  # cuboids_l is a list of lists, so we need to flatten it

# assign each cuboid a unique identifier
coords_df = pd.DataFrame(cuboids_l)
coords_df['precursor_cuboid_id'] = coords_df.index

# ... and save them in a file
print()
print('saving {} precursor cuboids to {}'.format(len(coords_df), CUBOIDS_FILE))
info.append(('total_running_time',round(time.time()-start_run,1)))
info.append(('processor',parser.prog))
info.append(('processed', time.ctime()))
content_d = {'coords_df':coords_df, 'metadata':info}
with open(CUBOIDS_FILE, 'wb') as handle:
    pickle.dump(content_d, handle)

stop_run = time.time()
print("total running time ({}): {} seconds".format(parser.prog, round(stop_run-start_run,1)))
from numba import njit
import pandas as pd
import numpy as np
import sys
import peakutils
from ms_deisotope import deconvolute_peaks, averagine, scoring
from ms_deisotope.deconvolution import peak_retention_strategy
import os.path
import argparse
import time
import pickle
from sys import getsizeof
import configparser
from configparser import ExtendedInterpolation
import warnings
from scipy.optimize import OptimizeWarning
import json




# returns a dataframe with the frame properties
def load_frame_properties(converted_db_name):
    # get all the isolation windows
    db_conn = sqlite3.connect(converted_db_name)
    frames_properties_df = pd.read_sql_query("select * from frame_properties order by Id ASC;", db_conn)
    db_conn.close()

    print("loaded {} frame_properties from {}".format(len(frames_properties_df), converted_db_name))
    return frames_properties_df

# find the closest lower ms1 frame_id, and the closest upper ms1 frame_id
def find_closest_ms1_frame_to_rt(frames_properties_df, retention_time_secs):
    # find the frame ids within this range of RT
    df = frames_properties_df[(frames_properties_df.Time > retention_time_secs) & (frames_properties_df.MsMsType == FRAME_TYPE_MS1)]
    if len(df) > 0:
        closest_ms1_frame_above_rt = df.Id.min()
    else:
        # couldn't find an ms1 frame above this RT, so just use the last one
        closest_ms1_frame_above_rt = frames_properties_df[(frames_properties_df.MsMsType == FRAME_TYPE_MS1)].Id.max()
    df = frames_properties_df[(frames_properties_df.Time < retention_time_secs) & (frames_properties_df.MsMsType == FRAME_TYPE_MS1)]
    if len(df) > 0:
        closest_ms1_frame_below_rt = df.Id.max()
    else:
        # couldn't find an ms1 frame below this RT, so just use the first one
        closest_ms1_frame_below_rt = frames_properties_df[(frames_properties_df.MsMsType == FRAME_TYPE_MS1)].Id.min()
    result = {}
    result['below'] = closest_ms1_frame_below_rt
    result['above'] = closest_ms1_frame_above_rt
    return result

# process a precursor cuboid to detect ms1 features
def ms1(precursor_metadata, ms1_points_df, args):
    # find features in the cuboid
    print("finding features for precursor {}".format(precursor_metadata['precursor_id']))
    checked_features_l = []
    features_df = find_features(precursor_metadata, ms1_points_df, args)
    if features_df is not None:
        features_df.reset_index(drop=True, inplace=True)
        for idx,feature in features_df.iterrows():
            feature_d = check_monoisotopic_peak(feature=feature, raw_points=ms1_points_df, idx=idx, total=len(features_df), args=args)
            checked_features_l.append(feature_d)

    checked_features_df = pd.DataFrame(checked_features_l)
    if len(checked_features_df) > 0:
        checked_features_df['monoisotopic_mass'] = (checked_features_df.monoisotopic_mz * checked_features_df.charge) - (args.PROTON_MASS * checked_features_df.charge)
    print("found {} features for precursor {}".format(len(features_df), precursor_metadata['precursor_id']))
    return checked_features_df

# prepare the metadata and raw points for the feature detection
@ray.remote
def detect_ms1_features(precursor_cuboid_row):

    # use the ms1 function to perform the feature detection step
    ms1_args = Namespace()
    ms1_args.experiment_name = args.experiment_name
    ms1_args.run_name = args.run_name
    ms1_args.MS1_PEAK_DELTA = config.getfloat('ms1', 'MS1_PEAK_DELTA')
    ms1_args.SATURATION_INTENSITY = config.getfloat('common', 'SATURATION_INTENSITY')
    ms1_args.MAX_MS1_PEAK_HEIGHT_RATIO_ERROR = config.getfloat('ms1', 'MAX_MS1_PEAK_HEIGHT_RATIO_ERROR')
    ms1_args.PROTON_MASS = config.getfloat('common', 'PROTON_MASS')
    ms1_args.INSTRUMENT_RESOLUTION = config.getfloat('common', 'INSTRUMENT_RESOLUTION')
    ms1_args.NUMBER_OF_STD_DEV_MZ = config.getfloat('ms1', 'NUMBER_OF_STD_DEV_MZ')
    ms1_args.FEATURES_DIR = '{}/features-3did/{}'.format(EXPERIMENT_DIR, args.run_name)
    ms1_args.CARBON_MASS_DIFFERENCE = config.getfloat('common', 'CARBON_MASS_DIFFERENCE')

    # create the metadata record
    cuboid_metadata = {}
    cuboid_metadata['precursor_id'] = row.precursor_cuboid_id
    cuboid_metadata['window_mz_lower'] = row.mz_lower
    cuboid_metadata['window_mz_upper'] = row.mz_upper
    cuboid_metadata['wide_mz_lower'] = row.mz_lower
    cuboid_metadata['wide_mz_upper'] = row.mz_upper
    cuboid_metadata['window_scan_width'] = row.scan_upper - row.scan_lower
    cuboid_metadata['fe_scan_lower'] = row.scan_lower
    cuboid_metadata['fe_scan_upper'] = row.scan_upper
    cuboid_metadata['wide_scan_lower'] = row.scan_lower
    cuboid_metadata['wide_scan_upper'] = row.scan_upper
    cuboid_metadata['wide_rt_lower'] = row.rt_lower
    cuboid_metadata['wide_rt_upper'] = row.rt_upper
    cuboid_metadata['fe_ms1_frame_lower'] = find_closest_ms1_frame_to_rt(frames_properties_df=frames_properties_df, retention_time_secs=row.rt_lower)['below']
    cuboid_metadata['fe_ms1_frame_upper'] = find_closest_ms1_frame_to_rt(frames_properties_df=frames_properties_df, retention_time_secs=row.rt_upper)['above']
    cuboid_metadata['fe_ms2_frame_lower'] = None
    cuboid_metadata['fe_ms2_frame_upper'] = None
    cuboid_metadata['wide_frame_lower'] = find_closest_ms1_frame_to_rt(frames_properties_df=frames_properties_df, retention_time_secs=row.rt_lower)['below']
    cuboid_metadata['wide_frame_upper'] = find_closest_ms1_frame_to_rt(frames_properties_df=frames_properties_df, retention_time_secs=row.rt_upper)['above']
    cuboid_metadata['number_of_windows'] = 1

    # load the raw points
    ms1_points_df = pd.DataFrame.from_dict(row.candidate_region_d)

    # adjust the args
    ms1_args.precursor_id = row.precursor_cuboid_id

    # detect the features
    df = ms1(precursor_metadata=cuboid_metadata, ms1_points_df=ms1_points_df, args=ms1_args)
    return df

###################################
parser = argparse.ArgumentParser(description='Detect the features precursor cuboids found in a run with 3D intensity descent.')
parser.add_argument('-eb','--experiment_base_dir', type=str, default='./experiments', help='Path to the experiments directory.', required=False)
parser.add_argument('-en','--experiment_name', type=str, help='Name of the experiment.', required=True)
parser.add_argument('-rn','--run_name', type=str, help='Name of the run.', required=True)
parser.add_argument('-ini','--ini_file', type=str, default='./open-path/pda/pasef-process-short-gradient.ini', help='Path to the config file.', required=False)
parser.add_argument('-ml','--mz_lower', type=int, default='100', help='Lower limit for m/z.', required=False)
parser.add_argument('-mu','--mz_upper', type=int, default='1700', help='Upper limit for m/z.', required=False)
parser.add_argument('-rl','--rt_lower', type=int, default='1650', help='Lower limit for retention time.', required=False)
parser.add_argument('-ru','--rt_upper', type=int, default='2200', help='Upper limit for retention time.', required=False)
parser.add_argument('-ssm', '--small_set_mode', action='store_true', help='Use a small subset of the data for debugging.')
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

CUBOIDS_DIR = "{}/precursor-cuboids-3did".format(EXPERIMENT_DIR)
CUBOIDS_FILE = '{}/exp-{}-run-{}-mz-{}-{}-precursor-cuboids.pkl'.format(CUBOIDS_DIR, args.experiment_name, args.run_name, args.mz_lower, args.mz_upper)

# check the cuboids file
if not os.path.isfile(CUBOIDS_FILE):
    print("The cuboids file is required but doesn't exist: {}".format(CUBOIDS_FILE))
    sys.exit(1)

# load the precursor cuboids
precursor_cuboids_df = pd.read_pickle(CUBOIDS_FILE)
print('loaded {} precursor cuboids from {}'.format(len(precursor_cuboids_df), CUBOIDS_FILE))

# parse the config file
config = configparser.ConfigParser(interpolation=ExtendedInterpolation())
config.read(args.ini_file)

# load the frame properties
frames_properties_df = load_frame_properties(CONVERTED_DATABASE_NAME)

# set up the output directory
if os.path.exists(ms1_args.FEATURES_DIR):
    shutil.rmtree(ms1_args.FEATURES_DIR)
os.makedirs(ms1_args.FEATURES_DIR)

# find the features in each precursor cuboid
features_l = ray.get([detect_ms1_features.remote(precursor_cuboid_row=row) for row in precursor_cuboids_df.itertuples()])
# join the list of dataframes into a single dataframe
features_df = pd.concat(features_l, axis=0, sort=False)




# consolidate the individual feature files into a single file of features
experiment_features_l = []
subdirs_l = glob('{}/features-3did/*/'.format(EXPERIMENT_DIR))  # get the runs that were processed above
for sd in subdirs_l:
    run_name = sd.split('/')[-2]
    print("consolidating the features found in run {}".format(run_name))
    features_dir = '{}/features-3did/{}'.format(EXPERIMENT_DIR, run_name)

    # consolidate the features found in this run
    run_feature_files = glob("{}/exp-{}-run-{}-features-precursor-*.pkl".format(features_dir, args.experiment_name, run_name))
    run_features_l = []
    print("found {} feature files for the run {}".format(len(run_feature_files), run_name))
    for file in run_feature_files:
        df = pd.read_pickle(file)
        run_features_l.append(df)
    # make a single df from the list of dfs
    run_features_df = pd.concat(run_features_l, axis=0, sort=False)
    run_features_df['run_name'] = run_name
    del run_features_l[:]

    experiment_features_l.append(run_features_df)

# consolidate the features found across the experiment
EXPERIMENT_FEATURES_NAME = '{}/{}'.format(ms1_args.FEATURES_DIR, 'experiment-features.pkl')
experiment_features_df = pd.concat(experiment_features_l, axis=0, sort=False)
print("saving {} experiment features to {}".format(len(experiment_features_df), EXPERIMENT_FEATURES_NAME))
experiment_features_df.to_pickle(EXPERIMENT_FEATURES_NAME)

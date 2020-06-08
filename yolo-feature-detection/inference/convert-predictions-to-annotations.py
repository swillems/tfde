import json
from PIL import Image, ImageDraw, ImageFont
import pandas as pd
import sqlite3
import numpy as np
import glob
import os
import argparse
import time
import sys
import shutil

PIXELS_X = 910
PIXELS_Y = 910  # equal to the number of scan lines

# charge states of interest
MIN_CHARGE = 2
MAX_CHARGE = 4

# number of isotopes of interest
MIN_ISOTOPES = 3
MAX_ISOTOPES = 7

SERVER_URL = "http://spectra-server-lb-1653892276.ap-southeast-2.elb.amazonaws.com"


#####################################
parser = argparse.ArgumentParser(description='Create annotation files for each prediction in a tile set.')
parser.add_argument('-eb','--experiment_base_dir', type=str, default='./experiments', help='Path to the experiments directory.', required=False)
parser.add_argument('-en','--experiment_name', type=str, help='Name of the experiment.', required=True)
parser.add_argument('-tsn','--tile_set_name', type=str, default='tile-set', help='Name of the tile set.', required=False)
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

# check the predictions directory exists
PREDICTIONS_BASE_DIR = '{}/predictions/tile-sets/{}'.format(EXPERIMENT_DIR, args.tile_set_name)
if not os.path.exists(PREDICTIONS_BASE_DIR):
    print("The predictions directory is required but does not exist: {}".format(PREDICTIONS_BASE_DIR))
    sys.exit(1)

# load the predictions file
print('loading the predictions')
prediction_json_file = '{}/batch-inference-tile-set-{}.json'.format(PREDICTIONS_BASE_DIR, args.tile_set_name)
if os.path.isfile(prediction_json_file):
    with open(prediction_json_file) as file:
        prediction_json = json.load(file)
else:
    print("The predictions file is required but does not exist: {}".format(prediction_json_file))
    sys.exit(1)

# check the annotations directory exists
ANNOTATIONS_BASE_DIR = '{}/annotations/tile-sets/{}'.format(EXPERIMENT_DIR, args.tile_set_name)
if os.path.exists(ANNOTATIONS_BASE_DIR):
    shutil.rmtree(ANNOTATIONS_BASE_DIR)
os.makedirs(ANNOTATIONS_BASE_DIR)

# for each prediction in the file, create an annotation
tiles_d = {}
for prediction_idx in range(len(prediction_json)):
    tile_file_name = prediction_json[prediction_idx]['filename']
    base_name = os.path.basename(tile_file_name)
    splits = base_name.split('-')
    run_name = splits[1]
    frame_id = int(splits[3])
    tile_id = int(splits[5].split('.')[0])
    tile_url = '{}/tile/run/{}/tile/{}/frame/{}'.format(SERVER_URL, run_name, tile_id, frame_id)
    print("processing {}".format(base_name))
    predictions = prediction_json[prediction_idx]['objects']
    regions_l = []
    for prediction in predictions:
        feature_class_name = prediction['name']
        splits = feature_class_name.split('-')
        charge = '{}+'.format(splits[1])  # must be a string
        isotopes = splits[3]              # must be a string
        coordinates = prediction['relative_coordinates']
        x = int((coordinates['center_x'] - (coordinates['width'] / 2)) * PIXELS_X)
        y = int((coordinates['center_y'] - (coordinates['height'] / 2)) * PIXELS_Y)
        width = int(coordinates['width'] * PIXELS_X)
        height = int(coordinates['height'] * PIXELS_Y)

        region = {'shape_attributes':{'name':'rect','x':x, 'y':y, 'width':width, 'height':height}, 'region_attributes':{'charge':charge, 'isotopes':isotopes}}
        regions_l.append(region)
    tiles_key = 'run-{}-tile-{}'.format(run_name, tile_id)
    if not tiles_key in tiles_d:
        tiles_d[tiles_key] = {}
    tiles_d[tiles_key]['{}-1'.format(tile_url)] = {'filename':tile_url, 'size':-1, 'regions':regions_l, 'file_attributes':{}}

# write out a separate JSON file for the annotations for each run and tile
print('writing annotation files to {}'.format(ANNOTATIONS_BASE_DIR))
for key, value in tiles_d.items():
    splits = key.split('-')
    run_name = splits[1]
    tile_id = int(splits[3])
    annotations_file_name = '{}/annotations-run-{}-tile-{}.json'.format(ANNOTATIONS_BASE_DIR, run_name, tile_id)
    with open(annotations_file_name, 'w') as outfile:
        json.dump(value, outfile)

print("wrote out {} annotations files to {}".format(len(tiles_d), ANNOTATIONS_BASE_DIR))

# This application creates everything YOLO needs in the training set. The output base directory should be symlinked to ~/darket/data/peptides on the training machine.
import json
from PIL import Image, ImageDraw, ImageChops, ImageFont
import os, shutil
import random
import argparse
import sqlite3
import pandas as pd
import sys
import pickle
import numpy as np
import time
import glob
import ray
import multiprocessing as mp
from collections import Counter

PIXELS_X = 910
PIXELS_Y = 910
MZ_MIN = 100.0
MZ_MAX = 1700.0
MZ_PER_TILE = 18.0
TILES_PER_FRAME = int((MZ_MAX - MZ_MIN) / MZ_PER_TILE) + 1
MIN_TILE_IDX = 0
MAX_TILE_IDX = TILES_PER_FRAME-1

# frame types for PASEF mode
FRAME_TYPE_MS1 = 0
FRAME_TYPE_MS2 = 8

# charge states of interest
MIN_CHARGE = 2
MAX_CHARGE = 4

# number of isotopes of interest
MIN_ISOTOPES = 3
MAX_ISOTOPES = 7

# in YOLO a small object is smaller than 16x16 @ 416x416 image size.
SMALL_OBJECT_W = SMALL_OBJECT_H = 16/416

# allow for some buffer area around the features
MZ_BUFFER = 0.25
SCAN_BUFFER = 20

# get the m/z extent for the specified tile ID
def mz_range_for_tile(tile_id):
    assert (tile_id >= 0) and (tile_id <= TILES_PER_FRAME-1), "tile_id not in range"

    mz_lower = MZ_MIN + (tile_id * MZ_PER_TILE)
    mz_upper = mz_lower + MZ_PER_TILE
    return (mz_lower, mz_upper)

# draw a straight line to exclude the charge-1 cloud
def scan_coords_for_single_charge_region(mz_lower, mz_upper):
    scan_for_mz_lower = -1 * ((1.2 * mz_lower) - 1252)
    scan_for_mz_upper = -1 * ((1.2 * mz_upper) - 1252)
    return (scan_for_mz_lower,scan_for_mz_upper)

def calculate_feature_class(isotopes, charge):
    # for just one class
    return 0

def number_of_feature_classes():
    # for just one class
    return 1

def feature_names():
    # for just one class
    names = []
    names.append('peptide feature')
    return names

@ray.remote
def process_annotation_tile(tile_annotations_d, tile_metadata):
    tile_list = []
    total_objects = 0
    small_objects = 0
    classes_d = {}
    feature_coordinates = []

    # get the tile's metadata
    tile_base_name = tile_annotations_d['file_attributes']['source']['tile']['base_name']
    tile_full_path = tile_metadata.full_path
    tile_id = tile_metadata.tile_id
    frame_id = tile_metadata.frame_id
    run_name = tile_metadata.run_name

    # only process this tile if there are annotations for it i.e. we don't want blank tiles
    tile_regions = tile_annotations_d['regions']
    if len(tile_regions) > 0:
        # copy the tile to the pre-assigned directory
        destination_name = '{}/{}'.format(PRE_ASSIGNED_FILES_DIR, tile_base_name)
        shutil.copyfile(tile_full_path, destination_name)

        # create a feature mask
        mask_im_array = np.zeros([PIXELS_Y+1, PIXELS_X+1, 3], dtype=np.uint8)
        mask = Image.fromarray(mask_im_array.astype('uint8'), 'RGB')
        mask_draw = ImageDraw.Draw(mask)

        # fill in the charge-1 area that we want to preserve
        tile_mz_lower,tile_mz_upper = mz_range_for_tile(tile_id)
        mask_region_y_left,mask_region_y_right = scan_coords_for_single_charge_region(tile_mz_lower, tile_mz_upper)
        mask_draw.polygon(xy=[(0,0), (PIXELS_X,0), (PIXELS_X,mask_region_y_right), (0,mask_region_y_left)], fill='white', outline='white')

        # set up the YOLO annotations text file
        annotations_filename = '{}.txt'.format(os.path.splitext(tile_base_name)[0])
        annotations_path = '{}/{}'.format(PRE_ASSIGNED_FILES_DIR, annotations_filename)
        tile_list.append((tile_base_name, annotations_filename))

        # render each annotation for the tile
        for region in tile_regions:
            shape_attributes = region['shape_attributes']
            x = shape_attributes['x']
            y = shape_attributes['y']
            width = shape_attributes['width']
            height = shape_attributes['height']
            # calculate the YOLO coordinates for the text file
            yolo_x = (x + (width / 2)) / PIXELS_X
            yolo_y = (y + (height / 2)) / PIXELS_Y
            yolo_w = width / PIXELS_X
            yolo_h = height / PIXELS_Y
            # determine the attributes of this feature
            region_attributes = region['region_attributes']
            charge = int(''.join(c for c in region_attributes['charge'] if c in digits))
            isotopes = int(region_attributes['isotopes'])
            # label the charge states we want to detect
            if (charge >= MIN_CHARGE) and (charge <= MAX_CHARGE):
                feature_class = calculate_feature_class(isotopes, charge)
                # keep record of how many instances of each class
                if feature_class in classes_d.keys():
                    classes_d[feature_class] += 1
                else:
                    classes_d[feature_class] = 1
                # add it to the list
                feature_coordinates.append(("{} {:.6f} {:.6f} {:.6f} {:.6f}".format(feature_class, yolo_x, yolo_y, yolo_w, yolo_h)))
                # draw the mask
                mask_draw.rectangle(xy=[(x, y), (x+width, y+height)], fill='white', outline='white')
                # keep record of the 'small' objects
                total_objects += 1
                if (yolo_w <= SMALL_OBJECT_W) or (yolo_h <= SMALL_OBJECT_H):
                    small_objects += 1
            # else:
            #     print("found a charge-{} feature - not included in the training set".format(charge))

        # finish drawing the mask
        del mask_draw
        # ...and save the mask
        mask.save('{}/{}'.format(MASK_FILES_DIR, tile_base_name))

        # apply the mask to the tile
        img = Image.open("{}/{}".format(PRE_ASSIGNED_FILES_DIR, tile_base_name))
        masked_tile = ImageChops.multiply(img, mask)
        masked_tile.save("{}/{}".format(PRE_ASSIGNED_FILES_DIR, tile_base_name))

        # write the annotations text file for this tile
        with open(annotations_path, 'w') as f:
            for item in feature_coordinates:
                f.write("%s\n" % item)

    return {'run_name':run_name, 'tile_id':tile_id, 'frame_id':frame_id, 'number_of_objects':total_objects, 'number_of_small_objects':small_objects, 'tile_list':tile_list, 'classes_d':classes_d}

# determine the number of workers based on the number of available cores and the proportion of the machine to be used
def number_of_workers():
    number_of_cores = mp.cpu_count()
    number_of_workers = int(args.proportion_of_cores_to_use * number_of_cores)
    return number_of_workers


########################################

# python ./tfde/yolo-feature-detection/training/create-training-set-from-tfd.py -eb ~/Downloads/experiments -en dwm-test -rn 190719_Hela_Ecoli_1to1_01 -tidx 34

# meaning of the annotations_source argument
#
# tfe                       annotations from features extracted with TFE; used to create a training set for seeding
# tfe-trained-predictions   annotations from predictions made with a model trained by tfe annotations; used for review and editing in Via
# via                       annotations that have been reviewed and edited by a human expert using Via; used to create a training set for the final model
# via-trained-predictions   annotations from predictions made with the final model trained by via annotations; used for visualisation and evaluation of performance

parser = argparse.ArgumentParser(description='Create a YOLO training set from one or more annotations files.')
parser.add_argument('-eb','--experiment_base_dir', type=str, default='./experiments', help='Path to the experiments directory.', required=False)
parser.add_argument('-en','--experiment_name', type=str, help='Name of the experiment.', required=True)
parser.add_argument('-tln','--tile_list_name', type=str, help='Name of the tile list.', required=True)
parser.add_argument('-as','--annotations_source', type=str, choices=['via','tfe','via-trained-predictions','tfe-trained-predictions'], help='Source of the annotations.', required=True)
parser.add_argument('-rm','--ray_mode', type=str, choices=['local','cluster'], default='cluster', help='The Ray mode to use.', required=False)
parser.add_argument('-pc','--proportion_of_cores_to_use', type=float, default=0.8, help='Proportion of the machine\'s cores to use for this program.', required=False)
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

# the directory for this tile list
TILE_LIST_BASE_DIR = '{}/tile-lists'.format(EXPERIMENT_DIR)
TILE_LIST_DIR = '{}/{}'.format(TILE_LIST_BASE_DIR, args.tile_list_name)
if not os.path.exists(TILE_LIST_DIR):
    print("The tile list directory is required but doesn't exist: {}".format(TILE_LIST_DIR))
    sys.exit(1)

# load the tile list metadata
TILE_LIST_METADATA_FILE_NAME = '{}/metadata.json'.format(TILE_LIST_DIR)
if os.path.isfile(TILE_LIST_METADATA_FILE_NAME):
    print('loading the tile list metadata from {}'.format(TILE_LIST_METADATA_FILE_NAME))
    with open(TILE_LIST_METADATA_FILE_NAME) as json_file:
        tile_list_metadata = json.load(json_file)
        tile_list_df = pd.DataFrame(tile_list_metadata['tile_info'])
        tile_list_df.set_index(keys=['base_name'], drop=True, inplace=True, verify_integrity=True)
        print('there are {} tiles in the list'.format(len(tile_list_df)))
else:
    print("Could not find the tile list's metadata file: {}".format(TILE_LIST_METADATA_FILE_NAME))
    sys.exit(1)

# check the raw tiles base directory exists
TILES_BASE_DIR = '{}/tiles/{}'.format(EXPERIMENT_DIR, tile_list_metadata['arguments']['tile_set_name'])
if not os.path.exists(TILES_BASE_DIR):
    print("The raw tiles base directory is required but does not exist: {}".format(TILES_BASE_DIR))
    sys.exit(1)

# check the annotations directory
ANNOTATIONS_DIR = '{}/annotations-from-{}'.format(TILE_LIST_DIR, args.annotations_source)
if not os.path.exists(EXPERIMENT_DIR):
    print("The annotations directory is required but doesn't exist: {}".format(ANNOTATIONS_DIR))
    sys.exit(1)

# set up the training set directories
TRAINING_SET_BASE_DIR = '{}/training-set'.format(ANNOTATIONS_DIR)
if os.path.exists(TRAINING_SET_BASE_DIR):
    shutil.rmtree(TRAINING_SET_BASE_DIR)
os.makedirs(TRAINING_SET_BASE_DIR)

PRE_ASSIGNED_FILES_DIR = '{}/pre-assigned'.format(TRAINING_SET_BASE_DIR)
if os.path.exists(PRE_ASSIGNED_FILES_DIR):
    shutil.rmtree(PRE_ASSIGNED_FILES_DIR)
os.makedirs(PRE_ASSIGNED_FILES_DIR)

MASK_FILES_DIR = '{}/masks'.format(TRAINING_SET_BASE_DIR)
if os.path.exists(MASK_FILES_DIR):
    shutil.rmtree(MASK_FILES_DIR)
os.makedirs(MASK_FILES_DIR)

SETS_BASE_DIR = '{}/sets'.format(TRAINING_SET_BASE_DIR)
if os.path.exists(SETS_BASE_DIR):
    shutil.rmtree(SETS_BASE_DIR)
os.makedirs(SETS_BASE_DIR)

TRAIN_SET_DIR = '{}/train'.format(SETS_BASE_DIR)
if os.path.exists(TRAIN_SET_DIR):
    shutil.rmtree(TRAIN_SET_DIR)
os.makedirs(TRAIN_SET_DIR)

VAL_SET_DIR = '{}/validation'.format(SETS_BASE_DIR)
if os.path.exists(VAL_SET_DIR):
    shutil.rmtree(VAL_SET_DIR)
os.makedirs(VAL_SET_DIR)

TEST_SET_DIR = '{}/test'.format(SETS_BASE_DIR)
if os.path.exists(TEST_SET_DIR):
    shutil.rmtree(TEST_SET_DIR)
os.makedirs(TEST_SET_DIR)

# determine tile allocation proportions
train_proportion = 0.9
val_proportion = 0.1
test_proportion = 0.0  # there's no need for a test set because mAP is measured on the validation set
print("set proportions: train {}, validation {}, test {}".format(train_proportion, val_proportion, test_proportion))

print("setting up Ray")
if not ray.is_initialized():
    if args.ray_mode == "cluster":
        ray.init(object_store_memory=20000000000,
                    redis_max_memory=25000000000,
                    num_cpus=number_of_workers())
    else:
        ray.init(local_mode=True)

# process all the annotations files
digits = '0123456789'

annotations_file_list = sorted(glob.glob("{}/annotations-run-*-tile-*.json".format(ANNOTATIONS_DIR)))
tile_objects_l = []
for annotation_file_name in annotations_file_list:
    # load the annotations file
    print('processing annotation file {}'.format(annotation_file_name))
    with open(annotation_file_name) as file:
        annotations = json.load(file)
    # for each tile in the annotations (the annotation keys are the frames through RT)
    tile_objects_l += ray.get([process_annotation_tile.remote(tile_annotations_d=annotations[tile_key], tile_metadata=tile_list_df.loc[annotations[tile_key]['file_attributes']['source']['tile']['base_name']]) for tile_key in list(annotations.keys())])

# at this point we have all the referenced tiles in the pre-assigned directory, the charge-1 cloud and all labelled features are masked, and each tile has an annotations file
classes_c = Counter()
small_objects = 0
total_objects = 0
tile_list_l = []
objects_per_tile_l = []
for t in tile_objects_l:
    classes_c += Counter(t['classes_d'])
    total_objects += t['number_of_objects']
    small_objects += t['number_of_small_objects']
    tile_list_l += t['tile_list']
    objects_per_tile_l.append((t['run_name'], t['tile_id'], t['frame_id'], t['number_of_objects']))
classes_d = dict(classes_c)

# display the object counts for each class
names = feature_names()
for c in sorted(classes_d.keys()):
    print("{} objects: {}".format(names[c], classes_d[c]))
if total_objects > 0:
    print("{} out of {} objects ({}%) are small.".format(small_objects, total_objects, round(small_objects/total_objects*100,1)))
else:
    print("note: there are no objects on these tiles")

# display the number of objects per tile
objects_per_tile_df = pd.DataFrame(objects_per_tile_l, columns=['run_name','tile_id','frame_id','number_of_objects'])
objects_per_tile_df.to_pickle('{}/objects_per_tile_df.pkl'.format(TRAINING_SET_BASE_DIR))
print("There are {} tiles with no objects.".format(len(objects_per_tile_df[objects_per_tile_df.number_of_objects == 0])))
print("On average there are {} objects per tile.".format(round(np.mean(objects_per_tile_df.number_of_objects),1)))

# assign the tiles to the training sets
train_n = round(len(tile_list_l) * train_proportion)
val_n = round(len(tile_list_l) * val_proportion)

train_set = random.sample(tile_list_l, train_n)
val_test_set = list(set(tile_list_l) - set(train_set))
val_set = random.sample(val_test_set, val_n)
test_set = list(set(val_test_set) - set(val_set))

print("tile counts - train {}, validation {}, test {}".format(len(train_set), len(val_set), len(test_set)))
number_of_classes = MAX_CHARGE - MIN_CHARGE + 1
max_batches = max(6000, max(2000*number_of_classes, len(train_set)))  # recommendation from AlexeyAB
print("set max_batches={}, steps={},{},{},{}".format(max_batches, int(0.4*max_batches), int(0.6*max_batches), int(0.8*max_batches), int(0.9*max_batches)))

# copy the training set tiles and their annotation files
print()
print("copying the training set to {}".format(TRAIN_SET_DIR))
for file_pair in train_set:
    shutil.copyfile('{}/{}'.format(PRE_ASSIGNED_FILES_DIR, file_pair[0]), '{}/{}'.format(TRAIN_SET_DIR, file_pair[0]))
    shutil.copyfile('{}/{}'.format(PRE_ASSIGNED_FILES_DIR, file_pair[1]), '{}/{}'.format(TRAIN_SET_DIR, file_pair[1]))

# copy the validation set tiles and their annotation files
print("copying the validation set to {}".format(VAL_SET_DIR))
for file_pair in val_set:
    shutil.copyfile('{}/{}'.format(PRE_ASSIGNED_FILES_DIR, file_pair[0]), '{}/{}'.format(VAL_SET_DIR, file_pair[0]))
    shutil.copyfile('{}/{}'.format(PRE_ASSIGNED_FILES_DIR, file_pair[1]), '{}/{}'.format(VAL_SET_DIR, file_pair[1]))

# copy the test set tiles and their annotation files
print("copying the test set to {}".format(TEST_SET_DIR))
for file_pair in test_set:
    shutil.copyfile('{}/{}'.format(PRE_ASSIGNED_FILES_DIR, file_pair[0]), '{}/{}'.format(TEST_SET_DIR, file_pair[0]))
    shutil.copyfile('{}/{}'.format(PRE_ASSIGNED_FILES_DIR, file_pair[1]), '{}/{}'.format(TEST_SET_DIR, file_pair[1]))

# create obj.names, for copying to ./darknet/data, with the object names, each one on a new line
LOCAL_NAMES_FILENAME = "{}/peptides-obj.names".format(TRAINING_SET_BASE_DIR)
print()
print("writing {}".format(LOCAL_NAMES_FILENAME))

# class labels
with open(LOCAL_NAMES_FILENAME, 'w') as f:
    for name in feature_names():
        f.write("{}\n".format(name))

# create obj.data, for copying to ./darknet/data
LOCAL_DATA_FILENAME = "{}/peptides-obj.data".format(TRAINING_SET_BASE_DIR)
print("writing {}".format(LOCAL_DATA_FILENAME))

with open(LOCAL_DATA_FILENAME, 'w') as f:
    f.write("classes={}\n".format(number_of_feature_classes()))
    f.write("train=data/peptides/train.txt\n")
    f.write("valid=data/peptides/validation.txt\n")
    f.write("names=data/peptides/peptides-obj.names\n")
    f.write("backup=backup/\n")

# create the file list for each set
with open('{}/train.txt'.format(TRAINING_SET_BASE_DIR), 'w') as f:
    for file_pair in train_set:
        f.write('data/peptides/sets/train/{}\n'.format(file_pair[0]))

with open('{}/validation.txt'.format(TRAINING_SET_BASE_DIR), 'w') as f:
    for file_pair in val_set:
        f.write('data/peptides/sets/validation/{}\n'.format(file_pair[0]))

with open('{}/test.txt'.format(TRAINING_SET_BASE_DIR), 'w') as f:
    for file_pair in test_set:
        f.write('data/peptides/sets/test/{}\n'.format(file_pair[0]))

stop_run = time.time()
print()
print("total running time ({}): {} seconds".format(parser.prog, round(stop_run-start_run,1)))

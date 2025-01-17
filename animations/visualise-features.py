import pandas as pd
import numpy as np
from matplotlib import colors, pyplot as plt
import os
from PIL import Image, ImageFont, ImageDraw, ImageEnhance
import shutil
import sqlite3
import sys
import json
import argparse
import pickle


def pixel_x_from_mz(mz):
    pixel_x = int((mz - limits['MZ_MIN']) * PIXELS_PER_MZ)
    return pixel_x

def pixel_y_from_scan(scan):
    pixel_y = int((scan - limits['SCAN_MIN']) * PIXELS_PER_SCAN)
    return pixel_y


# generate a tile for each frame, annotating intersecting feature cuboids

###################################
parser = argparse.ArgumentParser(description='Generate a tile for each frame, annotating intersecting feature cuboids.')
parser.add_argument('-eb','--experiment_base_dir', type=str, default='./experiments', help='Path to the experiments directory.', required=False)
parser.add_argument('-en','--experiment_name', type=str, help='Name of the experiment.', required=True)
parser.add_argument('-rn','--run_name', type=str, help='Name of the run.', required=True)
parser.add_argument('-pdm','--precursor_definition_method', type=str, choices=['pasef','3did','mq'], default='none', help='The method used to define the precursor cuboids.', required=False)
parser.add_argument('-fm','--feature_mode', type=str, choices=['detected','identified','none'], default='detected', help='The mode for the features to be displayed.', required=False)
parser.add_argument('-rl','--rt_lower', type=float, default='1650', help='Lower limit for retention time.', required=False)
parser.add_argument('-ru','--rt_upper', type=float, default='2200', help='Upper limit for retention time.', required=False)
parser.add_argument('-ml','--mz_lower', type=float, default='700', help='Lower limit for m/z.', required=False)
parser.add_argument('-mu','--mz_upper', type=float, default='720', help='Upper limit for m/z.', required=False)
parser.add_argument('-sl','--scan_lower', type=int, default='0', help='Lower limit for scan.', required=False)
parser.add_argument('-su','--scan_upper', type=int, default='920', help='Upper limit for scan.', required=False)
parser.add_argument('-tb','--tiles_base_dir', type=str, default='./tiles', help='Path to the output tiles directory.', required=False)
parser.add_argument('-px','--pixels_x', type=int, default='800', help='Number of pixels on the x-axis for the tiles.', required=False)
parser.add_argument('-py','--pixels_y', type=int, default='800', help='Number of pixels on the y-axis for the tiles.', required=False)
parser.add_argument('-minpi','--minimum_pixel_intensity', type=int, default='1', help='Lower edge of the colour map.', required=False)
parser.add_argument('-maxpi','--maximum_pixel_intensity', type=int, default='250', help='Upper edge of the colour map.', required=False)
parser.add_argument('-ccsm','--ccs_marker_each', type=int, default='50', help='Marker period for the CCS dimension.', required=False)
parser.add_argument('-mzm','--mz_marker_each', type=int, default='1', help='Marker period for the m/z dimension.', required=False)
parser.add_argument('-ofl','--omit_feature_labels', action='store_true', help='Don\'t label individual features.')
args = parser.parse_args()

# Print the arguments for the log
info = []
for arg in vars(args):
    info.append((arg, getattr(args, arg)))
print(info)

# add a buffer around the edges of the bounding box
BB_MZ_BUFFER = 0.2
BB_SCAN_BUFFER = 5

# frame types for PASEF mode
FRAME_TYPE_MS1 = 0
FRAME_TYPE_MS2 = 8

# offsets on the sides of a feature's apex
offset_rt_lower = 1
offset_rt_upper = 1

MAXIMUM_Q_VALUE = 0.01

# font paths for overlay labels
UBUNTU_FONT_PATH = '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'
MACOS_FONT_PATH = '/Library/Fonts/Arial.ttf'

TILES_BASE_DIR = '{}/{}-feature-tiles-{}'.format(args.tiles_base_dir, args.feature_mode, args.precursor_definition_method)

EXPERIMENT_DIR = '{}/{}'.format(args.experiment_base_dir, args.experiment_name)
CONVERTED_DATABASE_NAME = '{}/converted-databases/exp-{}-run-{}-converted.sqlite'.format(EXPERIMENT_DIR, args.experiment_name, args.run_name)

if not os.path.exists(EXPERIMENT_DIR):
    print("The experiment directory is required but doesn't exist: {}".format(EXPERIMENT_DIR))
    sys.exit(1)

# check the converted databases directory exists
if not os.path.isfile(CONVERTED_DATABASE_NAME):
    print("The converted database is required but doesn't exist: {}".format(CONVERTED_DATABASE_NAME))
    sys.exit(1)

if args.feature_mode == 'detected':
    FEATURES_DIR = '{}/features-{}'.format(EXPERIMENT_DIR, args.precursor_definition_method)
    FEATURES_FILE = '{}/exp-{}-run-{}-features-{}-dedup.pkl'.format(FEATURES_DIR, args.experiment_name, args.run_name, args.precursor_definition_method)
    # load the features detected
    with open(FEATURES_FILE, 'rb') as handle:
        d = pickle.load(handle)
    features_df = d['features_df']
elif args.feature_mode == 'identified':
    if args.precursor_definition_method == 'pasef':
        FEATURES_DIR = '{}/identifications-pasef'.format(EXPERIMENT_DIR)
        FEATURES_FILE = '{}/exp-{}-identifications-pasef-recalibrated.pkl'.format(FEATURES_DIR, args.experiment_name)
        # load the features detected
        with open(FEATURES_FILE, 'rb') as handle:
            d = pickle.load(handle)
        features_df = d['identifications_df']
        features_df = features_df[(features_df.run_name == args.run_name) & (features_df['percolator q-value'] <= MAXIMUM_Q_VALUE)]
    elif args.precursor_definition_method == 'mq':
        FEATURES_DIR = '{}/features-mq'.format(EXPERIMENT_DIR)
        FEATURES_FILE = '{}/exp-{}-run-{}-features-mq-dedup.pkl'.format(FEATURES_DIR, args.experiment_name, args.run_name)
        # load the features detected
        with open(FEATURES_FILE, 'rb') as handle:
            d = pickle.load(handle)
        features_df = d['features_df']
        # load the percolator output
        MQ_PERCOLATOR_OUTPUT_DIR = '{}/percolator-output-pasef-maxquant'.format(EXPERIMENT_DIR)
        MQ_PERCOLATOR_OUTPUT_FILE_NAME = "{}/{}.percolator.target.psms.txt".format(MQ_PERCOLATOR_OUTPUT_DIR, args.experiment_name)
        mq_psms_df = pd.read_csv(MQ_PERCOLATOR_OUTPUT_FILE_NAME, sep='\t')
        mq_psms_df.rename(columns={'scan': 'mq_index'}, inplace=True)
        mq_psms_df.drop(['charge'], axis=1, inplace=True)
        # remove the poor quality identifications
        mq_psms_df = mq_psms_df[mq_psms_df['peptide mass'] > 0]
        idents_mq_df = pd.merge(features_df, mq_psms_df, how='left', left_on=['mq_index'], right_on=['mq_index'])
        # remove any features that were not identified
        idents_mq_df.dropna(subset=['sequence'], inplace=True)
        features_df = idents_mq_df[(idents_mq_df.raw_file == args.run_name) & (idents_mq_df['percolator q-value'] <= MAXIMUM_Q_VALUE)]
    else: # 3did
        FEATURES_DIR = '{}/features-3did'.format(EXPERIMENT_DIR)
        FEATURES_FILE = '{}/exp-{}-run-{}-features-3did-dedup.pkl'.format(FEATURES_DIR, args.experiment_name, args.run_name)
        # FEATURES_FILE = '{}/exp-{}-run-{}-features-3did.pkl'.format(FEATURES_DIR, args.experiment_name, args.run_name)
        # load the features detected
        with open(FEATURES_FILE, 'rb') as handle:
            d = pickle.load(handle)
        features_df = d['features_df']
else:  # don't display features
    features_df = pd.DataFrame(columns=['monoisotopic_mz','rt_apex','scan_lower','scan_upper'])

# default scope of the visualisation
limits = {'MZ_MIN': args.mz_lower, 'MZ_MAX': args.mz_upper, 'SCAN_MIN': args.scan_lower, 'SCAN_MAX': args.scan_upper, 'RT_MIN': args.rt_lower, 'RT_MAX': args.rt_upper}

PIXELS_PER_MZ = args.pixels_x / (limits['MZ_MAX'] - limits['MZ_MIN'])
PIXELS_PER_SCAN = args.pixels_y / (limits['SCAN_MAX'] - limits['SCAN_MIN'])

print('loading raw data from {}'.format(CONVERTED_DATABASE_NAME))
db_conn = sqlite3.connect(CONVERTED_DATABASE_NAME)
raw_df = pd.read_sql_query("select frame_id,mz,scan,intensity,retention_time_secs from frames where frame_type == {} and mz >= {} and mz <= {} and scan >= {} and scan <= {} and retention_time_secs >= {} and retention_time_secs <= {}".format(FRAME_TYPE_MS1, limits['MZ_MIN'], limits['MZ_MAX'], limits['SCAN_MIN'], limits['SCAN_MAX'], limits['RT_MIN'], limits['RT_MAX']), db_conn)
db_conn.close()

raw_df['pixel_x'] = raw_df.apply(lambda row: pixel_x_from_mz(row.mz), axis=1)
raw_df['pixel_y'] = raw_df.apply(lambda row: pixel_y_from_scan(row.scan), axis=1)

# sum the intensity of raw points that have been assigned to each pixel
pixel_intensity_df = raw_df.groupby(by=['frame_id', 'pixel_x', 'pixel_y'], as_index=False).intensity.sum()
print('intensity range {}..{}'.format(pixel_intensity_df.intensity.min(), pixel_intensity_df.intensity.max()))

# create the colour map to convert intensity to colour
colour_map = plt.get_cmap('ocean')
# colour_map = cm.batlow
norm = colors.LogNorm(vmin=args.minimum_pixel_intensity, vmax=args.maximum_pixel_intensity, clip=True)  # aiming to get good colour variation in the lower range, and clipping everything else

# calculate the colour to represent the intensity
colours_l = []
for i in pixel_intensity_df.intensity.unique():
    colours_l.append((i, colour_map(norm(i), bytes=True)[:3]))
colours_df = pd.DataFrame(colours_l, columns=['intensity','colour'])
pixel_intensity_df = pd.merge(pixel_intensity_df, colours_df, how='left', left_on=['intensity'], right_on=['intensity'])

# create the tiles base directory
if os.path.exists(TILES_BASE_DIR):
    shutil.rmtree(TILES_BASE_DIR)
os.makedirs(TILES_BASE_DIR)

# add a buffer around the edges
x_buffer = 5
y_buffer = 5

# load the font to use for labelling the overlays
if os.path.isfile(UBUNTU_FONT_PATH):
    feature_label_font = ImageFont.truetype(UBUNTU_FONT_PATH, 10)
else:
    feature_label_font = ImageFont.truetype(MACOS_FONT_PATH, 10)

tile_id=1
print('generating the tiles')
for group_name,group_df in pixel_intensity_df.groupby(['frame_id'], as_index=False):
    tile_rt = raw_df[(raw_df.frame_id == group_name)].iloc[0].retention_time_secs

    # create an intensity array
    tile_im_array = np.zeros([args.pixels_y+1, args.pixels_x+1, 3], dtype=np.uint8)  # container for the image
    for r in zip(group_df.pixel_x, group_df.pixel_y, group_df.colour):
        x = r[0]
        y = r[1]
        c = r[2]
        tile_im_array[y,x,:] = c

    # create an image of the intensity array
    tile = Image.fromarray(tile_im_array, 'RGB')
    enhancer_object = ImageEnhance.Brightness(tile)
    tile = enhancer_object.enhance(1.1)

    # get a drawing context for the bounding boxes
    draw = ImageDraw.Draw(tile)

    # draw the CCS markers
    range_l = round(limits['SCAN_MIN'] / args.ccs_marker_each) * args.ccs_marker_each
    range_u = round(limits['SCAN_MAX'] / args.ccs_marker_each) * args.ccs_marker_each
    for marker_scan in np.arange(range_l,range_u+args.ccs_marker_each,args.ccs_marker_each):
        marker_y = pixel_y_from_scan(marker_scan)
        draw.text((10, marker_y-6), str(round(marker_scan)), font=feature_label_font, fill='lawngreen')
        draw.line((0,marker_y, 5,marker_y), fill='lawngreen', width=1)

    # draw the m/z markers
    range_l = round(limits['MZ_MIN'] / args.mz_marker_each) * args.mz_marker_each
    range_u = round(limits['MZ_MAX'] / args.mz_marker_each) * args.mz_marker_each
    for marker_mz in np.arange(range_l,range_u+args.mz_marker_each,args.mz_marker_each):
        marker_x = pixel_x_from_mz(marker_mz)
        draw.text((marker_x-10, 8), str(round(marker_mz)), font=feature_label_font, fill='lawngreen')
        draw.line((marker_x,0, marker_x,5), fill='lawngreen', width=1)

    # draw the tile info
    info_box_x_inset = 200
    info_box_y_inset = 24
    space_per_line = 12
    draw.rectangle(xy=[(args.pixels_x-info_box_x_inset, info_box_y_inset), (args.pixels_x, 3*space_per_line)], fill=(20,20,20), outline=None)
    draw.text((args.pixels_x-info_box_x_inset, (0*space_per_line)+info_box_y_inset), 'feature detection: {}'.format(args.precursor_definition_method.upper()), font=feature_label_font, fill='lawngreen')
    draw.text((args.pixels_x-info_box_x_inset, (1*space_per_line)+info_box_y_inset), 'run: {}'.format(args.run_name), font=feature_label_font, fill='lawngreen')
    draw.text((args.pixels_x-info_box_x_inset, (2*space_per_line)+info_box_y_inset), '{} secs'.format(round(tile_rt,1)), font=feature_label_font, fill='lawngreen')

    # find the intersecting precursor cuboids for this tile; can be partial overlap in the m/z and scan dimensions
    intersecting_features_df = features_df[
                ((features_df.rt_apex-offset_rt_lower) <= tile_rt) & ((features_df.rt_apex+offset_rt_upper) >= tile_rt) & 
                (features_df.monoisotopic_mz >= limits['MZ_MIN']) & (features_df.monoisotopic_mz <= limits['MZ_MAX']) & 
                ((features_df.scan_lower >= limits['SCAN_MIN']) & (features_df.scan_lower <= limits['SCAN_MAX']) | (features_df.scan_upper >= limits['SCAN_MIN']) & (features_df.scan_upper <= limits['SCAN_MAX']))
                ]

    for idx,feature in intersecting_features_df.iterrows():
        envelope = json.loads(feature.envelope)
        x0 = pixel_x_from_mz(envelope[0][0] - BB_MZ_BUFFER)
        x1 = pixel_x_from_mz(envelope[-1][0] + BB_MZ_BUFFER)
        y0 = pixel_y_from_scan(feature.scan_lower - BB_SCAN_BUFFER)
        y1 = pixel_y_from_scan(feature.scan_upper + BB_SCAN_BUFFER)
        # draw the bounding box
        draw.rectangle(xy=[(x0, y0), (x1, y1)], fill=None, outline='deepskyblue')
        if not args.omit_feature_labels:
            # draw the bounding box label
            if (args.feature_mode == 'detected') or (args.precursor_definition_method == '3did'):
                feature_label = 'feature {}'.format(feature.feature_id)
            else:
                feature_label = feature.sequence
            draw.text((x0, y0-(2*space_per_line)), feature_label, font=feature_label_font, fill='lawngreen')
            draw.text((x0, y0-(1*space_per_line)), 'charge {}+'.format(feature.charge), font=feature_label_font, fill='lawngreen')

    # save the tile
    tile_file_name = '{}/tile-{:05d}.png'.format(TILES_BASE_DIR, tile_id)
    tile.save(tile_file_name)
    tile_id += 1

    print('.', end='', flush=True)

print()
print('saved {} tiles to {}'.format(tile_id, TILES_BASE_DIR))

import os
import shutil
import glob
from os.path import expanduser
import sys
import argparse


# Note this script uses the convert command from ImageMagick, installed with:
# sudo apt install imagemagick


###################################
parser = argparse.ArgumentParser(description='Generate a tile for each frame, annotating intersecting feature cuboids.')
parser.add_argument('-fm','--feature_mode', type=str, choices=['detected','identified','none'], default='detected', help='The mode for the features to be displayed.', required=False)
args = parser.parse_args()

# Print the arguments for the log
info = []
for arg in vars(args):
    info.append((arg, getattr(args, arg)))
print(info)

BASE_TILES_DIR = '{}/tiles'.format(expanduser('~'))
OVERLAY_A_BASE_DIR = '{}/{}-feature-tiles-mq'.format(BASE_TILES_DIR, args.feature_mode)
OVERLAY_B_BASE_DIR = '{}/{}-feature-tiles-pasef'.format(BASE_TILES_DIR, args.feature_mode)
OVERLAY_C_BASE_DIR = '{}/{}-feature-tiles-3did'.format(BASE_TILES_DIR, args.feature_mode)

overlay_A_files_l = sorted(glob.glob('{}/*.png'.format(OVERLAY_A_BASE_DIR)), key=lambda x: ( int(x.split('tile-')[1].split('.png')[0]) ))
overlay_B_files_l = sorted(glob.glob('{}/*.png'.format(OVERLAY_B_BASE_DIR)), key=lambda x: ( int(x.split('tile-')[1].split('.png')[0]) ))
overlay_C_files_l = sorted(glob.glob('{}/*.png'.format(OVERLAY_C_BASE_DIR)), key=lambda x: ( int(x.split('tile-')[1].split('.png')[0]) ))
print('found {} tiles in {}, {} tiles in {}, and {} tiles in {}'.format(len(overlay_A_files_l), OVERLAY_A_BASE_DIR, len(overlay_B_files_l), OVERLAY_B_BASE_DIR, len(overlay_C_files_l), OVERLAY_C_BASE_DIR))

if not (len(overlay_A_files_l) == len(overlay_B_files_l) == len(overlay_C_files_l)):
    print('The number of tiles to be composited is not the same')
    sys.exit(1)

# check the composite tiles directory - the composites will be put in the tile list A directory
COMPOSITE_TILE_BASE_DIR = '{}/{}-composite-tiles'.format(BASE_TILES_DIR, args.feature_mode)
if os.path.exists(COMPOSITE_TILE_BASE_DIR):
    shutil.rmtree(COMPOSITE_TILE_BASE_DIR)
os.makedirs(COMPOSITE_TILE_BASE_DIR)

# for each tile in the tile list, find its A and B overlay, and create a composite of them
composite_tile_count = 0
for idx,f in enumerate(overlay_A_files_l):
    overlay_a_name = f
    overlay_b_name = overlay_B_files_l[idx]
    overlay_c_name = overlay_C_files_l[idx]
    print('compositing {},{},{} as tile {}'.format(overlay_a_name, overlay_b_name, overlay_c_name, idx+1))

    composite_name = '{}/composite-tile-{:05d}.png'.format(COMPOSITE_TILE_BASE_DIR, idx+1)

    # make the composite
    if os.path.isfile(overlay_a_name) and os.path.isfile(overlay_b_name) and os.path.isfile(overlay_c_name):
        # composite A+B
        cmd = "convert {} {} +append -background darkgrey -splice 10x0+800+0 {}".format(overlay_a_name, overlay_b_name, composite_name)
        os.system(cmd)
        # composite A+B+C
        cmd = "convert {} {} +append -background darkgrey -splice 10x0+1600+0 {}".format(composite_name, overlay_c_name, composite_name)
        os.system(cmd)
        composite_tile_count += 1
    else:
        if not os.path.isfile(overlay_a_name):
            print('could not find {}'.format(overlay_a_name))
        if not os.path.isfile(overlay_b_name):
            print('could not find {}'.format(overlay_b_name))
        if not os.path.isfile(overlay_c_name):
            print('could not find {}'.format(overlay_c_name))
    # print('.', end='', flush=True)

print()
print('wrote {} composite tiles to {}'.format(composite_tile_count, COMPOSITE_TILE_BASE_DIR))

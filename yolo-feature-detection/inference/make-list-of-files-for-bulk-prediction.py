# This script gets all the tiles for a particular section of m/z and creates a file
# containing their filenames for the purpose of bulk inference.

import glob,os

BASE_DIR = './yolo-train-rt-1000-4200-23-may'
TEST_DIR = '{}/test'.format(BASE_DIR)
TARGET_DIR = 'data/peptides/test'
TILE_ID = 33
FILE_LIST_FILENAME = './test-files.txt'

file_list = sorted(glob.glob("{}/frame-*-tile-{}-mz-*.png".format(TEST_DIR, TILE_ID)))

with open(FILE_LIST_FILENAME, 'w') as f:
    for file in file_list:
        f.write('{}/{}\n'.format(TARGET_DIR, os.path.basename(file)))
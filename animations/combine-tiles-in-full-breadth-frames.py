import glob
import os
from PIL import Image
import sqlite3
import pandas as pd
import shutil

INDIVIDUAL_TILES_DIR = '/home/ubuntu/yolo-train-rt-1000-4200-24-may/overlay'
BASE_DIR = '/home/ubuntu/yolo-movie-rt-3000-3600'
ANIMATION_FRAMES_DIR = '{}/animation-frames'.format(BASE_DIR)

TILE_START = 10
TILE_END = 70
CONVERTED_DATABASE = '/home/ubuntu/HeLa_20KInt-rt-3000-3600-denoised/HeLa_20KInt.sqlite'
RT_LOWER = 3000
RT_UPPER = 3600
MS1_CE = 10

# initialise the directories required
print("preparing directories")
if os.path.exists(BASE_DIR):
    shutil.rmtree(BASE_DIR)
os.makedirs(BASE_DIR)

if os.path.exists(ANIMATION_FRAMES_DIR):
    shutil.rmtree(ANIMATION_FRAMES_DIR)
os.makedirs(ANIMATION_FRAMES_DIR)

db_conn = sqlite3.connect(CONVERTED_DATABASE)
ms1_frame_properties_df = pd.read_sql_query("select frame_id,retention_time_secs from frame_properties where retention_time_secs >= {} and retention_time_secs <= {} and collision_energy == {}".format(RT_LOWER, RT_UPPER, MS1_CE), db_conn)
print("loaded {} frame ids".format(len(ms1_frame_properties_df)))
db_conn.close()

for idx in range(len(ms1_frame_properties_df)):
    print(".", end='')
    frame_id = int(ms1_frame_properties_df.iloc[idx].frame_id)
    file_list = []
    for tile_id in range(TILE_START, TILE_END+1):
        file_list.append(glob.glob("{}/frame-{}-tile-{}-mz-*.png".format(INDIVIDUAL_TILES_DIR, frame_id, tile_id))[0])
    images = list(map(Image.open, file_list))

    widths, heights = zip(*(i.size for i in images))
    total_width = sum(widths)
    max_height = max(heights)

    new_im = Image.new('RGB', (total_width, max_height))

    x_offset = 0
    for im in images:
      new_im.paste(im, (x_offset,0))
      x_offset += im.size[0]

    new_im.save('{}/frame-{:04d}.png'.format(ANIMATION_FRAMES_DIR, idx))

print()
print("wrote {} frames to {}".format(len(ms1_frame_properties_df), ANIMATION_FRAMES_DIR))
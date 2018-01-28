from __future__ import print_function
import sys
import argparse
import time
import pymysql


parser = argparse.ArgumentParser(description='Prepare the database for MS2 feature region peak detection')
parser.add_argument('-db','--database_name', type=str, help='The name of the database.', required=True)
args = parser.parse_args()

# Connect to the database
dest_conn = pymysql.connect(host='mscypher-004', user='root', passwd='password', database="{}".format(args.database_name))
dest_c = dest_conn.cursor()

print("Setting up tables and indexes")

print("Setting up tables and indexes")
dest_c.execute("CREATE OR REPLACE TABLE ms1_feature_region_peaks (feature_id INTEGER, peak_id INTEGER, centroid_mz REAL, centroid_scan REAL, intensity_sum INTEGER, scan_upper INTEGER, scan_lower INTEGER, std_dev_mz REAL, std_dev_scan REAL, rationale TEXT, intensity_max INTEGER, peak_max_mz REAL, peak_max_scan INTEGER, PRIMARY KEY (feature_id, peak_id))")
dest_c.execute("CREATE OR REPLACE TABLE ms1_feature_region_peak_detect_info (item TEXT, value TEXT)")
dest_c.execute("CREATE OR REPLACE TABLE feature_base_peaks (feature_id INTEGER, base_peak_id INTEGER, PRIMARY KEY (feature_id, base_peak_id))")

print("Resetting peak IDs")
dest_c.execute("update summed_ms1_regions set peak_id=0 where peak_id!=0")

dest_conn.commit()
dest_conn.close()

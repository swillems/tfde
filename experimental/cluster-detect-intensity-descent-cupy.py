import sys
import numpy as np
import pandas as pd
import time
import sqlite3
import copy
import argparse
import os.path
import collections
import json

NUMBER_OF_SULPHUR_ATOMS = 3
MAX_NUMBER_OF_PREDICTED_RATIOS = 6

S0_r = np.empty(MAX_NUMBER_OF_PREDICTED_RATIOS+1, dtype=object)
S0_r[1] = [-0.00142320578040, 0.53158267080224, 0.00572776591574, -0.00040226083326, -0.00007968737684]
S0_r[2] = [0.06258138406507, 0.24252967352808, 0.01729736525102, -0.00427641490976, 0.00038011211412]
S0_r[3] = [0.03092092306220, 0.22353930450345, -0.02630395501009, 0.00728183023772, -0.00073155573939]
S0_r[4] = [-0.02490747037406, 0.26363266501679, -0.07330346656184, 0.01876886839392, -0.00176688757979]
S0_r[5] = [-0.19423148776489, 0.45952477474223, -0.18163820209523, 0.04173579115885, -0.00355426505742]
S0_r[6] = [0.04574408690798, -0.05092121193598, 0.13874539944789, -0.04344815868749, 0.00449747222180]

S1_r = np.empty(MAX_NUMBER_OF_PREDICTED_RATIOS+1, dtype=object)
S1_r[1] = [-0.01040584267474, 0.53121149663696, 0.00576913817747, -0.00039325152252, -0.00007954180489]
S1_r[2] = [0.37339166598255, -0.15814640001919, 0.24085046064819, -0.06068695741919, 0.00563606634601]
S1_r[3] = [0.06969331604484, 0.28154425636993, -0.08121643989151, 0.02372741957255, -0.00238998426027]
S1_r[4] = [0.04462649178239, 0.23204790123388, -0.06083969521863, 0.01564282892512, -0.00145145206815]
S1_r[5] = [-0.20727547407753, 0.53536509500863, -0.22521649838170, 0.05180965157326, -0.00439750995163]
S1_r[6] = [0.27169670700251, -0.37192045082925, 0.31939855191976, -0.08668833166842, 0.00822975581940]

S2_r = np.empty(MAX_NUMBER_OF_PREDICTED_RATIOS+1, dtype=object)
S2_r[1] = [-0.01937823810470, 0.53084210514216, 0.00580573751882, -0.00038281138203, -0.00007958217070]
S2_r[2] = [0.68496829280011, -0.54558176102022, 0.44926662609767, -0.11154849560657, 0.01023294598884]
S2_r[3] = [0.04215807391059, 0.40434195078925, -0.15884974959493, 0.04319968814535, -0.00413693825139]
S2_r[4] = [0.14015578207913, 0.14407679007180, -0.01310480312503, 0.00362292256563, -0.00034189078786]
S2_r[5] = [-0.02549241716294, 0.32153542852101, -0.11409513283836, 0.02617210469576, -0.00221816103608]
S2_r[6] = [-0.14490868030324, 0.33629928307361, -0.08223564735018, 0.01023410734015, -0.00027717589598]

model_params = np.empty(NUMBER_OF_SULPHUR_ATOMS, dtype=object)
model_params[0] = S0_r
model_params[1] = S1_r
model_params[2] = S2_r

# Find the ratio of H(peak_number)/H(peak_number-1) for peak_number=1..6
# peak_number = 0 refers to the monoisotopic peak
# number_of_sulphur = number of sulphur atoms in the molecule
def peak_ratio(monoisotopic_mass, peak_number, number_of_sulphur):
    ratio = 0.0
    if (((1 <= peak_number <= 3) & (((number_of_sulphur == 0) & (498 <= monoisotopic_mass <= 3915)) |
                                    ((number_of_sulphur == 1) & (530 <= monoisotopic_mass <= 3947)) |
                                    ((number_of_sulphur == 2) & (562 <= monoisotopic_mass <= 3978)))) |
       ((peak_number == 4) & (((number_of_sulphur == 0) & (907 <= monoisotopic_mass <= 3915)) |
                              ((number_of_sulphur == 1) & (939 <= monoisotopic_mass <= 3947)) |
                              ((number_of_sulphur == 2) & (971 <= monoisotopic_mass <= 3978)))) |
       ((peak_number == 5) & (((number_of_sulphur == 0) & (1219 <= monoisotopic_mass <= 3915)) |
                              ((number_of_sulphur == 1) & (1251 <= monoisotopic_mass <= 3947)) |
                              ((number_of_sulphur == 2) & (1283 <= monoisotopic_mass <= 3978)))) |
       ((peak_number == 6) & (((number_of_sulphur == 0) & (1559 <= monoisotopic_mass <= 3915)) |
                              ((number_of_sulphur == 1) & (1591 <= monoisotopic_mass <= 3947)) |
                              ((number_of_sulphur == 2) & (1623 <= monoisotopic_mass <= 3978))))):
        beta0 = model_params[number_of_sulphur][peak_number][0]
        beta1 = model_params[number_of_sulphur][peak_number][1]
        beta2 = model_params[number_of_sulphur][peak_number][2]
        beta3 = model_params[number_of_sulphur][peak_number][3]
        beta4 = model_params[number_of_sulphur][peak_number][4]
        scaled_m = monoisotopic_mass / 1000.0
        ratio = beta0 + (beta1*scaled_m) + beta2*(scaled_m**2) + beta3*(scaled_m**3) + beta4*(scaled_m**4)
    return ratio


parser = argparse.ArgumentParser(description='A tree descent method for clustering peaks.')
parser.add_argument('-db','--database_name', type=str, help='The name of the source database.', required=True)
parser.add_argument('-fl','--frame_lower', type=int, help='The lower frame number to process.', required=True)
parser.add_argument('-fu','--frame_upper', type=int, help='The upper frame number to process.', required=True)
parser.add_argument('-sl','--scan_lower', type=int, default=0, help='The lower scan number to process.', required=False)
parser.add_argument('-su','--scan_upper', type=int, default=183, help='The upper scan number to process.', required=False)
parser.add_argument('-ir','--isotope_number_right', type=int, default=5, help='Isotope numbers to look on the right.', required=False)
parser.add_argument('-il','--isotope_number_left', type=int, default=2, help='Isotope numbers to look on the left.', required=False)
parser.add_argument('-mi','--minimum_peak_intensity', type=int, default=250, help='Minimum peak intensity to process.', required=False)
parser.add_argument('-mp','--minimum_peaks_nearby', type=int, default=3, help='A peak must have more peaks in its neighbourhood for processing.', required=False)
parser.add_argument('-cs','--maximum_charge_state', type=int, default=5, help='Maximum charge state to look for.', required=False)
parser.add_argument('-sd','--scan_std_dev', type=int, default=1, help='Number of weighted standard deviations to look either side of the intense peak, in the scan dimension.', required=False)
parser.add_argument('-md','--mz_std_dev', type=int, default=2, help='Number of weighted standard deviations to look either side of the intense peak, in the m/z dimension.', required=False)

args = parser.parse_args()

# Store the arguments as metadata in the database for later reference
cluster_detect_info = []
for arg in vars(args):
    cluster_detect_info.append((arg, getattr(args, arg)))

# Connect to the database file
source_conn = sqlite3.connect(args.database_name)
c = source_conn.cursor()

print("Setting up tables and indexes")
c.execute('''DROP TABLE IF EXISTS clusters''')
c.execute('''CREATE TABLE `clusters` ( `frame_id` INTEGER, 
                                        `cluster_id` INTEGER, 
                                        `charge_state` INTEGER, 
                                        'base_isotope_peak_id' INTEGER, 
                                        'base_peak_mz_centroid' REAL, 
                                        'base_peak_mz_std_dev' REAL, 
                                        'base_peak_scan_centroid' REAL, 
                                        'base_peak_scan_std_dev' REAL, 
                                        'monoisotopic_peak_id' INTEGER, 
                                        'mono_peak_mz_centroid' REAL, 
                                        'mono_peak_mz_std_dev' REAL, 
                                        'mono_peak_scan_centroid' REAL, 
                                        'mono_peak_scan_std_dev' REAL, 
                                        'sulphides' INTEGER, 
                                        'fit_error' REAL, 
                                        'rationale' TEXT, 
                                        'state' TEXT, 
                                        'intensity_sum' INTEGER, 
                                        'feature_id' INTEGER, 
                                        PRIMARY KEY(`cluster_id`,`frame_id`) )''')
c.execute('''DROP INDEX IF EXISTS idx_clusters''')
c.execute('''CREATE INDEX idx_clusters ON clusters (frame_id,cluster_id)''')
c.execute("update peaks set cluster_id=0")
c.execute('''DROP TABLE IF EXISTS cluster_detect_info''')
c.execute('''CREATE TABLE cluster_detect_info (item TEXT, value TEXT)''')
source_conn.commit()

DELTA_MZ = 1.003355     # mass difference between Carbon-12 and Carbon-13 isotopes, in Da
PROTON_MASS = 1.007276  # mass of a proton in unified atomic mass units, or Da

clusters = []
start_run = time.time()
for frame_id in range(args.frame_lower, args.frame_upper+1):
    print "Processing frame {}".format(frame_id)
    start_frame = time.time()
    cluster_id = 1
    # Get all the peaks for this frame
    peaks_df = pd.read_sql_query("select peak_id,centroid_mz,centroid_scan,intensity_sum,scan_upper,scan_lower,std_dev_mz,std_dev_scan,intensity_max from peaks where frame_id={} order by peak_id asc;"
        .format(frame_id), source_conn)
    peaks_v = peaks_df.values
    # for i in range(1,6):
    while len(peaks_v) > 0:

        max_intensity_index = peaks_v.argmax(axis=0)[3]
        base_peak_id = int(peaks_v[max_intensity_index][0])
        # print("maximum peak id {}".format(base_peak_id))
        rationale = collections.OrderedDict()
        rationale["maximum peak id"] = base_peak_id

        peak_mz = peaks_v[max_intensity_index][1]
        peak_scan = peaks_v[max_intensity_index][2]
        peak_intensity = int(peaks_v[max_intensity_index][3])
        peak_scan_lower = peak_scan - args.scan_std_dev*peaks_v[max_intensity_index][7]
        peak_scan_upper = peak_scan + args.scan_std_dev*peaks_v[max_intensity_index][7]
        mz_comparison_tolerance = args.mz_std_dev*peaks_v[max_intensity_index][6]
        # print("m/z tolerance: +/- {}".format(mz_comparison_tolerance))
        rationale["m/z tolerance"] = mz_comparison_tolerance

        if peak_intensity < args.minimum_peak_intensity:
            # print "Reached minimum peak intensity - exiting."
            break
        peaks_nearby_indices = np.where((peaks_v[:,1] <= peak_mz + DELTA_MZ*args.isotope_number_right) & (peaks_v[:,1] >= peak_mz - DELTA_MZ*args.isotope_number_left) & (peaks_v[:,2] >= peak_scan_lower) & (peaks_v[:,2] <= peak_scan_upper))[0]
        peaks_nearby = peaks_v[peaks_nearby_indices]
        peaks_nearby_sorted = peaks_nearby[np.argsort(peaks_nearby[:,1])]
        # print("found {} peaks nearby".format(len(peaks_nearby_indices)))
        rationale["peaks nearby"] = len(peaks_nearby_indices)
        cluster_indices = np.empty(0, dtype=int)
        cluster_indices = np.append(cluster_indices, max_intensity_index)
        cluster_peaks = peaks_v[cluster_indices]
        if len(peaks_nearby_indices) >= args.minimum_peaks_nearby:
            # Go through the charge states and isotopes and find the combination with maximum total intensity
            isotope_search_results = [[0, np.empty(0, dtype=int)] for x in range(args.maximum_charge_state+1)]      # array of summed intensity of the peaks, peak IDs
            for charge_state in range(1,args.maximum_charge_state+1):
                # Pick out the peaks belonging to this cluster from the peaks nearby
                # To the right...
                for isotope_number in range(1,args.isotope_number_right+1):
                    mz = peak_mz + (isotope_number*DELTA_MZ/charge_state)
                    cluster_peak_indices_right = np.where((abs(peaks_nearby[:,1] - mz) < mz_comparison_tolerance))[0]
                    if len(cluster_peak_indices_right) > 0:
                        # Add the sum of the peak(s) intensity to this charge state in the matrix
                        isotope_search_results[charge_state][0] += np.sum(peaks_nearby[cluster_peak_indices_right][:,3])
                        # Add the indices of the peak(s) to this charge state in the matrix
                        isotope_search_results[charge_state][1] = np.append(isotope_search_results[charge_state][1], peaks_nearby_indices[cluster_peak_indices_right])
                    else:
                        break

                # To the left...
                for isotope_number in range(1,args.isotope_number_left+1):
                    mz = peak_mz - (isotope_number*DELTA_MZ/charge_state)
                    cluster_peak_indices_left = np.where((abs(peaks_nearby[:,1] - mz) < mz_comparison_tolerance))[0]
                    if len(cluster_peak_indices_left) > 0:
                        # Add the sum of the peak(s) intensity to this charge state in the matrix
                        isotope_search_results[charge_state][0] += np.sum(peaks_nearby[cluster_peak_indices_left][:,3])
                        # Add the indices of the peak(s) to this charge state in the matrix
                        isotope_search_results[charge_state][1] = np.append(isotope_search_results[charge_state][1], peaks_nearby_indices[cluster_peak_indices_left])
                    else:
                        break
            # Find the charge state with the maximum summed intensity
            max_intensity = 0
            max_peaks = np.empty(0, dtype=int)
            charge = 0
            for idx, r in enumerate(isotope_search_results):
                if r[0] > max_intensity:
                    max_intensity = r[0]
                    max_peaks = r[1]
                    charge = idx
            if max_intensity > 0:
                cluster_indices = np.append(cluster_indices, max_peaks)
                # print "cluster id {}, charge {}".format(cluster_id, charge)
                # Reflect the clusters in the peak table of the database
                cluster_indices = np.unique(cluster_indices)
                cluster_peaks = peaks_v[cluster_indices]
                # Find the monoisotopic peak - sort by m/z
                cluster_peaks = cluster_peaks[cluster_peaks[:,1].argsort()] # sorted by m/z
                monoisotopic_peak_id = int(cluster_peaks[0][0])
                monoisotopic_mz = cluster_peaks[0][1]
                # print "cluster peak IDs (sorted by m/z): {}".format(cluster_peaks[:,0].astype(int))
                rationale["initial cluster peak IDs"] = cluster_peaks[:,0].astype(int).tolist()
                # Determine the monoisotopic mass in Da
                monoisotopic_mass = monoisotopic_mz*charge - PROTON_MASS*charge
                # print("mono peak id {}, mono mz {}, mono mass {}".format(monoisotopic_peak_id, monoisotopic_mz, monoisotopic_mass))
                # Find the base peak (maximum intensity)
                # print "peak intensity (before trimming) {}".format(cluster_peaks[:,3])
                rationale["peak intensity before trimming"] = cluster_peaks[:,8].astype(int).tolist()
                base_peak_index = cluster_peaks[:,8].argmax()
                # print "base peak position within cluster {}".format(base_peak_index)
                # Determine the measured height ratios
                observed_height_ratio = np.zeros(len(cluster_peaks))
                for peak_index in range(1, len(cluster_peaks)):
                    # observed_height_ratio[peak_index] = cluster_peaks[peak_index,3] / cluster_peaks[peak_index-1,3]  # summed intensity
                    observed_height_ratio[peak_index] = cluster_peaks[peak_index,8] / cluster_peaks[peak_index-1,8]  # max intensity
                # print "observed peak ratios {}".format(observed_height_ratio)
                # rationale["observed peak ratios"] = observed_height_ratio.tolist()
                # Trim any peaks off the end of the cluster that don't belong (height ratio to the right of the base peak should be less than 1)
                trim_cluster_indices = np.empty(0, dtype=int)
                cluster_positions_of_high_ratios = np.where(observed_height_ratio[:] > 1.0)[0]
                positions_to_trim_right = cluster_positions_of_high_ratios[np.where(cluster_positions_of_high_ratios > base_peak_index)[0]]
                if len(positions_to_trim_right) > 0:
                    first_position_to_trim_right = positions_to_trim_right[0]
                    # print "trim right {}".format(cluster_peaks[first_position_to_trim_right:,3])
                    trim_cluster_indices = np.append(trim_cluster_indices, np.arange(first_position_to_trim_right,len(cluster_peaks)))
                    rationale["IDs trimmed right"] = cluster_peaks[first_position_to_trim_right:,0].astype(int).tolist()
                # Trim any peaks off the beginning of the cluster that don't belong (height ratios to the left of the base peak should be greater than 1)
                cluster_positions_of_low_ratios = np.where(observed_height_ratio[1:] < 1.0)[0]
                positions_to_trim_left = cluster_positions_of_low_ratios[np.where(cluster_positions_of_low_ratios < base_peak_index)[0]]
                if len(positions_to_trim_left) > 0:
                    last_position_to_trim_left = positions_to_trim_left[len(positions_to_trim_left)-1]
                    # print "trim left {}".format(cluster_peaks[:last_position_to_trim_left+1,3])
                    trim_cluster_indices = np.append(trim_cluster_indices, np.arange(0,last_position_to_trim_left+1))
                    rationale["IDs trimmed left"] = cluster_peaks[:last_position_to_trim_left+1,0].astype(int).tolist()

                # Now delete the peaks to be trimmed
                cluster_peaks = np.delete(cluster_peaks, trim_cluster_indices, 0)
                # print "peak intensity (after trimming) {}".format(cluster_peaks[:,3])
                rationale["peak IDs before height ratio error check"] = cluster_peaks[:,0].astype(int).tolist()
                # Need to determine the measured height ratios again
                observed_height_ratio = np.zeros(len(cluster_peaks))
                for peak_index in range(1, len(cluster_peaks)):
                    # observed_height_ratio[peak_index] = cluster_peaks[peak_index,3] / cluster_peaks[peak_index-1,3]  # using summed intensity
                    observed_height_ratio[peak_index] = cluster_peaks[peak_index,8] / cluster_peaks[peak_index-1,8]  # using max intensity
                # print "updated observed peak ratios {}".format(observed_height_ratio)
                # rationale["updated observed peak ratios"] = observed_height_ratio.tolist()

                # Predict the height ratios for this monoisoptopic mass
                predicted_height_ratio = np.zeros((NUMBER_OF_SULPHUR_ATOMS, MAX_NUMBER_OF_PREDICTED_RATIOS+1))
                for sulphur in range(0,NUMBER_OF_SULPHUR_ATOMS):
                    for peak_number in range(1,MAX_NUMBER_OF_PREDICTED_RATIOS+1):
                        predicted_height_ratio[sulphur][peak_number] = peak_ratio(monoisotopic_mass, peak_number=peak_number, number_of_sulphur=sulphur)
                # rationale["predicted peak ratios"] = predicted_height_ratio.tolist()
                # Work out the error for different monoisotopic peaks
                base_peak_index = cluster_peaks[:,3].argmax()
                last_test_mono_index = base_peak_index
                # print "base peak index {}, last mono index {}".format(base_peak_index, last_test_mono_index)
                error = np.zeros((last_test_mono_index+1, NUMBER_OF_SULPHUR_ATOMS))
                for test_mono_index in range(0,last_test_mono_index+1):
                    for sulphur in range(0,3):
                        actual_number_of_predicted_ratios = len(np.where(predicted_height_ratio[sulphur] > 0)[0])
                        end_range = min(len(cluster_peaks)-1, actual_number_of_predicted_ratios)
                        for cluster_peak_index in range(test_mono_index, end_range):
                            observed_index = cluster_peak_index+1
                            predicted_index = cluster_peak_index - test_mono_index + 1
                            error[test_mono_index][sulphur] += (predicted_height_ratio[sulphur][predicted_index] - observed_height_ratio[observed_index])**2 / \
                                predicted_height_ratio[sulphur][predicted_index]
                # print "error {}".format(error)
                rationale["peak height error"] = error.tolist()
                # Find the best monoisotopic peak, preferring to move it only if the fit is poor
                found_good_mono_index = False
                best_monoisotopic_peak_index = 0
                number_of_sulphur = 0
                for test_mono_index in range(0,last_test_mono_index+1):
                    if (min(error[test_mono_index,:]) <= 2):
                        best_monoisotopic_peak_index = test_mono_index
                        number_of_sulphur = error[test_mono_index,:].argmin()
                        found_good_mono_index = True
                        break

                if test_mono_index > 0:
                    rationale["shift monoisotopic"] = "yes"
                else:
                    rationale["shift monoisotopic"] = "no"

                fit_error = error[best_monoisotopic_peak_index,number_of_sulphur]

                if ((found_good_mono_index == True) and (fit_error > 0)):
                    monoisotopic_peak_id = cluster_peaks[best_monoisotopic_peak_index,0].astype(int)
                    rationale["best monoisotopic peak ID"] = monoisotopic_peak_id
                    rationale["number of sulphur"] = int(number_of_sulphur)
                    rationale["final height ratio error"] = fit_error
                    # Trim the peaks before the best monoisotopic
                    cluster_peaks = np.delete(cluster_peaks, np.arange(best_monoisotopic_peak_index), 0)
                    rationale["final peak IDs"] = cluster_peaks[:,0].astype(int).tolist()
                    # Update the cluster indices so we can remove them from the frame
                    for p in cluster_peaks:
                        p_id = int(p[0])
                        # Update the peaks in the peaks table with their cluster ID
                        values = (cluster_id, frame_id, p_id)
                        c.execute("update peaks set cluster_id=? where frame_id=? and peak_id=?", values)
                    # determine some other cluster characteristics before writing it to the database
                    cluster_intensity_sum = sum(cluster_peaks[:,3])

                    base_peak_df = pd.read_sql_query("select centroid_mz,centroid_scan,std_dev_mz,std_dev_scan from peaks where frame_id={} and peak_id={};".format(frame_id, base_peak_id), source_conn)
                    base_peak_v = base_peak_df.values
                    base_peak_mz_centroid = base_peak_v[0][0]
                    base_peak_scan_centroid = base_peak_v[0][1]
                    base_peak_mz_std_dev = base_peak_v[0][2]
                    base_peak_scan_std_dev = base_peak_v[0][3]

                    mono_peak_df = pd.read_sql_query("select centroid_mz,centroid_scan,std_dev_mz,std_dev_scan from peaks where frame_id={} and peak_id={};".format(frame_id, monoisotopic_peak_id), source_conn)
                    mono_peak_v = mono_peak_df.values
                    mono_peak_mz_centroid = mono_peak_v[0][0]
                    mono_peak_scan_centroid = mono_peak_v[0][1]
                    mono_peak_mz_std_dev = mono_peak_v[0][2]
                    mono_peak_scan_std_dev = mono_peak_v[0][3]

                    cluster_feature_id = 0
                    # add the cluster to the list
                    clusters.append((frame_id, 
                                        cluster_id, 
                                        charge, 
                                        base_peak_id, 
                                        base_peak_mz_centroid, 
                                        base_peak_mz_std_dev, 
                                        base_peak_scan_centroid, 
                                        base_peak_scan_std_dev, 
                                        monoisotopic_peak_id, 
                                        mono_peak_mz_centroid, 
                                        mono_peak_mz_std_dev, 
                                        mono_peak_scan_centroid, 
                                        mono_peak_scan_std_dev, 
                                        int(number_of_sulphur), 
                                        fit_error, 
                                        json.dumps(rationale), 
                                        ' ', 
                                        cluster_intensity_sum, 
                                        cluster_feature_id))
                    cluster_id += 1
                # else:
                    # print "Bad fit - disregarding the peak"
            # else:
                # print "Found no isotopic peaks either side of this peak."
        # else:
            # print "Found less than {} peaks nearby - skipping peak search.".format(args.minimum_peaks_nearby)

        # remove the peaks we've processed from the frame
        peaks_v_indices = np.searchsorted(peaks_v[:,0], cluster_peaks[:,0])
        peaks_v = np.delete(peaks_v, peaks_v_indices, 0)
        # print("removed peak ids {} - {} peaks remaining\n".format(cluster_peaks[:,0].astype(int), len(peaks_v)))

    stop_frame = time.time()
    print("{} seconds to process frame - found {} clusters".format(stop_frame-start_frame, cluster_id))

# Write out all the peaks to the database
c.executemany("INSERT INTO clusters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", clusters)
stop_run = time.time()

cluster_detect_info.append(("run processing time (sec)", stop_run-start_run))
cluster_detect_info.append(("processed", time.ctime()))
c.executemany("INSERT INTO cluster_detect_info VALUES (?, ?)", cluster_detect_info)

source_conn.commit()
source_conn.close()

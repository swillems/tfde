import argparse
import pymysql

# Process the command line arguments
parser = argparse.ArgumentParser(description='Generates the commands to run MS2 peak detection in parallel.')
parser.add_argument('-db','--database_name', type=str, help='The name of the database.', required=True)
parser.add_argument('-fbs','--number_of_batches', type=int, default=12, help='The number of batches.', required=False)
parser.add_argument('-ms2ce','--ms2_collision_energy', type=float, help='Collision energy used for MS2.', required=False)
parser.add_argument('-ml','--mz_lower', type=float, help='Lower feature m/z to process.', required=False)
parser.add_argument('-mu','--mz_upper', type=float, help='Upper feature m/z to process.', required=False)
parser.add_argument('-op','--operation', type=str, default='all', help='The operation to perform.', required=False)
parser.add_argument('-os','--operating_system', type=str, default='unix', help='The operating system to target.', required=False)
args = parser.parse_args()

# Connect to the database
source_conn = pymysql.connect(host='mscypher-004', user='root', passwd='password', database="{}".format(args.database_name))
src_c = source_conn.cursor()

# Find out the number of MS1 features in the database
src_c.execute("SELECT MAX(feature_id) FROM features")
row = src_c.fetchone()
number_of_features = int(row[0])

# Close the database connection
source_conn.close()

batch_size = number_of_features / args.number_of_batches
if (batch_size * args.number_of_batches) < number_of_features:
    args.number_of_batches += 1

print("number of features {}, batch size {}, number of batches {}".format(number_of_features, batch_size, args.number_of_batches))

#
# MS2 feature region summing
#
if (args.operation == 'all') or (args.operation == 'sum-ms2-region'):
    if (args.mz_lower is not None) and (args.mz_upper is not None) and (args.ms2_collision_energy is not None):

        print("")
        print("python ./feature-region-ms2-sum-frames-prep.py -db {}".format(args.database_name))

        for i in range(args.number_of_batches):
            first_feature_id = i*batch_size+1
            last_feature_id = first_feature_id + batch_size - 1
            if last_feature_id > number_of_features:
                last_feature_id = number_of_features

            if args.operating_system == 'windows':
                print("start \"Summing {}-{}\" python -u feature-region-ms2-sum-frames.py -db {} -fl {} -fu {} -ms2ce {} -ml {} -mu {}"
                    .format(first_feature_id, last_feature_id, args.database_name, first_feature_id, last_feature_id, args.ms2_collision_energy, args.mz_lower, args.mz_upper))
            else:
                print("nohup python -u ./feature-region-ms2-sum-frames.py -db {} -fl {} -fu {} -ms2ce {} -ml {} -mu {} > ../logs/{}-ms2-feature-region-sum-batch-{}-{}-{}.log 2>&1 &"
                    .format(args.database_name, first_feature_id, last_feature_id, args.ms2_collision_energy, args.mz_lower, args.mz_upper, args.database_name, i, first_feature_id, last_feature_id))
    else:
        print("ERROR: mandatory parameters missing.")

#
# MS2 feature region peak detection
#
if (args.operation == 'all') or (args.operation == 'peak-ms2-region'):

    print("")
    print("python ./feature-region-ms2-peak-detect-prep.py -db {}".format(args.database_name))

    for i in range(args.number_of_batches):
        first_feature_id = i*batch_size+1
        last_feature_id = first_feature_id + batch_size - 1
        if last_feature_id > number_of_features:
            last_feature_id = number_of_features

        if args.operating_system == 'windows':
            print("start \"Summing {}-{}\" python -u feature-region-ms2-peak-detect.py -db {} -fl {} -fu {} -ml {} -mu {}"
                .format(first_feature_id, last_feature_id, args.database_name, first_feature_id, last_feature_id, args.mz_lower, args.mz_upper))
        else:
            print("nohup python -u ./feature-region-ms2-peak-detect.py -db {} -fl {} -fu {} -ml {} -mu {} > ../logs/{}-ms2-feature-region-peak-batch-{}-{}-{}.log 2>&1 &"
                .format(args.database_name, first_feature_id, last_feature_id, args.mz_lower, args.mz_upper, args.database_name, i, first_feature_id, last_feature_id))

#
# MS1 feature region summing
#
if (args.operation == 'all') or (args.operation == 'sum-ms1-region'):
    if (args.mz_lower is not None) and (args.mz_upper is not None):
        print("")
        print("python ./feature-region-ms1-sum-frames-prep.py -db {}".format(args.database_name))
        for i in range(args.number_of_batches):
            first_feature_id = i*batch_size+1
            last_feature_id = first_feature_id + batch_size - 1
            if last_feature_id > number_of_features:
                last_feature_id = number_of_features

            if args.operating_system == 'windows':
                print("start \"Summing {}-{}\" python -u feature-region-ms1-sum-frames.py -db {} -fl {} -fu {} -ml {} -mu {}"
                    .format(first_feature_id, last_feature_id, args.database_name, first_feature_id, last_feature_id, args.mz_lower, args.mz_upper))
            else:
                print("nohup python -u ./feature-region-ms1-sum-frames.py -db {} -fl {} -fu {} -ml {} -mu {} > ../logs/{}-ms1-feature-region-sum-batch-{}-{}-{}.log 2>&1 &"
                    .format(args.database_name, first_feature_id, last_feature_id, args.mz_lower, args.mz_upper, args.database_name, i, first_feature_id, last_feature_id))
    else:
        print("ERROR: mandatory parameters missing.")

#
# MS1 feature region peak detection
#
if (args.operation == 'all') or (args.operation == 'peak-ms1-region'):
    print("")
    print("python ./feature-region-ms1-peak-detect-prep.py -db {}".format(args.database_name))
    for i in range(args.number_of_batches):
        first_feature_id = i*batch_size+1
        last_feature_id = first_feature_id + batch_size - 1
        if last_feature_id > number_of_features:
            last_feature_id = number_of_features

        if args.operating_system == 'windows':
            print("start \"Summing {}-{}\" python -u feature-region-ms1-peak-detect.py -db {} -fl {} -fu {} -ml {} -mu {}"
                .format(first_feature_id, last_feature_id, args.database_name, first_feature_id, last_feature_id, args.mz_lower, args.mz_upper))
        else:
            print("nohup python -u ./feature-region-ms1-peak-detect.py -db {} -fl {} -fu {} -ml {} -mu {} > ../logs/{}-ms1-feature-region-peak-batch-{}-{}-{}.log 2>&1 &"
                .format(args.database_name, first_feature_id, last_feature_id, args.mz_lower, args.mz_upper, args.database_name, i, first_feature_id, last_feature_id))

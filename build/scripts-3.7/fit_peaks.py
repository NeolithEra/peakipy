#!/Users/jacobbrady/virtual_envs/py37/bin/python
"""Fit and deconvolute NMR peaks

    Usage:
        fit_peaks.py <peaklist> <data> <output> [options]

    Arguments:
        <peaklist>                             peaklist output from read_peaklist.py
        <data>                                 2D or pseudo3D NMRPipe data (single file)
        <output>                               output peaklist "<output>.csv" will output CSV
                                               format file, "<output>.tab" will give a tab delimited output
                                               while "<output>.pkl" results in Pandas pickle of DataFrame

    Options:
        -h --help                              Show this page
        -v --version                           Show version

        --dims=<ID,F1,F2>                      Dimension order [default: 0,1,2]

        --max_cluster_size=<max_cluster_size>  Maximum size of cluster to fit (i.e exclude large clusters) [default: 999]

        --lineshape=<G/L/PV>                   lineshape to fit [default: PV]

        --fix=<fraction,sigma,center>          Parameters to fix after initial fit on summed planes [default: fraction,sigma,center]

        --xy_bounds=<x_ppm,y_ppm>              Bound X and Y peak centers during fit [default: None]
                                               This can be set like so --xy_bounds=0.1,0.5

        --vclist=<fname>                       Bruker style vclist [default: None]

        --plot=<dir>                           Whether to plot wireframe fits for each peak
                                               (saved into <dir>) [default: None]

        --show                                 Whether to show (using plt.show()) wireframe
                                               fits for each peak. Only works if --plot is also selected

        --verb                                 Print what's going on


"""
import os
from pathlib import Path

import nmrglue as ng
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

from lmfit import Model
from mpl_toolkits.mplot3d import Axes3D
from docopt import docopt
from skimage.filters import threshold_otsu
from schema import Schema, And, Or, Use, SchemaError

from peakipy.core import fix_params, get_params, fit_first_plane, Pseudo3D, run_log


def norm(data):
    return (data - np.min(data)) / (np.max(data) - np.min(data))

def check_xybounds(x):
    x = x.split(",")
    if len(x) == 2:
        xy_bounds = float(x[0]),float(x[1])
        return xy_bounds
    else:
        print("xy_bounds must be pair of floats e.g. --xy_bounds=0.05,0.5")
        exit()


args = docopt(__doc__)

schema = Schema({
        '<peaklist>': And(os.path.exists, open, error='FILE should be readable'),
        '<data>': And(os.path.exists, Use(ng.pipe.read, error='<data> should be NMRPipe format 2D or 3D cube')),
        '<output>': And(os.path.exists, error='PATH should exist'),
        '--max_cluster_size': And(Use(int), lambda n: 0 < n),
        '--lineshape': Or('PV','L','G', error="Must be either PV, L or G"),
        '--fix': Or(Use(lambda x: [i for i in x.split(',') if (i=='fraction') or (i=='center') or (i=='sigma')])),
        '--dims': Use(lambda n: [int(i) for i in eval(n)], error="--dims should be list of integers e.g. --dims=0,1,2"),
        '--vclist': Or('None', And(os.path.exists, Use(np.genfromtxt, error=f"cannot open {args.get('--vclist')}"))),
        '--plot': Or('None',Use(lambda f: Path(f))),
        '--xy_bounds': Or('None', Use(check_xybounds, error="xy_bounds must be pair of floats e.g. --xy_bounds=0.05,0.5")),
        object: object,
        },
        #ignore_extra_keys=True,
        )

try:
    args = schema.validate(args)
except SchemaError as e:
    exit(e)

lineshape = args.get("--lineshape")
# params to fix
to_fix = args.get("--fix")
#print(to_fix)
verb = args.get("--verb")
if verb:
    print("Using ", args)
log = open("log.txt", "w")

# path to peaklist
peaklist = Path(args.get("<peaklist>"))

# determine filetype
if peaklist.suffix == ".csv":
    peaks = pd.read_csv(peaklist, comment="#")
else:
    # assume that file is a pickle
    peaks = pd.read_pickle(peaklist)

# only include peaks with 'include'
if "include" in peaks.columns:
    pass
else:
    # for compatibility
    peaks["include"] = peaks.apply(lambda _: "yes", axis=1)

if len(peaks[peaks.include != "yes"]) > 0:
    print(f"The following peaks have been exluded:\n{peaks[peaks.include != 'yes']}")
    peaks = peaks[peaks.include == "yes"]

# filter list based on cluster size
max_cluster_size = args.get("--max_cluster_size")
if max_cluster_size == 999:
    max_cluster_size = peaks.MEMCNT.max()
    if peaks.MEMCNT.max() > 10:
        print(
        f"""
            ##################################################################
            You have some clusters of as many as {max_cluster_size} peaks.
            You may want to consider reducing the size of your clusters as the
            fits will struggle.

            Otherwise you can use the --max_cluster_size flag to exclude large
            clusters
            ##################################################################
        """
        )
else:
    max_cluster_size = max_cluster_size

# read vclist
vclist = args.get("--vclist")
if vclist == "None":
    vclist = False
else:
    vclist_data = vclist
    vclist = True

# plot results or not
plot = args.get("--plot")
if plot == "None":
    plot = None
else:
    plot.mkdir(parents=True, exist_ok=True)

# get dims from command line input
dims = args.get("--dims")

# read NMR data
dic, data = args["<data>"]

pseudo3D = Pseudo3D(dic, data, dims)
uc_f1 = pseudo3D.uc_f1
uc_f2 = pseudo3D.uc_f2
uc_dics = {"f1": uc_f1, "f2": uc_f2}

dims = pseudo3D.dims
data = pseudo3D.data
if len(dims) != len(data.shape):
    print(f"Dims are {dims} while data shape is {data.shape}?")
    exit()

noise = threshold_otsu(data)
#print(noise)

# point per Hz
pt_per_hz_f2 = pseudo3D.pt_per_hz_f2
pt_per_hz_f1 = pseudo3D.pt_per_hz_f1

# point per Hz
hz_per_pt_f2 = 1.0 / pt_per_hz_f2
hz_per_pt_f1 = 1.0 / pt_per_hz_f1

# ppm per point
ppm_per_pt_f2 = pseudo3D.ppm_per_pt_f2
ppm_per_pt_f1 = pseudo3D.ppm_per_pt_f1

# point per ppm
pt_per_ppm_f2 = pseudo3D.pt_per_ppm_f2
pt_per_ppm_f1 = pseudo3D.pt_per_ppm_f1

xy_bounds = args.get("--xy_bounds")
if xy_bounds == "None":
    xy_bounds = None
else:
    # convert ppm to points
    xy_bounds[0] = xy_bounds[0] * pt_per_ppm_f2
    xy_bounds[1] = xy_bounds[1] * pt_per_ppm_f1


# convert linewidths from Hz to points in case they were adjusted when running run_check_fits.py
peaks["XW"] = peaks.XW_HZ * pt_per_hz_f2
peaks["YW"] = peaks.YW_HZ * pt_per_hz_f1

# convert peak positions from ppm to points in case they were adjusted running run_check_fits.py
peaks["X_AXIS"] = peaks.X_PPM.apply(lambda x: uc_f2(x, "PPM"))
peaks["Y_AXIS"] = peaks.Y_PPM.apply(lambda x: uc_f1(x, "PPM"))
peaks["X_AXISf"] = peaks.X_PPM.apply(lambda x: uc_f2.f(x, "PPM"))
peaks["Y_AXISf"] = peaks.Y_PPM.apply(lambda x: uc_f1.f(x, "PPM"))

# sum planes for initial fit
summed_planes = data.sum(axis=0)

# for saving data, currently not using errs for center and sigma
amps = []
amp_errs = []

center_xs = []
init_center_xs = []
# center_x_errs = []

center_ys = []
init_center_ys = []
# center_y_errs = []

sigma_ys = []
# sigma_y_errs = []

sigma_xs = []
# sigma_x_errs = []

fractions = []
names = []
indices = []
assign = []
clustids = []
planes = []
x_radii = []
y_radii = []
x_radii_ppm = []
y_radii_ppm = []
lineshapes = []

# group peaks based on CLUSTID
groups = peaks.groupby("CLUSTID")

# iterate over groups of peaks
for name, group in groups:
    #  max cluster size
    len_group = len(group)
    if len_group <= max_cluster_size:
        if len_group == 1:
            peak_str = "peak"
        else:
            peak_str = "peaks"

        print(
        f"""

            ####################################
            Fitting cluster of {len_group} {peak_str}
            ####################################
        """
        )
        # fits sum of all planes first
        first, mask = fit_first_plane(
            group,
            summed_planes,
            # norm(summed_planes),
            uc_dics,
            lineshape=lineshape,
            xy_bounds=xy_bounds,
            plot=plot,
            show=args.get("--show"),
            verbose=verb,
            log=log,
            noise=noise,
        )

        # fix sigma center and fraction parameters
        # could add an option to select params to fix
        if len(to_fix) == 0 or to_fix == "None":
            if verb:
                print("Floating all parameters")
            pass
        else:
            to_fix = to_fix
            if verb:
                print("Fixing parameters:", to_fix)
            fix_params(first.params, to_fix)

        for num, d in enumerate(data):

            first.fit(data=d[mask], params=first.params)
            if verb:
                print(first.fit_report())

            amp, amp_err, name = get_params(first.params, "amplitude")
            cen_x, cen_x_err, cx_name = get_params(first.params, "center_x")
            cen_y, cen_y_err, cy_name = get_params(first.params, "center_y")
            sig_x, sig_x_err, name = get_params(first.params, "sigma_x")
            sig_y, sig_y_err, name = get_params(first.params, "sigma_y")
            frac, frac_err, name = get_params(first.params, "fraction")

            amps.extend(amp)
            amp_errs.extend(amp_err)
            center_xs.extend(cen_x)
            init_center_xs.extend(group.X_AXISf)
            # center_x_errs.extend(cen_x_err)
            center_ys.extend(cen_y)
            init_center_ys.extend(group.Y_AXISf)
            # center_y_errs.extend(cen_y_err)
            sigma_xs.extend(sig_x)
            # sigma_x_errs.extend(sig_x_err)
            sigma_ys.extend(sig_y)
            # sigma_y_errs.extend(sig_y_err)
            fractions.extend(frac)
            # add plane number, this should map to vclist
            planes.extend([num for _ in amp])
            lineshapes.extend([lineshape for _ in amp])
            #  get prefix for fit
            names.extend([i.replace("fraction", "") for i in name])
            assign.extend(group["ASS"])
            clustids.extend(group["CLUSTID"])
            x_radii.extend(group["X_RADIUS"])
            y_radii.extend(group["Y_RADIUS"])
            x_radii_ppm.extend(group["X_RADIUS_PPM"])
            y_radii_ppm.extend(group["Y_RADIUS_PPM"])

df_dic = {
    "fit_prefix": names,
    "assignment": assign,
    "amp": amps,
    "amp_err": amp_errs,
    "center_x": center_xs,
    "init_center_x": init_center_xs,
    # "center_x_err": center_x_errs,
    "center_y": center_ys,
    "init_center_y": init_center_ys,
    # "center_y_err": center_y_errs,
    "sigma_x": sigma_xs,
    # "sigma_x_err": sigma_x_errs,
    "sigma_y": sigma_ys,
    # "sigma_y_err": sigma_y_errs,
    "fraction": fractions,
    "clustid": clustids,
    "plane": planes,
    "x_radius": x_radii,
    "y_radius": y_radii,
    "x_radius_ppm": x_radii_ppm,
    "y_radius_ppm": y_radii_ppm,
    "lineshape": lineshapes,
}

#  make dataframe
df = pd.DataFrame(df_dic)
#  convert sigmas to fwhm
df["fwhm_x"] = df.sigma_x.apply(lambda x: x * 2.0)
df["fwhm_y"] = df.sigma_y.apply(lambda x: x * 2.0)
#  convert values to ppm
df["center_x_ppm"] = df.center_x.apply(lambda x: uc_f2.ppm(x))
df["center_y_ppm"] = df.center_y.apply(lambda x: uc_f1.ppm(x))
df["init_center_x_ppm"] = df.init_center_x.apply(lambda x: uc_f2.ppm(x))
df["init_center_y_ppm"] = df.init_center_y.apply(lambda x: uc_f1.ppm(x))
df["sigma_x_ppm"] = df.sigma_x.apply(lambda x: x * ppm_per_pt_f2)
df["sigma_y_ppm"] = df.sigma_y.apply(lambda x: x * ppm_per_pt_f1)
df["fwhm_x_ppm"] = df.fwhm_x.apply(lambda x: x * ppm_per_pt_f2)
df["fwhm_y_ppm"] = df.fwhm_y.apply(lambda x: x * ppm_per_pt_f1)
df["fwhm_x_hz"] = df.fwhm_x.apply(lambda x: x * hz_per_pt_f2)
df["fwhm_y_hz"] = df.fwhm_y.apply(lambda x: x * hz_per_pt_f1)
# Fill nan values
df.fillna(value=np.nan, inplace=True)
# vclist
if vclist:
    df["vclist"] = df.plane.apply(lambda x: vclist_data[x])
#  output data
output = Path(args["<output>"])
suffix = output.suffix
if suffix == ".csv":
    df.to_csv(output, float_format="%.4f")

elif suffix == ".tab":
    df.to_csv(output, sep="\t", float_format="%.4f")

else:
    df.to_pickle(output)

run_log()
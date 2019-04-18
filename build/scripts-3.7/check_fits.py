""" Plot peakipy fits

    Usage:
        check_fits.py <fits> <nmrdata> [options]

    Options:
        --dims=<id,f1,f2>         Dimension order [default: 0,1,2]

        --clusters=<id1,id2,etc>  Plot selected cluster based on clustid [default: None]
                                  eg. --clusters=1 or --clusters=2,4,6,7

        --outname=<plotname>      Plot name [default: plots.pdf]

        --first                   Only plot first plane
        --show                    Invoke plt.show() for interactive plot


        --rcount=<int>            row count setting for wireplot [default: 50]
        --ccount=<int>            column count setting for wireplot [default: 50]
        --colors=<data,fit>       plot colors [default: k,r]
        
        --help
"""
from sys import exit
from pathlib import Path

import pandas as pd
import numpy as np
import nmrglue as ng
import matplotlib.pyplot as plt
from docopt import docopt
from mpl_toolkits.mplot3d import Axes3D
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.widgets import Button

from peakipy.core import make_mask, pvoigt2d, make_models, Pseudo3D, run_log


if __name__ == "__main__":

    args = docopt(__doc__)

    fits = Path(args.get("<fits>"))
    fits = pd.read_csv(fits)

    dims = args.get("--dims")
    dims = [int(i) for i in eval(dims)]

    data_path = args.get("<nmrdata>")
    dic, data = ng.pipe.read(data_path)
    pseudo3D = Pseudo3D(dic, data, dims)

    outname = args.get("--outname")
    first_only = args.get("--first")
    show = args.get("--show")
    clusters = args.get("--clusters")
    ccount = eval(args.get("--ccount"))
    rcount = eval(args.get("--rcount"))
    colors = args.get("--colors").strip().split(',')

    if type(ccount) == int:
        ccount = ccount
    else:
        raise TypeError("ccount should be an integer")

    if type(rcount) == int:
        rcount = rcount
    else:
        raise TypeError("rcount should be an integer")

    if (type(colors) == list) and len(colors) == 2:
        data_color, fit_color = colors
    else:
        raise TypeError("colors should be valid pair for matplotlib. i.e. g,b or green,blue")

    if clusters == "None":
        pass
    else:
        clusters = [int(i) for i in clusters.split(",")]
        # only use these clusters
        fits = fits[fits.clustid.isin(clusters)]

    groups = fits.groupby("clustid")

    # make plotting meshes
    x = np.arange(pseudo3D.f2_size)
    y = np.arange(pseudo3D.f1_size)
    XY = np.meshgrid(x, y)
    X, Y = XY

    with PdfPages(outname) as pdf:

        for ind, group in groups:

            mask = np.zeros((pseudo3D.f1_size, pseudo3D.f2_size), dtype=bool)
            # sim_data = np.zeros((pseudo3D.f1_size, pseudo3D.f2_size))

            first_plane = group[group.plane == 0]

            x_radius = group.x_radius.max()
            y_radius = group.y_radius.max()
            max_x, min_x = (
                int(np.ceil(max(group.center_x) + x_radius + 1)),
                int(np.floor(min(group.center_x) - x_radius)),
            )
            max_y, min_y = (
                int(np.ceil(max(group.center_y) + y_radius + 1)),
                int(np.floor(min(group.center_y) - y_radius)),
            )
            
            # deal with peaks on the edge of spectrum
            if min_y < 0:
                min_y = 0

            if min_x < 0:
                min_x = 0

            if max_y > pseudo3D.f1_size:
                max_y = pseudo3D.f1_size

            if max_x > pseudo3D.f2_size:
                max_x = pseudo3D.f2_size

            masks = []
            # make masks
            for cx, cy, rx, ry, name in zip(
                first_plane.center_x,
                first_plane.center_y,
                first_plane.x_radius,
                first_plane.y_radius,
                first_plane.assignment,
            ):

                tmp_mask = make_mask(mask, cx, cy, rx, ry)
                mask += tmp_mask
                masks.append(tmp_mask)

            # generate simulated data
            for plane_id, plane in group.groupby("plane"):
                sim_data = np.zeros((pseudo3D.f1_size, pseudo3D.f2_size))
                shape = sim_data.shape
                for amp, c_x, c_y, s_x, s_y, frac in zip(
                    plane.amp,
                    plane.center_x,
                    plane.center_y,
                    plane.sigma_x,
                    plane.sigma_y,
                    plane.fraction,
                ):
                    #print(amp)
                    sim_data += pvoigt2d(XY, amp, c_x, c_y, s_x, s_y, frac).reshape(
                        shape
                    )

                masked_data = pseudo3D.data[plane_id].copy()
                masked_sim_data = sim_data.copy()
                masked_data[~mask] = np.nan
                masked_sim_data[~mask] = np.nan

                fig = plt.figure()
                ax = fig.add_subplot(111, projection="3d")
                # slice out plot area
                x_plot = pseudo3D.uc_f2.ppm(X[min_y:max_y, min_x:max_x])
                y_plot = pseudo3D.uc_f1.ppm(Y[min_y:max_y, min_x:max_x])

                masked_data = masked_data[min_y:max_y, min_x:max_x]
                sim_plot = masked_sim_data[min_y:max_y, min_x:max_x]

                ax.plot_wireframe(
                    x_plot,
                    y_plot,
                    sim_plot,
                    colors=fit_color,
                    linestyle="--",
                    label="fit",
                    rcount=rcount,
                    ccount=ccount,
                )
                ax.plot_wireframe(
                    x_plot,
                    y_plot,
                    masked_data,
                    colors=data_color,
                    linestyle="-",
                    label="data",
                    rcount=rcount,
                    ccount=ccount,
                )
                ax.set_ylabel(pseudo3D.f1_label)
                ax.set_xlabel(pseudo3D.f2_label)

                plt.legend()
                names = ",".join(plane.assignment)
                title = f"Plane={plane_id},Cluster={plane.clustid.iloc[0]}"
                plt.title(title)
                print(f"Plotting: {title}")
                out_str = "Amplitudes\n----------------\n"
                # chi2s = []
                for amp, name, peak_mask in zip(plane.amp, plane.assignment, masks):

                    out_str += f"{name} = {amp:.3e}\n"
                ax.text2D(
                    -0.15, 1.0, out_str, transform=ax.transAxes, fontsize=10, va="top"
                )
                pdf.savefig()

                if show:
                    def exit_program(event):
                        exit()

                    def next_plot(event):
                        plt.close()

                    axexit = plt.axes([0.81, 0.05, 0.1, 0.075])
                    bnexit = Button(axexit, "Exit")
                    bnexit.on_clicked(exit_program)
                    axnext = plt.axes([0.71, 0.05, 0.1, 0.075])
                    bnnext = Button(axnext, "Next")
                    bnnext.on_clicked(next_plot)
                    plt.show()

                plt.close()

                if first_only:
                    break
run_log()
Zero-inflated mixture model.

Algorithm code is contained in ZIMM.py.

Sample usage:

import ZIMM

Z, model_params = ZIMM.fitModel(Y, k)

where Y is the observed zero-inflated data, k is the desired number of clusters, and Z is the cluster assignments.

This code requires pylab, scipy, numpy, and scikits.learn for full functionality.


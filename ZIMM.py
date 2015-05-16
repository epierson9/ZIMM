import numpy as np
from pylab import *
from scipy.optimize import curve_fit, minimize
from copy import deepcopy
from collections import Counter
from sklearn.mixture import GMM
from sklearn.cluster import KMeans
import scipy.special
from sklearn.decomposition import FactorAnalysis
import random
"""
Zero-inflated mixture model (ZIMM). Performs clustering on zero-inflated data. 
Created by Emma Pierson and Christopher Yau. 

Sample usage:
Z, model_params = fitModel(Y, k)

where Y is the observed zero-inflated data, k is the desired number of clusters, and Z is the low-dimensional projection. 
Throughout, K denotes the number of clusters, N denotes the number of samples, and D the number of dimensions. 
"""

def computeIntegrals(mu, sigma, decay_coef):
	"""
	computes E1 (normalization constant), EX (expectation of X), and EX2 (expectation of X^2) for the normal distribution parameterized by mu, sigma multiplied by exp(-decay_coef * x^2) (truncated at 0). 
	Checked (numerical integration). 
	"""
	if not ((sigma > 0).all()):
		raise Exception('Error: Sigma < 0 in %i cases, equal to zero in %i cases' % ((sigma < 0).sum(), (np.abs(sigma) < 1e-6).sum()))
		 
	assert(decay_coef > 0)
	a = (1. + 2 * (sigma**2) * decay_coef)/(2*(sigma**2))
	b = mu / (2 * a * sigma**2)
	D = np.exp(-mu**2 / (2 * sigma**2) + a * b**2)
	C = D * np.sqrt(math.pi) * (scipy.special.erf(b * np.sqrt(a)) + 1) / (2 * np.sqrt(a))
	E1 = C / (np.sqrt(2 * math.pi ) * sigma)
	EX = (D / C) * exp(-a * b**2) / (2 * a) + b
	EX2 = (D / C) * (np.sqrt(math.pi) / (4 * a ** 1.5) - 
	np.sqrt(math.pi) * scipy.special.erf(-b * np.sqrt(a)) / (4 * a ** 1.5) - 
	b * np.exp(-a * b**2) / (2 * a)) + 2 * b * (D / C) * np.exp(-a * b**2) / (2*a) + b**2
	return E1, EX, EX2

def computePosteriorLogZProbability(Y, cluster_mus, cluster_sigmas, cluster_weights, decay_coef, E1):
	"""
	Given the model parameters cluster_mus, cluster_sigmas, cluster_weights, decay_coef, computes the posterior log probability that Y_i is generated by the cluster. 
	E1 is a normalization constant (precomputed and passed in to speed up computation). 
	Returns W, a n_samples x n_clusters matrix of posterior log probabilities. 
	Checked. 
	"""
	N, D = Y.shape
	D, K = cluster_mus.shape
	W = np.zeros([N, K])
	Y_is_zero = np.abs(Y) < 1e-6
	zero_ps = np.nan_to_num(np.dot(Y_is_zero, np.log(E1))) #this matrix contains probabilities if Y_ij = 0
	cluster_weights_matrix = np.tile(np.log(cluster_weights), [N, 1]) #this matrix contains base cluster weights. 
	decay_matrix = np.tile(np.nan_to_num((~Y_is_zero) * np.log(1 - np.exp(-decay_coef * (Y**2)))).sum(axis = 1), [K, 1]).transpose() #this matrix contains decay probabilities for Y_ij != 0
	normalization_matrix = np.dot(~Y_is_zero, np.log(1 / np.sqrt(2 * math.pi * cluster_sigmas ** 2))) #this matrix contains 
	Y3D = np.tile(np.resize(Y, [N, D, 1]), [1, 1, K])
	Yiszero3D = np.tile(np.resize(Y_is_zero, [N, D, 1]), [1, 1, K])
	mu3d = np.tile(cluster_mus, [N, 1, 1])
	sigma3d = np.tile(cluster_sigmas, [N, 1, 1])
	probs = - ((~Yiszero3D) * ((Y3D - mu3d) ** 2 / (2 * sigma3d ** 2))).sum(axis = 1)
	W = cluster_weights_matrix + zero_ps + normalization_matrix + decay_matrix + probs
	return W
			
			
def computeLLFromW(W):
	"""
	computes log likelihood from W in a way that avoids underflow. 
	Checked. 
	"""
	W = deepcopy(W)
	checkNoNans([W])
	ll = 0
	for i in range(len(W)):
		max_val = max(W[i, :])
		W[i, :] = W[i, :] - max_val
		ll = ll + max_val + np.log(sum(np.exp(W[i, :])))
	return ll
		
def Estep(Y, cluster_mus, cluster_sigmas, cluster_weights, decay_coef, verbose = False):
	"""
	Given the observed data Y and the model parameters cluster_mus, cluster_sigmas, cluster_weights, decay_coef
	estimates the requisite quantities in the E-step. 
	Checked. 
	"""
	K = len(cluster_weights)
	N, D = Y.shape
	EX = np.zeros([D, K])
	EX2 = np.zeros([D, K])	
	E1, EX, EX2 = computeIntegrals(cluster_mus, cluster_sigmas, decay_coef)
	checkNoNans([E1, EX, EX2])
	W = computePosteriorLogZProbability(Y, cluster_mus, cluster_sigmas, cluster_weights, decay_coef, E1)
	ll = computeLLFromW(W)
	
	assert(~np.isinf(ll))
	for i in range(N):
		W[i, :] = W[i, :] - W[i, :].max()#normalize to avoid underflow
		W[i, :] = np.exp(W[i, :])
		W[i, :] = W[i, :] / W[i, :].sum()	
	
	checkNoNans([W])
	return W, ll, E1, EX, EX2
	
def Mstep(Y, W, EX, EX2, cluster_mus, cluster_sigmas, cluster_weights, decay_coef):
	"""
	Given the observed data Y and the expectations computed in the E-step, optimizes parameters. 
	"""
	N, D = Y.shape
	D, K = EX.shape

	#optimize weights
	unnormalized_cluster_weights = W.sum(axis = 0)
	unnormalized_weight_matrix = np.tile(unnormalized_cluster_weights, [D, 1])
	
	Y_is_zero = np.abs(Y) < 1e-6
	
	#create 3D versions of matrices (all of them are N x D x K with appropriate dimensions copied.) 
	Yiszero3D = np.tile(np.resize(Y_is_zero, [N, D, 1]), [1, 1, K])
	W3D = np.tile(np.resize(W, [N, 1, K]), [1, D, 1])
	EX3D = np.tile(np.resize(EX, [1, D, K]), [N, 1, 1])
	Y3D = np.tile(np.resize(Y, [N, D, 1]), [1, 1, K])
	EX23D = np.tile(np.resize(EX2, [1, D, K]), [N, 1, 1])
	
	#optimize mus
	nonzero_mus = np.dot(((1 - Y_is_zero) * Y).transpose(), W)
	zero_mus = (Yiszero3D * W3D * EX3D).sum(axis = 0)
	new_cluster_mus = (nonzero_mus + zero_mus) / unnormalized_weight_matrix
	mu3D = np.tile(np.resize(new_cluster_mus, [1, D, K]), [N, 1, 1])
	
	#optimize sigmas given new mus
	new_cluster_sigmas = ((1 - Yiszero3D) * W3D * (Y3D - mu3D) ** 2).sum(axis = 0)
	new_cluster_sigmas += (Yiszero3D * W3D * (EX23D - 2 * mu3D * EX3D + mu3D**2)).sum(axis = 0)
	new_cluster_sigmas = np.sqrt(new_cluster_sigmas / unnormalized_weight_matrix)
	
	#renormalize weights	
	new_cluster_weights = unnormalized_cluster_weights / unnormalized_cluster_weights.sum()		
	
	#optimize decay_coef
	new_decay_coef = decay_coef
	term_1 = sum(Y_is_zero * np.dot(-W, EX2.transpose()))
	new_decay_coef = minimize(lambda x:decayCoefObjectiveFn(x, term_1, Y, Y_is_zero, W), decay_coef, bounds = [[1e-4, np.inf]], options = {'gtol': 1e-8}, jac = True)
	new_decay_coef = new_decay_coef.x[0]
		
	return new_cluster_mus, new_cluster_sigmas, new_cluster_weights, new_decay_coef
	
def decayCoefObjectiveFn(x, term_1, Y, Y_is_zero, W):
	"""
	returns the objective function and the gradient to optimize lambda
	"""
	exp_Y_squared = np.exp(-x * (Y**2))
	log_exp_Y = np.nan_to_num(np.log(1 - exp_Y_squared))#replaces nans with zeros. 
	exp_ratio = np.nan_to_num(exp_Y_squared / (1 - exp_Y_squared))
	obj = x * term_1 + sum(np.dot(W.transpose(), ((1 - Y_is_zero) * log_exp_Y)))
	grad = term_1 + sum(np.dot(W.transpose(), (1 - Y_is_zero) * Y * exp_ratio))
	if type(obj) is np.float64:
		obj = np.array([obj])
	if type(grad) is np.float64:
		grad = np.array([grad])
	return -obj, -grad
	
	
def exp_decay(x, decay_coef):
	"""
	Squared exponential decay function.
	"""
	if decay_coef <= 0:
		return -np.Inf
	return np.exp(-(x**2)*decay_coef)

def initalizeParams(Y, k, method = 'standard'):
	"""
	initializes parameters. 
	By default, (method set to "standard") initializes using a mixture model. 
	If method is set to "high_dimensional", first does dimensionality reduction using factor analysis 
	and then clusters the low-dimensional data. 
	Checked.
	"""
	assert(method in ['high_dimensional', 'standard'])
	if method == 'high_dimensional':
		N, D = Y.shape
		#initialize using factor analysis. 
		model = FactorAnalysis(n_components = 5)
		low_dim_Y = model.fit_transform(Y)
		kmeans_model = KMeans(n_clusters = k)
		z = kmeans_model.fit_predict(low_dim_Y)
		cluster_mus = np.zeros([D, k])
		cluster_weights = np.zeros([k,])
		cluster_sigmas = np.zeros([D, k])
		
		for z_i in sorted(set(z)):
			idxs = (z == z_i)
			cluster_weights[z_i] = np.mean(idxs)
			cluster_Y = Y[idxs, :]
			cluster_Y_is_nonzero = np.abs(cluster_Y) > 1e-6
			cluster_mus[:, z_i] = cluster_Y.sum(axis = 0) / cluster_Y_is_nonzero.sum(axis = 0)
			
			cluster_sigmas[:, z_i] = np.sqrt(((cluster_Y ** 2).sum(axis = 0) - 2 * cluster_mus[:, z_i] * (cluster_Y.sum(axis = 0)) + cluster_mus[:, z_i]**2 * cluster_Y_is_nonzero.sum(axis = 0)) / cluster_Y_is_nonzero.sum(axis = 0))
			for j in range(1, 5):
				assert(np.abs(cluster_sigmas[j, z_i] - np.std(cluster_Y[cluster_Y_is_nonzero[:, j], j])) < 1e-4)		
		
		
	if method == 'standard':
		N, D = Y.shape
		model = GMM(n_components = k)
		imputedY = deepcopy(Y)
		for j in range(D):
			non_zero_idxs = np.abs(Y[:, j]) > 1e-6
			for i in range(N):
				if Y[i][j] == 0:
					imputedY[i][j] = np.random.choice(Y[non_zero_idxs, j])
		model.fit(imputedY)
		cluster_mus = model.means_.transpose()
		cluster_weights = model.weights_
		cluster_sigmas = np.sqrt(model.covars_.transpose())
		
	#now fit decay coefficient
	means = []
	ps = []
	for j in range(D):
		non_zero_idxs = np.abs(Y[:, j]) > 1e-6
		means.append(Y[non_zero_idxs, j].mean())
		ps.append(1 - non_zero_idxs.mean())
	
	
	decay_coef, pcov = curve_fit(exp_decay, means, ps)
	mse = np.mean(np.abs(ps - np.exp(-decay_coef * (np.array(means) ** 2))))
	print 'Decay Coef is %2.3f; MSE is %2.3f' % (decay_coef, mse)
	
	decay_coef = decay_coef[0]

	
	assert(np.all(cluster_sigmas > 0))
	return cluster_mus, cluster_sigmas, cluster_weights, decay_coef
def checkNoNans(matrix_list):
	"""
	Returns false if any of the matrices are nans or infinite. 
	Checked. 
	"""
	for i, M in enumerate(matrix_list):
		if np.any(np.isnan(np.array(M))) or np.any(np.isinf(np.array(M))):
			raise Exception('Matrix index %i in list has a NaN or infinite element' % i)

def fitModel(Y, K, verbose = True, max_iter = 20, ll_delta_thresh = 1e-2):
	"""
	fits the model to data.
	Input: 
	Y: data matrix, n_samples x n_genes
	K: number of clusters
	verbose: if True, print verbose output. 
	max_iter: maximum number of iterations. 
	ll_delta_thresh: if change in likelihood is less than this, terminate. 
	Returns: 
	zhat: the estimated clustering
	params: a dictionary of model parameters. Throughout, we refer to lambda as "decay_coef". 
	Checked. 
	"""
	print 'Running zero-inflated mixture model on data of shape', Y.shape, 'with %i clusters' % K
	#initialize the parameters
	np.random.seed(23)
	cluster_mus, cluster_sigmas, cluster_weights, decay_coef = initalizeParams(Y, K)
	checkNoNans([cluster_mus, cluster_sigmas, cluster_weights, decay_coef])	
	n_iter = 0
	lls = []
	clusterings = []
	while n_iter < max_iter:
		W, ll, E1, EX, EX2 = Estep(Y, cluster_mus, cluster_sigmas, cluster_weights, decay_coef)
		lls.append(ll)
		cluster_mus, cluster_sigmas, cluster_weights, decay_coef = Mstep(Y, W, EX, EX2, cluster_mus, cluster_sigmas, cluster_weights, decay_coef)
		checkNoNans([W, EX, EX2, cluster_mus, cluster_sigmas, cluster_weights, decay_coef])
		zhat = []
		for i in range(len(W)):
			zhat.append(np.argmax(W[i, :]))
		clusterings.append(zhat)
		if verbose:
			print 'Iteration %i: objective is: %2.5f' % (n_iter, ll)
		if len(lls) >= 2: 
			if lls[-1] < lls[-2]:
				raise Exception('Likelihood is not increasing: likelihood at past iteration is %2.5f, current iteration %2.5f' % (lls[-2], lls[-1]))
			if np.abs(lls[-1] - lls[-2])  < ll_delta_thresh:
				if verbose:
					print 'Change in likelihood too small; terminating loop'
				break
		if n_iter >= max_iter:
			if verbose:
				print 'Maximum number of iterations reached; terminating loop'
			break
		n_iter += 1	
	W, ll, E1, EX, EX2 = Estep(Y, cluster_mus, cluster_sigmas, cluster_weights, decay_coef)
	zhat = []
	for i in range(len(W)):
		zhat.append(np.argmax(W[i, :]))
	params = {'cluster_mus':cluster_mus, 'cluster_sigmas': cluster_sigmas, 'cluster_weights':cluster_weights, 'decay_coef':decay_coef, 'lls':np.array(lls)}
	return zhat, params



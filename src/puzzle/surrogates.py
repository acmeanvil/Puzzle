"""Models of a response from a few solved points (ADR 0063 phase 3), in numpy and scipy:
a Gaussian process (for Bayesian optimisation's expected improvement), a quadratic
surface and radial basis functions (response surfaces), and their leave-one-out error.

Inputs are the design variables scaled to 0..1. Everything here is deterministic, so an
optimisation built on it replays to the same points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# --- the Gaussian process ------------------------------------------------------------


def _matern(a: np.ndarray, b: np.ndarray, scales: np.ndarray) -> np.ndarray:
    """Matérn 5/2 correlation between rows of `a` and `b`, a length scale per input."""
    d = np.sqrt(np.maximum(((a[:, None, :] - b[None, :, :]) / scales) ** 2, 0).sum(-1))
    r = math.sqrt(5) * d
    return (1 + r + r * r / 3) * np.exp(-r)


@dataclass
class Gaussian:
    """A fitted Gaussian process: the mean and standard deviation of the response at
    new points. The response is standardised; the kernel's length scales (one per
    variable), signal and noise come from the marginal likelihood."""

    x: np.ndarray
    mean: float
    std: float
    scales: np.ndarray
    signal: float
    noise: float
    chol: np.ndarray
    alpha: np.ndarray

    def predict(self, at: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        k = self.signal * _matern(at, self.x, self.scales)
        mu = k @ self.alpha
        v = np.linalg.solve(self.chol, k.T)
        var = np.maximum(self.signal - (v * v).sum(0), 1e-12)
        return self.mean + self.std * mu, self.std * np.sqrt(var)


def _factor(x, y, scales, signal, noise):
    k = signal * _matern(x, x, scales) + (noise + 1e-10) * np.eye(len(x))
    chol = np.linalg.cholesky(k)
    alpha = np.linalg.solve(chol.T, np.linalg.solve(chol, y))
    return chol, alpha


def fit_gaussian(x: np.ndarray, y: np.ndarray) -> Gaussian:
    """A Gaussian process through (x, y): hyperparameters by maximum marginal
    likelihood (L-BFGS-B from two fixed starts, so the same data give the same fit)."""
    from scipy.optimize import minimize

    x, y = np.asarray(x, float), np.asarray(y, float)
    mean, std = float(y.mean()), float(y.std()) or 1.0
    z = (y - mean) / std
    d = x.shape[1]

    def unpack(t):
        return np.exp(t[:d]), math.exp(t[d]), math.exp(t[d + 1])

    def nll(t):
        scales, signal, noise = unpack(t)
        try:
            chol, alpha = _factor(x, z, scales, signal, noise)
        except np.linalg.LinAlgError:
            return 1e10
        return float(0.5 * z @ alpha + np.log(np.diag(chol)).sum())

    # length scales 0.01..10 of the range, signal 0.05..20, noise 1e-8..0.5 (the
    # standardised response): a remeshed point's scatter is noise, not a feature
    bounds = [(math.log(0.01), math.log(10.0))] * d + [(math.log(0.05), math.log(20.0)),
                                                      (math.log(1e-8), math.log(0.5))]  # fmt: skip
    best = None
    for start in (0.2, 0.7):
        t0 = np.array([math.log(start)] * d + [0.0, math.log(1e-4)])
        got = minimize(nll, t0, method="L-BFGS-B", bounds=bounds)
        if best is None or got.fun < best.fun:
            best = got
    scales, signal, noise = unpack(best.x)
    chol, alpha = _factor(x, z, scales, signal, noise)
    return Gaussian(x, mean, std, scales, signal, noise, chol, alpha)


def expected_improvement(mu: np.ndarray, sigma: np.ndarray, best: float) -> np.ndarray:
    """How much below `best` a minimised response is expected to fall."""
    from scipy.stats import norm

    z = (best - mu) / sigma
    return sigma * (z * norm.cdf(z) + norm.pdf(z))


def feasibility(mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """The probability a margin (positive holds) is at least zero."""
    from scipy.stats import norm

    return norm.cdf(mu / sigma)


# --- response surfaces ----------------------------------------------------------------


def quadratic_terms(d: int) -> int:
    """A full quadratic's coefficients over d variables: 1, each, and each pair."""
    return (d + 1) * (d + 2) // 2


def _features(x: np.ndarray) -> np.ndarray:
    n, d = x.shape
    cols = [np.ones(n)] + [x[:, i] for i in range(d)]
    cols += [x[:, i] * x[:, j] for i in range(d) for j in range(i, d)]
    return np.column_stack(cols)


class Surface:
    """A response surface through solved points: `quadratic` (least squares) or `rbf`
    (scipy's thin-plate radial basis functions with a linear part, through every
    point)."""

    def __init__(self, kind: str, x: np.ndarray, y: np.ndarray) -> None:
        self.kind = kind
        x, y = np.asarray(x, float), np.asarray(y, float)
        if kind == "quadratic":
            self.coef = np.linalg.lstsq(_features(x), y, rcond=None)[0]
        else:
            from scipy.interpolate import RBFInterpolator

            self.rbf = RBFInterpolator(x, y, kernel="thin_plate_spline", degree=1)

    def __call__(self, at: np.ndarray) -> np.ndarray:
        at = np.atleast_2d(np.asarray(at, float))
        return _features(at) @ self.coef if self.kind == "quadratic" else self.rbf(at)


def leave_one_out(kind: str, x: np.ndarray, y: np.ndarray) -> float | None:
    """The surface's leave-one-out error: each point predicted by a surface fitted to
    the others, the RMS of those errors as a share of the response's range (0.02 = 2 %).
    None when too few points to tell."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    need = quadratic_terms(x.shape[1]) + 1 if kind == "quadratic" else x.shape[1] + 3
    span = float(np.ptp(y))
    if len(y) < need or span == 0:
        return None
    errors = []
    for i in range(len(y)):
        keep = np.arange(len(y)) != i
        errors.append(float(Surface(kind, x[keep], y[keep])(x[i])[0]) - y[i])
    return math.sqrt(float(np.mean(np.square(errors)))) / span

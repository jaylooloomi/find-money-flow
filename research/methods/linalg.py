"""Pure-Python pieces for the method experiments: correlation matrix, Jacobi eigen-decomposition,
Marchenko–Pastur bound, DTW, average-linkage clustering, the normal CDF. Sizes here are ~150 stocks ×
~160 points, small enough for plain loops."""

from __future__ import annotations

import math


def standardize(rows: list[list[float | None]]) -> list[list[float]]:
    """Each series to mean 0, sd 1 over its known points; a missing point becomes 0 (the mean)."""
    out = []
    for r in rows:
        xs = [x for x in r if x is not None]
        m = sum(xs) / len(xs) if xs else 0.0
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) if xs else 0.0
        out.append([((x - m) / sd if sd else 0.0) if x is not None else 0.0 for x in r])
    return out


def corr_matrix(z: list[list[float]]) -> list[list[float]]:
    n, t = len(z), len(z[0])
    c = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            v = sum(a * b for a, b in zip(z[i], z[j])) / t
            c[i][j] = c[j][i] = v
    return c


def jacobi_eigen(a: list[list[float]], sweeps: int = 12, tol: float = 1e-9) -> tuple[list[float], list[list[float]]]:
    """Eigenvalues (descending) and eigenvectors (as rows) of a symmetric matrix (cyclic Jacobi)."""
    n = len(a)
    a = [row[:] for row in a]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        off = sum(a[i][j] ** 2 for i in range(n) for j in range(i + 1, n))
        if off < tol:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-12:
                    continue
                theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = (1 if theta >= 0 else -1) / (abs(theta) + math.sqrt(theta * theta + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    vals = [a[i][i] for i in range(n)]
    order = sorted(range(n), key=lambda i: -vals[i])
    return [vals[i] for i in order], [[v[k][i] for k in range(n)] for i in order]


def mp_upper(n: int, t: int) -> float:
    """Marchenko–Pastur upper edge for the correlation matrix of n pure-noise series of length t."""
    q = n / t
    return (1 + math.sqrt(q)) ** 2


def remove_factors(z: list[list[float]], vecs: list[list[float]], k: int) -> list[list[float]]:
    """Residuals of each standardized series after regressing out the first k principal factors."""
    t = len(z[0])
    factors = []
    for v in vecs[:k]:
        f = [sum(v[i] * z[i][s] for i in range(len(z))) for s in range(t)]
        norm = math.sqrt(sum(x * x for x in f)) or 1.0
        factors.append([x / norm for x in f])  # orthonormal (eigenvectors are)
    out = []
    for row in z:
        r = row[:]
        for f in factors:
            b = sum(a * x for a, x in zip(r, f))
            r = [a - b * x for a, x in zip(r, f)]
        out.append(r)
    return out


def dtw(a: list[float], b: list[float], window: int = 6) -> float:
    """DTW distance with a Sakoe–Chiba band of `window` steps."""
    n, m = len(a), len(b)
    inf = float("inf")
    prev = [inf] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur = [inf] * (m + 1)
        for j in range(max(1, i - window), min(m, i + window) + 1):
            cost = (a[i - 1] - b[j - 1]) ** 2
            cur[j] = cost + min(prev[j], prev[j - 1], cur[j - 1])
        prev = cur
    return math.sqrt(prev[m])


def average_linkage(dist: list[list[float]], cut: float) -> list[list[int]]:
    """Clusters (index lists) from average-linkage agglomeration stopped at distance `cut`."""
    clusters = [[i] for i in range(len(dist))]

    def d(a, b):
        return sum(dist[i][j] for i in a for j in b) / (len(a) * len(b))

    while len(clusters) > 1:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                x = d(clusters[i], clusters[j])
                if best is None or x < best[0]:
                    best = (x, i, j)
        if best[0] > cut:
            break
        _, i, j = best
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]
    return clusters


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

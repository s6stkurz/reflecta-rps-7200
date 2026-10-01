"""Fit the film models to what real prescans measure.

- **c41** against the real compose frames (`fpn.stats` on their labels): the
  picture's density quantiles against base, the share brighter than base, and
  the base grain. These are the only real frames the fit may see.
- **bw** and **slide** against the numbers already measured on real prescans
  (Stefan, 2026-09-30: those films' only real frames are their test sets, so
  only these few global numbers are taken from them, never a picture):
    bw     picture p1 / p50 / p99 of 1-3 / 7-19 / 19-24 counts, grain 7-10%;
    slide  picture median 14 / 10 / 14 counts, p99.5 about 27 / 19 / 25.

Each film gets a fixed bench of synthetic frames from the training rolls
(base on one side, so both base and picture show), whose exposures are
computed once; every trial re-develops them with new parameters and the same
noise, so the objective is smooth enough for Nelder-Mead.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

import numpy as np

from fpn import compose, device, film, stats

# -- the targets that are numbers rather than frames ---------------------------
BW_TARGET = {"p1": 2.0, "p50": 13.0, "p99": 21.5, "spread": 0.085}
SLIDE_TARGET = {"p50": (14.0, 10.0, 14.0), "p99.5": (27.0, 19.0, 25.0)}


@dataclass
class Bench:
    film: str
    recipes: list[compose.Recipe]
    exposures: list[tuple[np.ndarray, np.ndarray]]
    pool: device.ReferencePool

    def develop(self, params: film.FilmParams) -> list[np.ndarray]:
        out = []
        for r, (e, g) in zip(self.recipes, self.exposures):
            rng = np.random.default_rng([r.seed, 2])
            roll = film.draw_roll(r.film, params, rng)   # the base follows the params
            out.append(compose.develop(e, g, r, self.pool, params, roll))
        return out


def make_bench(film_name: str, triples: list[tuple[str, str, str]],
               grid: Callable[[str], np.ndarray], pool: device.ReferencePool,
               seed: int) -> Bench:
    recipes, exposures = [], []
    for i, triple in enumerate(triples):
        rng = np.random.default_rng([seed, i])
        base = float(rng.uniform(20.0, 60.0))
        r = compose.Recipe(
            film=film_name, pictures=triple, frame_width=float(rng.normal(435.5, 0.7)),
            gaps=compose.draw_gaps(rng), left_edge=base, tilt=0.0,
            softness=float(rng.uniform(0.35, 0.8)), top=1.75,
            stops=(float(rng.normal(0, 0.5)), float(rng.normal(0, 0.5)), float(rng.normal(0, 0.5))),
            strip_end="", lead=0.0, rows=287, calibration=pool.pick(rng).entry,
            seed=int(seed * 100000 + i))
        recipes.append(r)
        exposures.append(compose.exposure_of(r, grid))
    return Bench(film_name, recipes, exposures, pool)


def bench_stats(bench: Bench, images: list[np.ndarray]) -> dict:
    rng = np.random.default_rng(0)
    frames = [stats.measure(im, compose.truth(r), rng) for im, r in zip(images, bench.recipes)]
    bases = [f.base if f.base is not None else np.ones(3) for f in frames]
    return stats.summarise(frames, bases)


# -- Nelder-Mead, bounded by clipping ------------------------------------------

def nelder_mead(f: Callable[[np.ndarray], float], x0: np.ndarray, lo: np.ndarray,
                hi: np.ndarray, steps: np.ndarray, iterations: int,
                log: Callable[[str], None] = print) -> tuple[np.ndarray, float]:
    def g(x: np.ndarray) -> float:
        return f(np.clip(x, lo, hi))

    n = len(x0)
    simplex = [np.clip(x0, lo, hi)] + [np.clip(x0 + np.eye(n)[i] * steps[i], lo, hi)
                                       for i in range(n)]
    values = [g(x) for x in simplex]
    for it in range(iterations):
        order = np.argsort(values)
        simplex = [simplex[i] for i in order]
        values = [values[i] for i in order]
        if it % 10 == 0:
            log(f"  iteration {it}: loss {values[0]:.4f}")
        centroid = np.mean(simplex[:-1], axis=0)
        worst = simplex[-1]
        reflected = centroid + (centroid - worst)
        fr = g(reflected)
        if fr < values[0]:
            expanded = centroid + 2.0 * (centroid - worst)
            fe = g(expanded)
            simplex[-1], values[-1] = (expanded, fe) if fe < fr else (reflected, fr)
        elif fr < values[-2]:
            simplex[-1], values[-1] = reflected, fr
        else:
            contracted = centroid + 0.5 * (worst - centroid)
            fc = g(contracted)
            if fc < values[-1]:
                simplex[-1], values[-1] = contracted, fc
            else:
                best = simplex[0]
                simplex = [best] + [best + 0.5 * (x - best) for x in simplex[1:]]
                values = [values[0]] + [g(x) for x in simplex[1:]]
    i = int(np.argmin(values))
    return np.clip(simplex[i], lo, hi), float(values[i])


# -- the three fits ------------------------------------------------------------

def _c41_params(base: film.FilmParams, x: np.ndarray) -> film.FilmParams:
    c = replace(base.c41, gamma=(float(x[0]), float(x[1]), float(x[2])),
                toe=float(10 ** x[3]), toe_sharpness=float(x[4]),
                cross=(float(x[5]), float(x[6]), float(x[7])),
                grain=(float(x[8]), float(x[9]), float(x[10])), stops_mean=float(x[11]))
    return replace(base, c41=c)


def _c41_loss(s: dict, real: dict) -> float:
    q_syn = np.array([s["picture_log2_vs_base"][str(q)] for q in stats.QUANTILES])
    q_real = np.array([real["picture_log2_vs_base"][str(q)] for q in stats.QUANTILES])
    loss = float(np.abs(q_syn - q_real).mean())
    loss += 5.0 * float(np.abs(np.array(s["picture_above_base"]) -
                               np.array(real["picture_above_base"])).sum())
    loss += 10.0 * float(np.abs(np.array(s["base_spread"]) - np.array(real["base_spread"])).sum())
    return loss


def fit_c41(bench: Bench, real: dict, iterations: int, log=print) -> tuple[film.FilmParams, dict]:
    p0 = film.FilmParams()
    c = p0.c41
    x0 = np.array([*c.gamma, np.log10(c.toe), c.toe_sharpness, *c.cross, *c.grain, c.stops_mean])
    lo = np.array([0.15, 0.15, 0.15, -3.0, 1.0, -0.4, -0.4, -0.4, 0.001, 0.001, 0.001, -4.0])
    hi = np.array([1.2, 1.2, 1.2, -0.2, 10.0, 0.3, 0.3, 0.3, 0.04, 0.04, 0.04, 4.0])
    steps = np.array([0.1, 0.1, 0.1, 0.3, 1.0, 0.05, 0.05, 0.05, 0.004, 0.004, 0.004, 0.7])

    def loss(x: np.ndarray) -> float:
        return _c41_loss(bench_stats(bench, bench.develop(_c41_params(p0, x))), real)

    x, value = nelder_mead(loss, x0, lo, hi, steps, iterations, log)
    params = _c41_params(p0, x)
    return params, {"loss": value, "stats": bench_stats(bench, bench.develop(params))}


def _picture_counts(bench: Bench, images: list[np.ndarray]) -> np.ndarray:
    rng = np.random.default_rng(0)
    pics = [stats.measure(im, compose.truth(r), rng).picture for im, r in zip(images, bench.recipes)]
    return np.concatenate([p for p in pics if p is not None])


def _bw_params(base: film.FilmParams, x: np.ndarray) -> film.FilmParams:
    return replace(base, bw=replace(base.bw, gamma=float(x[0]), toe=float(10 ** x[1]),
                                    grain=float(x[2]), stops_mean=float(x[3])))


def fit_bw(bench: Bench, iterations: int, log=print) -> tuple[film.FilmParams, dict]:
    p0 = film.FilmParams()
    b = p0.bw
    x0 = np.array([b.gamma, np.log10(b.toe), b.grain, b.stops_mean])
    lo, hi = np.array([0.15, -3.0, 0.005, -4.0]), np.array([1.5, -0.2, 0.08, 4.0])
    steps = np.array([0.1, 0.3, 0.008, 0.7])

    def measure(params: film.FilmParams) -> dict:
        images = bench.develop(params)
        pic = _picture_counts(bench, images).mean(axis=1)
        s = bench_stats(bench, images)
        return {"p1": float(np.percentile(pic, 1)), "p50": float(np.percentile(pic, 50)),
                "p99": float(np.percentile(pic, 99)),
                "spread": float(np.mean(s["base_spread"]))}

    def loss(x: np.ndarray) -> float:
        m = measure(_bw_params(p0, x))
        return (sum(abs(np.log2((m[k] + 0.5) / (BW_TARGET[k] + 0.5))) for k in ("p1", "p50", "p99"))
                + 10.0 * abs(m["spread"] - BW_TARGET["spread"]))

    x, value = nelder_mead(loss, x0, lo, hi, steps, iterations, log)
    params = _bw_params(p0, x)
    return params, {"loss": value, "measured": measure(params), "target": BW_TARGET}


def _slide_params(base: film.FilmParams, x: np.ndarray) -> film.FilmParams:
    return replace(base, slide=replace(base.slide, tmax=(float(x[0]), float(x[1]), float(x[2])),
                                       kappa=float(x[3]), stops_mean=float(x[4])))


def fit_slide(bench: Bench, iterations: int, log=print) -> tuple[film.FilmParams, dict]:
    p0 = film.FilmParams()
    sl = p0.slide
    x0 = np.array([*sl.tmax, sl.kappa, sl.stops_mean])
    lo, hi = np.array([0.02, 0.02, 0.02, 0.3, -4.0]), np.array([1.0, 1.0, 1.0, 2.5, 4.0])
    steps = np.array([0.1, 0.1, 0.1, 0.2, 0.7])

    def measure(params: film.FilmParams) -> dict:
        pic = _picture_counts(bench, bench.develop(params))
        return {"p50": np.percentile(pic, 50, axis=0).tolist(),
                "p99.5": np.percentile(pic, 99.5, axis=0).tolist()}

    def loss(x: np.ndarray) -> float:
        m = measure(_slide_params(p0, x))
        return float(sum(np.abs(np.log2((np.array(m[k]) + 0.5) / (np.array(SLIDE_TARGET[k]) + 0.5))).sum()
                         for k in ("p50", "p99.5")))

    x, value = nelder_mead(loss, x0, lo, hi, steps, iterations, log)
    params = _slide_params(p0, x)
    return params, {"loss": value, "measured": measure(params), "target": SLIDE_TARGET}

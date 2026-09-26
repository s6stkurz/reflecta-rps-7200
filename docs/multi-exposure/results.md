# Results, as the scripts print them

Run on 2026-09-26 against the stored 3600 dpi slide passes, corrected with
that day's code:
- **9-pass bracket:** tag `bracket-3600x9`.
- **3-pass bracket:** tag `bracket-3600x3`.
- **Repeat pairs:** one pass from each run at the same exposure.

Every figure re-runs offline with

    uv run python docs/multi-exposure/analysis/merge_library.py --tag bracket-3600x9
    uv run python docs/multi-exposure/analysis/merge_library.py ENTRY_A ENTRY_B

**How to read the columns:**
- **dy, dx:** where a pass sat relative to the first, in lines and columns.
  The sign follows `rps7200.uniformity.register`.
- **peak z:** the registration's confidence. Unrelated frames score 4-6.
- **|z| before / after:** median disagreement with the first pass, in sigma,
  before and after registering. A repeat pair scores about 1.03.
- **noise:** relative shadow noise of each pass on the reference's scale, over
  the darkest tenth, inside a 40 px border.

```
bracket of 9 passes
  entry                                    exposure      dy      dx  peak z  |z| before  after  noise
  20260904T104325Z_slide_3600dpi              1.706   +0.00   +0.00     inf   reference         6.07%
  20260904T104330Z_slide_3600dpi              2.029   +0.01   -0.17    95.5        0.95   0.92  5.97%
  20260904T104334Z_slide_3600dpi              2.413   +0.01   -0.27    92.1        1.02   0.95  5.87%
  20260904T104339Z_slide_3600dpi              2.869   -0.09   -0.30    91.2        1.07   0.99  5.76%
  20260904T104343Z_slide_3600dpi              3.412   -0.21   -0.35    85.1        1.18   1.02  5.67%
  20260904T104347Z_slide_3600dpi              4.057   -0.41   -0.40    73.8        1.45   1.07  5.60%
  20260904T104351Z_slide_3600dpi              4.825   -0.76   -0.48    72.6        1.96   1.13  5.52%
  20260904T104355Z_slide_3600dpi              5.738   -1.15   -0.52    74.4        2.59   1.18  5.46%
  20260904T104358Z_slide_3600dpi              6.824   -2.45   -0.60    68.4        5.65   1.29  5.44%
  left after registering, last pass, row   286: dy -0.25  dx -0.08
  left after registering, last pass, row   860: dy +0.08  dx -0.07
  left after registering, last pass, row  1434: dy -0.02  dx +0.02
  left after registering, last pass, row  2008: dy +0.04  dx -0.01
  left after registering, last pass, row  2582: dy +0.04  dx -0.01
  left after registering, last pass, row  3156: dy +0.17  dx +0.05
  9 passes spanning x3.84 (1.94 stops); mean confidence 0.877; 0.000% of pixels had no usable pass; 6.073% took a single pass (the passes disagreed there)
  shadow noise, on the reference's scale: merged 5.27%, -7.0% against the middle pass, -3.1% against the best single pass
== 3-pass
bracket of 3 passes
  entry                                    exposure      dy      dx  peak z  |z| before  after  noise
  20260904T101819Z_slide_3600dpi              1.706   +0.00   +0.00     inf   reference         6.32%
  20260904T101825Z_slide_3600dpi              3.412   +0.03   -0.19    97.6        1.00   0.97  5.96%
  20260904T101830Z_slide_3600dpi              6.824   -0.12   -0.34    84.7        1.40   1.17  5.75%
  left after registering, last pass, row   286: dy -0.18  dx +0.00
  left after registering, last pass, row   860: dy +0.00  dx -0.04
  left after registering, last pass, row  1434: dy +0.01  dx +0.02
  left after registering, last pass, row  2008: dy +0.00  dx -0.02
  left after registering, last pass, row  2582: dy -0.09  dx -0.01
  left after registering, last pass, row  3156: dy -0.09  dx +0.05
  3 passes spanning x3.84 (1.94 stops); mean confidence 0.897; 0.000% of pixels had no usable pass; 6.072% took a single pass (the passes disagreed there)
  shadow noise, on the reference's scale: merged 5.67%, -4.8% against the middle pass, -1.5% against the best single pass
== 9-pass unregistered
  20260904T104358Z_slide_3600dpi              6.824                                5.65         6.88%
  9 passes spanning x3.66 (1.87 stops); mean confidence 0.598; 0.000% of pixels had no usable pass; 18.760% took a single pass (the passes disagreed there)
  shadow noise, on the reference's scale: merged 5.90%, +2.5% against the middle pass, +2.5% against the best single pass
== repeat 101819 + 104325
  left after registering, last pass, row  3156: dy -0.05  dx -0.03
  2 passes spanning x1.00 (-0.00 stops); mean confidence 0.999; 0.000% of pixels had no usable pass; 0.001% took a single pass (the passes disagreed there)
  shadow noise, on the reference's scale: merged 6.04%, -7.4% against the middle pass, -5.6% against the best single pass
== repeat 101825 + 104343
  left after registering, last pass, row  3156: dy -0.22  dx -0.03
  2 passes spanning x1.00 (-0.00 stops); mean confidence 0.994; 0.000% of pixels had no usable pass; 0.020% took a single pass (the passes disagreed there)
  shadow noise, on the reference's scale: merged 5.56%, -4.7% against the middle pass, -2.8% against the best single pass
== repeat 101830 + 104358
  left after registering, last pass, row  3156: dy -0.31  dx -0.01
  2 passes spanning x1.00 (-0.00 stops); mean confidence 0.966; 0.000% of pixels had no usable pass; 0.298% took a single pass (the passes disagreed there)
  shadow noise, on the reference's scale: merged 5.00%, -2.9% against the middle pass, -1.6% against the best single pass
```

Comparison figures from `analysis/resampling_control.py` on entry
`20260904T104343Z_slide_3600dpi`. Shifting the pass there and back changes its
shadow noise by +0.00% (0.25 px), +0.00% (0.5 px) and +0.01% (2.45 px). The
round-trip error has a median of 4 DN, and its 99.9th percentile falls from
161 DN at 16 px from the edge to 111 at 32 and 71 at 64.

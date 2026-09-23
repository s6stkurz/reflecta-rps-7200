# Labelling a frame's edges by eye

You are building the **ground truth** a frame-edge detector will be judged
against. Nothing you write may come from an algorithm: look, read the numbers,
decide. Do not run or read any detector code, and do not look at other
labellers' files.

## What is being labelled

A 300 dpi prescan of a 35 mm film strip, 428 columns wide (the transport runs
left-right). Between frames on the strip is **unexposed film base**. On the
negative that base is the *brightest* thing -- in view **A** (negative) it is a
near-white plateau; in view **B** (positive, inverted) it is solid black. For
each frame, on each side, find where the **picture ends and base begins**.

Real base, in Stefan's words: *"a sharp line, completely black from top to
bottom"* (in the positive). So base is:

* uniform, featureless, running the **full height** of the frame,
* the same colour as base anywhere else on the strip,
* bounded by a **straight, sharp, near-vertical line** -- possibly slightly tilted.

A dark area in the scene (a silhouette at dusk, a black wall, deep shadow) can
be nearly as clear as base on the negative. It is **not** base unless it runs
top to bottom with a straight sharp boundary. When in doubt, look at the
four-band table: real base is the same in all four bands.

**Addendum, measured after the first pass: base is not always the top of the
table.** On C-41 the orange mask is part of the base; exposed areas lose it,
so a few percent of picture pixels can be *brighter* than base in a channel.
On `strip6_01` the left base strip reads ~80 on the table while the brightest
picture reads 100. What identifies base is its **colour ratio** -- R/G 2.1-2.3,
B/G ~0.53, the same on every roll measured -- and its geometry: full height,
uniform, sharp straight boundary. A strip like that at 76-86 is base, even
though view B then shows it dark slate rather than pure black (the stretch
puts the brighter picture pixels at black). When in doubt, print the strip's
median raw RGB (`np.median` over its columns of `common.load(id).image`) and
check the ratio: a straight full-height strip whose ratio is not base's is
picture.

## Files

For frame `<id>`, in `research/frame-edge/crops/label/`:

* `<id>_A.png` -- view A, the negative (base white). `<id>_B.png` -- view B, the
  positive (base black). The top row is the whole frame at 1:1 (both views);
  below it the outer 64 columns of each side, magnified 10x across.
* `<id>.txt` -- per column, a base-likeness 0..100 (100 = brightest in the frame)
  in four horizontal bands (top..bottom) and over all rows.

**Coordinates are column boundaries.** The ruler's number *n* sits on the line
between column *n-1* and column *n*. Report positions in that coordinate, as a
float. Column *c* of the table spans boundary *c* to *c+1*.

* **Left side:** `x` = the boundary where base stops and picture starts. So `x`
  is also *how many columns of base* show at the left. Example: columns 0-11
  are base, 12 onwards picture -> `x = 12.0`.
* **Right side:** `x` = the boundary where picture stops and base starts.
  Example: columns 412-427 base -> `x = 412.0`.

**Refine with the table.** The edge is the 50% crossing between the base
plateau and the picture level beside it. If column 411 reads 25 between picture
at 14 and base at 97, it is ~13% base: right edge `x ~ 411.9`. Give one
decimal; the interval `lo`/`hi` says how sure you are (typically +-0.5 on a
clean edge, wider on a soft or ambiguous one).

## States, per side

| state | when |
|---|---|
| `edge` | base shows on this side; give `x` (and `lo`, `hi`) |
| `picture_to_border` | no base at all: picture runs to column 0 / 428 |
| `all_base` | the whole frame is base (blank frame) |
| `no_film` | the empty gate: no film, brighter than base and neutral grey (frames `r0909_13` and `r0914_21` show it) |
| `unreadable` | you cannot tell; say why in `note` |

A thin sliver still counts: one or two columns of base at the edge is `edge`
with `x` about 1-2 (left) or 426-427 (right). If a column at the very border
looks odd but no plateau exists, that is `picture_to_border`.

If a dark scene region sits right next to the gap so the boundary is hard to
place, it is still `edge` -- give your best `x` and a wide `lo`/`hi`, and note it.

**Neighbour sliver:** if beyond the base gap the *next* frame's picture shows
again, record `outer` = the boundary between that neighbour picture and the
base (left side: `outer < x`; right side: `outer > x`).

**Tilt:** if the edge is visibly not vertical, give `x_top` (at the top used
row) and `x_bottom` (bottom used row); `x` is then the value at mid-height.

## Output

One JSON file per frame at `research/frame-edge/labels/<you>/<id>.json`:

```json
{
  "id": "r0914_03", "labeller": "L1", "view": "A",
  "left":  {"state": "picture_to_border", "x": null, "lo": null, "hi": null,
            "outer": null, "x_top": null, "x_bottom": null, "note": ""},
  "right": {"state": "edge", "x": 411.9, "lo": 411.5, "hi": 412.2,
            "outer": null, "x_top": null, "x_bottom": null, "note": "clean"},
  "frame_note": ""
}
```

Look at **your assigned view first** and decide from it; use the other view
and the table to refine and check. Label every frame you are given. Honest
`unreadable` beats a guess.

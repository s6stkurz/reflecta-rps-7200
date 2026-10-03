"""research/frame-position-net: the network, its inputs and its weights files.

Needs torch (the `nn` dependency group) and skips without it. Trained weights
are local and never in a checkout, so these use seeded untrained ones: what is
tested is the plumbing a trained network relies on, not how well it reads.
"""

import re
import sys
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

ROOT = Path(__file__).resolve().parent.parent
FPN = ROOT / "research" / "frame-position-net"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(FPN))

from fpn import weights  # noqa: E402
from fpn.inputs import prepare  # noqa: E402
from fpn.loss import column_loss, column_weights  # noqa: E402
from fpn.model import CLASSES, build_model, parameter_count  # noqa: E402
from fpn.net import Net  # noqa: E402


def _prescan(rows=287, seed=0, dtype=np.uint8):
    rng = np.random.default_rng(seed)
    top = 255 if dtype == np.uint8 else 65535
    return rng.integers(0, top // 4, size=(rows, 428, 3)).astype(dtype)


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    torch.manual_seed(0)
    path = tmp_path_factory.mktemp("w") / "untrained.npz"
    weights.save(path, build_model().eval(), {"note": "seeded, untrained"})
    return path


def test_the_model_is_the_one_written_out_layer_by_layer():
    model = build_model()
    assert parameter_count(model) == 153_356
    out = model.eval()(torch.zeros(2, 6, 287, 428))
    assert tuple(out.shape) == (2, len(CLASSES), 1, 428)


def test_inputs_take_both_row_counts_and_both_depths():
    a = prepare(_prescan(287), dtype="uint8", film="negative")
    b = prepare(_prescan(286), dtype="uint8", film="c41")
    c = prepare(_prescan(287, dtype=np.uint16), dtype="uint16", film="positive")
    assert a.shape == b.shape == c.shape == (6, 287, 428)
    assert a[3:].reshape(3, -1)[:, 0].tolist() == [1.0, 0.0, 0.0]
    assert c[3:].reshape(3, -1)[:, 0].tolist() == [0.0, 0.0, 1.0]
    with pytest.raises(ValueError):
        prepare(_prescan(287)[:, :400], dtype="uint8", film="negative")
    with pytest.raises(ValueError):
        prepare(_prescan(287), dtype="uint8", film="kodachrome")


def test_exposure_drops_out_of_the_input():
    """The median is removed per channel, so a frame and the same frame at a
    higher exposure read alike -- exactly so where the +0.5 no longer matters."""
    base = _prescan(287, dtype=np.uint16).astype(np.float64) + 4000
    a = prepare(base.astype(np.uint16), dtype="uint16", film="negative")
    b = prepare((base * 2).astype(np.uint16), dtype="uint16", film="negative")
    # counts of 15-80 here: the +0.5 leaves at most ~0.02 of a stop
    assert np.abs(a[:3] - b[:3]).max() < 0.03


def test_the_answer_for_a_mirrored_frame_is_the_mirrored_answer(saved):
    net = Net(saved)
    image = _prescan(287, seed=3)
    p = net.probabilities(image, film="negative", dtype="uint8")
    pm = net.probabilities(image[:, ::-1].copy(), film="negative", dtype="uint8")
    assert np.abs(p - pm[:, ::-1]).max() < 1e-6
    assert p.shape == (4, 428) and np.allclose(p.sum(axis=0), 1.0)


def test_weights_round_trip_exactly_and_hold_nothing_but_numbers(saved, tmp_path):
    a, b = build_model().eval(), build_model().eval()
    weights.load(saved, a)
    weights.load(saved, b)
    x = torch.randn(1, 6, 287, 428)
    assert torch.equal(a(x), b(x))
    with np.load(saved, allow_pickle=False) as z:
        assert all(z[k].dtype != object for k in z.files)
    bad = tmp_path / "pickled.npz"
    np.savez(bad, **{"__meta__": np.array([{"a": 1}], dtype=object)})
    with pytest.raises(ValueError):
        weights.load(bad, build_model())


def test_no_torch_save_torch_load_or_pickle_anywhere():
    for path in sorted(FPN.rglob("*.py")):
        text = path.read_text()
        assert not re.search(r"torch\.(save|load)\s*\(", text), path
        assert "import pickle" not in text, path


def test_the_loss_weights_the_columns_that_decide_the_move():
    target = np.zeros((1, 4, 428), dtype=np.float32)
    target[0, 1, :50] = 1.0                       # base up to column 50
    target[0, 0, 50:] = 1.0
    w = column_weights(target)
    # the two columns that meet at the boundary, and three either side of them
    assert w[0, 45] == 1.0 and w[0, 46] == 4.0 and w[0, 53] == 4.0 and w[0, 54] == 1.0
    assert w[0, 427] == 4.0                       # picture at the border
    logits = torch.zeros(1, 4, 1, 428, requires_grad=True)
    loss = column_loss(logits, torch.from_numpy(target), torch.from_numpy(w))
    loss.backward()
    assert float(loss) == pytest.approx(np.log(4.0), rel=1e-5)


def test_the_reader_offers_the_edge_readers_interface(saved, monkeypatch):
    from fpn import reader

    monkeypatch.setattr(reader, "_net", None)
    monkeypatch.setenv("FPN_WEIGHTS", str(saved))
    walk = reader.walk_reader("negative")
    assert isinstance(walk, reader.NetWalkReader)
    image = _prescan(287, seed=5)
    walk.observe(1, image)
    mm, note = walk.judge(1, image)
    assert mm is None or isinstance(mm, float)
    assert note["reader"] == "frame-position-net" and "source" in note and "edges" in note
    assert walk.reread(1, image) == mm
    assert reader.walk_reader("kodachrome") is None
    assert reader.detect(image[:, :400], film="negative").left.state == "refuse"
    offsets, notes = reader.propose_centred([(1, image), (2, _prescan(287, seed=6))],
                                            film="positive")
    assert set(notes) == {1, 2}


def test_without_weights_there_is_no_reader(monkeypatch, tmp_path):
    from fpn import reader

    monkeypatch.setenv("FPN_WEIGHTS", str(tmp_path / "absent.npz"))
    assert reader.walk_reader("negative") is None


def test_the_studys_own_scoring_rules_load():
    """train.py and realism.py score sides with the study's common.side_agrees,
    loaded by path; its dataclasses need the module registered first."""
    from fpn import data
    from tools.frame_edges.sides import EDGE, PICTURE_TO_BORDER, Side

    common = data.study_common()
    assert common.side_agrees(Side(EDGE, x=10.0), Side(EDGE, x=10.6), "left", 428) is True
    assert common.side_agrees(Side(PICTURE_TO_BORDER), Side(EDGE, x=0.4), "left", 428) is True


def test_the_small_model_is_written_out_too_and_files_say_which_they_hold(tmp_path):
    from fpn.model import MODELS, build_model_small

    model = build_model_small().eval()
    assert parameter_count(model) == 37_668
    assert tuple(model(torch.zeros(1, 6, 287, 428)).shape) == (1, len(CLASSES), 1, 428)
    assert set(MODELS) == {"full", "small"}
    torch.manual_seed(1)
    path = tmp_path / "small.npz"
    weights.save(path, model, {"model": "small"})
    net = Net(path)
    assert parameter_count(net.model) == 37_668
    image = _prescan(287, seed=4)
    p = net.probabilities(image, film="bw", dtype="uint8")
    pm = net.probabilities(image[:, ::-1].copy(), film="bw", dtype="uint8")
    assert np.abs(p - pm[:, ::-1]).max() < 1e-6


def test_a_file_from_before_models_were_named_is_the_full_one(saved):
    assert "model" not in weights.meta(saved)
    assert parameter_count(Net(saved).model) == 153_356


class _Columns:
    """A stand-in synthetic set: each input and each target says its column."""

    def __init__(self, n=24):
        cols = np.arange(428, dtype=np.float32)
        rows = np.arange(287, dtype=np.float32)[:, None]
        self.x = np.broadcast_to(cols + 1000 * rows, (6, 287, 428)).astype(np.float32)
        self.targets = np.zeros((n, 4, 428), np.float32)
        self.targets[:, 0, :100] = 1                    # picture at the left, base after
        self.targets[:, 1, 100:] = 1

    def __len__(self):
        return len(self.targets)

    def inputs(self, i):
        return self.x


def test_the_flips_are_seeded_by_sample_and_epoch_and_move_the_labels_too():
    from fpn.loader import AugmentedSet

    plain = AugmentedSet(_Columns(), seed=3, augment=False)
    x0, t0, w0 = plain[5]
    assert np.array_equal(x0, plain.data.x) and np.array_equal(t0, plain.data.targets[5])

    aug = AugmentedSet(_Columns(), seed=3)
    seen = set()
    for epoch in (0, 1, 2):
        aug.epoch = epoch
        for i in range(len(aug)):
            x, t, w = aug[i]
            again = aug[i]
            assert all(np.array_equal(a, b) for a, b in zip((x, t, w), again))
            corner = float(x[0, 0, 0])                  # column + 1000 * row of the source
            mirrored = corner % 1000 == 427
            upside_down = corner >= 1000
            # The labels follow the columns, never the rows.
            assert bool(t[0, 0] == 0) == bool(mirrored)
            assert np.array_equal(w, column_weights(t[None])[0])
            seen.add((epoch, i, bool(mirrored), bool(upside_down)))
    flips = {(m, u) for _, _, m, u in seen}
    assert flips == {(False, False), (False, True), (True, False), (True, True)}
    by_sample = {}
    for epoch, i, m, u in seen:
        by_sample.setdefault(i, set()).add((m, u))
    assert any(len(v) > 1 for v in by_sample.values())     # a new epoch, new flips

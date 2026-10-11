"""The GUI tour's judgement of a frame: what it calls a band, blank or shifted.

The tour itself opens a window and is run by hand, or by the demo-gui-tester
agent; these hold the measures its verdicts rest on. A measure that passes a
band is a tour that calls a broken prescan clean. And how it waits for the
window: a tour that looks too soon fails a window that works.
"""
import json
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import load_tool

tour = load_tool("gui_tour")


def grain(height=40, width=60, seed=0, dtype=np.uint16):
    """A picture no two of whose neighbouring columns are equal, as film is."""
    rng = np.random.default_rng(seed)
    base = np.linspace(4000, 30000, width)[None, :, None]
    return (base + rng.normal(0, 300, (height, width, 3))).clip(
        0, 65535).astype(dtype)


def test_a_picture_with_grain_is_clean():
    problems, facts = tour.frame_problems(grain())
    assert problems == [], problems
    assert facts["repeated_columns"]["longest"] == 0


def test_an_edge_column_repeated_is_a_band():
    """What the demo once drew where the film moved: the last column, again
    and again, down one side of every aimed frame."""
    picture = grain()
    picture[:, -16:] = picture[:, -17:-16]
    problems, facts = tour.frame_problems(picture)
    assert any("band of 17" in p for p in problems), problems
    assert facts["repeated_columns"]["right"] == 16


def test_a_mirrored_strip_has_no_run_and_is_left_to_the_bit_check():
    """A mirror repeats one column, at the fold. It is caught by comparing
    with the stored source, not by this measure -- which is why both run."""
    picture = grain()
    picture[:, -8:] = picture[:, -9:-17:-1]
    problems, _ = tour.frame_problems(picture)
    assert not any("band" in p for p in problems), problems


def test_a_flat_bar_at_an_edge_is_found():
    picture = grain()
    picture[:, :4] = 0
    problems, _ = tour.frame_problems(picture)
    assert any("flat bar" in p for p in problems), problems


def test_a_blank_picture_is_blank():
    problems, _ = tour.frame_problems(np.full((40, 60, 3), 900, np.uint16))
    assert any(p.startswith("blank") for p in problems), problems


def test_a_picture_drawn_twice_its_size_is_not_a_band_on_screen():
    """Nearest-neighbour doubles every column, which is not a band there."""
    shown = np.repeat(grain(dtype=np.uint8), 2, axis=1)
    problems, _ = tour.frame_problems(shown, rail=False,
                                      band=tour.SHOT_BAND_COLUMNS)
    assert problems == [], problems


def test_the_rail_is_counted_per_channel():
    picture = grain()
    picture[:10, :, 2] = 65535
    _, facts = tour.frame_problems(picture)
    assert facts["rail_top"][2] == 0.25 and facts["rail_top"][0] == 0.0


def test_a_known_shift_is_found_between_two_views():
    picture = grain(width=200)
    moved = np.roll(picture, 12, axis=1)
    assert tour._column_shift(picture, moved)[0] == 12
    assert tour._column_shift(picture, picture) == (0, 1.0)


# --- a band the stored source has -------------------------------------------


def test_a_flat_bar_the_source_has_at_those_columns_is_the_sources():
    """The 1800 dpi RGBI entry the demo shows first is clipped to 65535 in
    every row of its last eight columns, and stays flat through correction:
    the tour called its pass an invented band on every run. Flat, and not
    identical: each column is divided by its own gain."""
    picture = grain()
    picture[:, -4:] = np.array([63189, 63062, 63300, 63795])[None, :, None]
    inherited = np.zeros(picture.shape[1], bool)
    inherited[-8:] = True
    problems, facts = tour.frame_problems(picture, inherited=inherited)
    assert problems == [], problems
    assert facts["flat_from_source"] == {"left": 0, "right": 4}


def test_a_flat_bar_one_column_wider_than_the_sources_is_the_windows():
    picture = grain()
    picture[:, -9:] = np.arange(60000, 60900, 100)[None, :, None]
    inherited = np.zeros(picture.shape[1], bool)
    inherited[-8:] = True
    problems, facts = tour.frame_problems(picture, inherited=inherited)
    assert any("flat bar" in p for p in problems), problems
    assert "flat_from_source" not in facts


def test_a_flat_bar_where_the_source_is_flat_elsewhere_is_still_found():
    picture = grain()
    picture[:, :4] = 0
    inherited = np.zeros(picture.shape[1], bool)
    inherited[-8:] = True
    problems, _ = tour.frame_problems(picture, inherited=inherited)
    assert any("flat bar" in p for p in problems), problems


def _demo_pass(tmp_path, monkeypatch, stored, fit_shape):
    """A stored entry, and a demo pass's entry that names it and its fit."""
    import rps7200.library as library

    source_root = tmp_path / "stored"
    source = source_root / "20260924T100319Z_stored"
    source.mkdir(parents=True)
    (source / "scan.json").write_text("{}", encoding="utf-8")
    passed = tmp_path / "tour" / "20261009T000000Z_pass"
    passed.mkdir(parents=True)
    record = {"scan": {"demo_source": {"entry": source.name,
                                       "file": "scan.tif"},
                       "demo_fit": {"source_shape": list(stored.shape[:2]),
                                    "shape": list(fit_shape)}}}
    (passed / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(tour, "SOURCE", source_root)
    monkeypatch.setattr(tour, "_CORRECTED", {})
    read = []

    def corrected(entry):
        read.append(Path(entry).name)
        return stored, {}
    monkeypatch.setattr(library, "corrected", corrected)
    return passed, read


def test_the_source_is_read_where_the_pass_drew_each_column(tmp_path,
                                                            monkeypatch):
    """Through the fit and the window's stride: a pass of the stored shape,
    shown at every second column, has the source's last eight flat columns
    as its last four."""
    stored = grain(height=60, width=80)
    stored[:, -8:] = 63000
    passed, read = _demo_pass(tmp_path, monkeypatch, stored, (60, 80))
    flat = tour.source_flat(passed, (30, 40, 3))
    assert flat is not None and flat.shape == (40,)
    assert list(np.flatnonzero(flat)) == [36, 37, 38, 39]
    full = tour.source_flat(passed, (60, 80, 3))
    assert full is not None and list(np.flatnonzero(full)) == list(range(72, 80))
    assert read == ["20260924T100319Z_stored"]          # read once, then kept


def test_a_pass_fitted_to_another_size_is_followed_to_its_columns(
        tmp_path, monkeypatch):
    """A 900 dpi pass of an 1800 dpi entry shows every second stored column."""
    stored = grain(height=60, width=80)
    stored[:, -8:] = 63000
    passed, _ = _demo_pass(tmp_path, monkeypatch, stored, (30, 40))
    flat = tour.source_flat(passed, (30, 40, 3))
    assert flat is not None and list(np.flatnonzero(flat)) == [36, 37, 38, 39]


def test_a_picture_that_cannot_be_traced_to_its_columns_excuses_nothing(
        tmp_path, monkeypatch):
    """Turned a quarter, its columns are the pass's rows."""
    stored = grain(height=60, width=80)
    stored[:, -8:] = 63000
    passed, _ = _demo_pass(tmp_path, monkeypatch, stored, (60, 80))
    assert tour.source_flat(passed, (80, 60, 3)) is None


def test_on_screen_only_the_channel_shown_is_asked(tmp_path, monkeypatch):
    """The demo's first entry has green and blue clipped across far more of
    its right edge than all three together, and the G and B views drew that
    as a flat bar of 93 and 108 screen columns, called invented."""
    stored = grain(height=60, width=80)
    stored[:, -20:, 1] = np.arange(60000, 62000, 100)[None, :]
    passed, _ = _demo_pass(tmp_path, monkeypatch, stored, (60, 80))
    # At half size, from the origin: screen column i shows stored column 2i.
    view = dict(entry=passed, working=(60, 80, 3), view=(40, 30, 0.5, 0, 0),
                invert=False, cuts=None, depth=16)
    green = tour.screen_flat(channel="G", **view)
    assert green is not None and green.shape == (40,)
    # Stored columns 60-79 are screen columns 30-39, and one either side
    # is allowed for.
    assert list(np.flatnonzero(green)) == list(range(29, 40))
    red = tour.screen_flat(channel="R", **view)
    assert red is not None and not red.any()
    shot = np.repeat(stored[::2, ::2, 1:2] // 257, 3, axis=2).astype(np.uint8)
    problems, facts = tour.frame_problems(shot, rail=False,
                                          band=tour.SHOT_BAND_COLUMNS,
                                          inherited=green)
    assert problems == [] and facts["flat_from_source"]["right"] == 10
    assert tour._verdict(problems, facts)[0] == "warn"


def test_on_screen_a_bar_wider_than_the_sources_is_found(tmp_path,
                                                         monkeypatch):
    stored = grain(height=60, width=80)
    stored[:, -20:, 1] = np.arange(60000, 62000, 100)[None, :]
    passed, _ = _demo_pass(tmp_path, monkeypatch, stored, (60, 80))
    green = tour.screen_flat(entry=passed, working=(60, 80, 3),
                             view=(40, 30, 0.5, 0, 0), channel="G",
                             invert=False, cuts=None, depth=16)
    shot = np.repeat(stored[::2, ::2, 1:2] // 257, 3, axis=2).astype(np.uint8)
    shot[:, -13:] = np.arange(100, 230, 10)[None, :, None]
    problems, _ = tour.frame_problems(shot, rail=False,
                                      band=tour.SHOT_BAND_COLUMNS,
                                      inherited=green)
    assert any("flat bar" in p for p in problems), problems


def test_a_pass_from_no_stored_entry_excuses_nothing(tmp_path):
    passed = tmp_path / "card"
    passed.mkdir()
    (passed / "scan.json").write_text(json.dumps(
        {"scan": {"demo_source": {"entry": None, "file": None}}}),
        encoding="utf-8")
    assert tour.source_flat(passed, (30, 40, 3)) is None


# --- waiting for the window -------------------------------------------------


def test_nothing_a_tour_keeps_hides_one_of_its_steps(tmp_path):
    """An attribute named like a step replaces it on the instance: one did,
    and "views and zoom" failed as "'dict' object is not callable"."""
    t = tour.Tour(SimpleNamespace(), None, tmp_path, "main")
    hidden = [name for name, value in vars(tour.Tour).items()
              if callable(value) and name in vars(t)]
    assert hidden == []


class _Root:
    """A root whose event loop has nothing to do, for waits that need none."""

    def update(self):
        pass


def test_idle_waits_for_the_writer_and_the_windows_own_writing(tmp_path):
    """A job ends before its passes are filed: the session's writer files
    them after, and the window's Save all and Export write on threads of
    their own. The tour looked for entries and files as the job ended, and
    found none -- an Export of a stopped roll with nothing to export."""
    writer = SimpleNamespace(queue=queue.Queue())
    writer.queue.put("a pass")
    session = SimpleNamespace(_writer=writer, _events=queue.Queue(),
                              outstanding=lambda: ())
    release = threading.Event()
    exporting = threading.Thread(target=release.wait, name="export-rolls",
                                 daemon=True)
    exporting.start()
    app = SimpleNamespace(busy=False, session=session, _writing=[exporting])
    t = tour.Tour(app, _Root(), tmp_path, "main")
    assert t.still_going() == [
        "1 pass(es) the session's writer has not filed",
        "the window writing (export-rolls)"]

    def finish():
        time.sleep(0.3)
        writer.queue.get()
        writer.queue.task_done()
        session._events.put("filed")
        time.sleep(0.3)
        session._events.get()
        release.set()
    threading.Thread(target=finish, daemon=True).start()
    started = time.monotonic()
    assert t.idle(timeout=10)
    assert time.monotonic() - started >= 0.6
    assert t.checks == []


def test_idle_that_runs_out_says_what_it_waited_for(tmp_path):
    writer = SimpleNamespace(queue=queue.Queue())
    writer.queue.put("a pass")
    app = SimpleNamespace(busy=False, _writing=[],
                          session=SimpleNamespace(_writer=writer,
                                                  _events=queue.Queue(),
                                                  outstanding=lambda: ()))
    t = tour.Tour(app, _Root(), tmp_path, "main")
    t.current = "prescan and scan"
    assert not t.idle(timeout=0.2)
    [check] = t.checks
    assert check["step"] == "prescan and scan" and check["status"] == "fail"
    assert "the session's writer has not filed" in check["detail"]



def test_still_going_counts_what_the_session_holds(tmp_path):
    """A job handed over is the session's from that moment
    (`ScanSession.outstanding`); the window's own copy of the queue, a tick
    behind it, is gone. A stand-in session that has no count is nothing
    queued."""
    session = SimpleNamespace(_writer=None, _events=queue.Queue(),
                              outstanding=lambda: ("a scan", "a roll"))
    app = SimpleNamespace(busy=False, session=session, _writing=[])
    t = tour.Tour(app, _Root(), tmp_path, "main")
    assert t.still_going() == ["2 job(s) queued or running"]
    app.session = SimpleNamespace(_writer=None, _events=queue.Queue())
    assert t.still_going() == []

@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:                       # no display
        pytest.skip(f"no display: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


def test_a_window_is_handed_over_once_its_buttons_are_drawn(tmp_path,
                                                            tk_root):
    """The calibration prompt exists as soon as it is made and is drawn only
    once the event loop runs. Handed over at once, the press into it found
    "there are []", and the unattended tour stopped at its first prompt."""
    import tkinter as tk

    t = tour.Tour(SimpleNamespace(), tk_root, tmp_path, "main")
    prompt = tk.Toplevel(tk_root)
    prompt.title("Calibrate first")
    pressed = []
    tk.Button(prompt, text="Calibrate now",
              command=lambda: pressed.append(1)).pack()
    assert t.buttons(prompt) == []                   # made, not yet drawn
    found = t.top("Calibrate")
    assert found is prompt
    t.press("Calibrate now", found)
    assert pressed == [1]


def test_a_press_waits_for_its_button_to_be_drawn(tmp_path, tk_root):
    import tkinter as tk

    t = tour.Tour(SimpleNamespace(), tk_root, tmp_path, "main")
    window = tk.Toplevel(tk_root)
    window.title("Rolls")
    tk.Button(window, text="Open").pack()
    pressed = []
    tk_root.after(300, lambda: tk.Button(
        window, text="Delete", command=lambda: pressed.append(1)).pack())
    t.press("Delete", t.top("Rolls"))
    assert pressed == [1]


def test_a_window_that_never_draws_a_button_is_named(tmp_path, tk_root):
    import tkinter as tk

    t = tour.Tour(SimpleNamespace(), tk_root, tmp_path, "main")
    empty = tk.Toplevel(tk_root)
    empty.title("Calibrate first")
    tk.Label(empty, text="nothing to press").pack()
    with pytest.raises(LookupError, match="'Calibrate first' did not show "
                                          "its buttons within 0 s"):
        t.top("Calibrate", timeout=0.3)
    with pytest.raises(LookupError, match="no button 'Calibrate now' in "
                                          "'Calibrate first' after waiting"):
        t.press("Calibrate now", empty, timeout=0.3)

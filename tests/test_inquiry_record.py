"""The INQUIRY reply is kept whole, not only as the fields read from it.

The fields are read at the offsets `pieusb` assumes and most of the reply is
read by nothing. An entry that kept only those could never be re-read if an
offset were wrong, or a skipped byte -- a firmware sub-revision -- mattered.
"""
from __future__ import annotations

import json

import numpy as np

from conftest import FakeTransport
from rps7200 import library
from rps7200.direct import DirectScanner
from rps7200.protocol import SCSI_INQUIRY

#: A 128-byte reply: its length at byte 4, as the device gives it, and every
#: other byte distinct so that nothing can be kept by accident.
REPLY = bytes([0, 0, 0, 0, 124]) + bytes(range(5, 128))


def _answer(command):
    size = (command[3] << 8) | command[4]
    return REPLY[:size]


def test_the_inquiry_reply_is_kept_whole_in_every_entry(tmp_path):
    s = DirectScanner(transport=FakeTransport(replies={SCSI_INQUIRY: _answer}),
                      debug=False)
    s._own_transport = False
    inquiry = s.inquiry()
    assert bytes.fromhex(inquiry.raw_hex) == REPLY

    entry = library.save(np.zeros((4, 4, 3), np.uint16),
                         {"resolution_dpi": 300, "channels": 3, "width": 4,
                          "height": 4, "depth": 16},
                         root=tmp_path, inquiry=inquiry)
    record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
    assert bytes.fromhex(record["device"]["raw_hex"]) == REPLY

"""The network: plain PyTorch, one layer per line.

It labels each of the 428 columns of a 300 dpi prescan as one of four classes
(`CLASSES`). Where the frame sits, and how far to move it, is then read off
those labels by `fpn.read` -- plain numpy, nothing learned.

Shapes are (batch, channels, rows, columns). Every block keeps all 428
columns, because the answer is a column; only the rows are pooled away.

    input            (N,  6, 287, 428)   R, G, B + three film flags (fpn.inputs)
    five 3x3 blocks  (N, 64,   8, 428)   each halves the rows
    row average      (N, 64,   1, 428)   one 64-number description per column
    eight 1x3 blocks (N, 64,   1, 428)   dilation 1..128: each column sees ~260 either way
    1x1 conv         (N,  4,   1, 428)   four class scores per column

About 0.15 M parameters and 2 GFLOP per frame.
"""

from __future__ import annotations

from torch import nn

#: What each column is. The order is the class index the loss uses.
CLASSES = ("picture", "base", "neighbour", "no_film")

#: R, G, B and the film flags c41, bw, positive -- see fpn.inputs.
IN_CHANNELS = 6


def build_model() -> nn.Sequential:
    layers: list[nn.Module] = []

    # -- Five blocks over 3x3 neighbourhoods. Each halves the rows. -----------
    layers.append(nn.Conv2d(IN_CHANNELS, 16, kernel_size=3, padding=1))  # (N, 16, 287, 428)
    layers.append(nn.BatchNorm2d(16))
    layers.append(nn.ReLU())
    layers.append(nn.MaxPool2d(kernel_size=(2, 1)))                       # (N, 16, 143, 428)

    layers.append(nn.Conv2d(16, 24, kernel_size=3, padding=1))
    layers.append(nn.BatchNorm2d(24))
    layers.append(nn.ReLU())
    layers.append(nn.MaxPool2d(kernel_size=(2, 1)))                       # (N, 24, 71, 428)

    layers.append(nn.Conv2d(24, 32, kernel_size=3, padding=1))
    layers.append(nn.BatchNorm2d(32))
    layers.append(nn.ReLU())
    layers.append(nn.MaxPool2d(kernel_size=(2, 1)))                       # (N, 32, 35, 428)

    layers.append(nn.Conv2d(32, 48, kernel_size=3, padding=1))
    layers.append(nn.BatchNorm2d(48))
    layers.append(nn.ReLU())
    layers.append(nn.MaxPool2d(kernel_size=(2, 1)))                       # (N, 48, 17, 428)

    layers.append(nn.Conv2d(48, 64, kernel_size=3, padding=1))
    layers.append(nn.BatchNorm2d(64))
    layers.append(nn.ReLU())
    layers.append(nn.MaxPool2d(kernel_size=(2, 1)))                       # (N, 64, 8, 428)

    # -- Collapse the rows: each column keeps 64 numbers about itself. --------
    layers.append(nn.AdaptiveAvgPool2d((1, None)))                         # (N, 64, 1, 428)

    # -- Eight blocks along the strip, ever wider. ---------------------------
    # A 1x3 kernel with dilation d reads the columns d either side; padding d
    # keeps the width at 428. Together they reach about 260 columns each way,
    # enough to tell a 17-column gap from a dark wall.
    layers.append(nn.Conv2d(64, 64, kernel_size=(1, 3), padding=(0, 1), dilation=(1, 1)))
    layers.append(nn.BatchNorm2d(64))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(64, 64, kernel_size=(1, 3), padding=(0, 2), dilation=(1, 2)))
    layers.append(nn.BatchNorm2d(64))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(64, 64, kernel_size=(1, 3), padding=(0, 4), dilation=(1, 4)))
    layers.append(nn.BatchNorm2d(64))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(64, 64, kernel_size=(1, 3), padding=(0, 8), dilation=(1, 8)))
    layers.append(nn.BatchNorm2d(64))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(64, 64, kernel_size=(1, 3), padding=(0, 16), dilation=(1, 16)))
    layers.append(nn.BatchNorm2d(64))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(64, 64, kernel_size=(1, 3), padding=(0, 32), dilation=(1, 32)))
    layers.append(nn.BatchNorm2d(64))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(64, 64, kernel_size=(1, 3), padding=(0, 64), dilation=(1, 64)))
    layers.append(nn.BatchNorm2d(64))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(64, 64, kernel_size=(1, 3), padding=(0, 128), dilation=(1, 128)))
    layers.append(nn.BatchNorm2d(64))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    # -- One answer per column: four class scores. ---------------------------
    layers.append(nn.Conv2d(64, len(CLASSES), kernel_size=1))             # (N, 4, 1, 428)

    return nn.Sequential(*layers)


def build_model_small() -> nn.Sequential:
    """The same idea, slimmer: the rows are averaged four at a time first, and
    the 3x3 blocks use half the channels. The answer is a column and edges run
    straight down the frame (1-2 columns of tilt over its height), so the rows
    carry little the columns need. The dilations still reach the whole
    aperture.

        input            (N,  6, 287, 428)
        row average x4   (N,  6,  71, 428)
        four 3x3 blocks  (N, 32,   4, 428)   each halves the rows
        row average      (N, 32,   1, 428)
        eight 1x3 blocks (N, 32,   1, 428)   dilation 1..128
        1x1 conv         (N,  4,   1, 428)

    About 38 k parameters and 0.2 GFLOP per frame, a tenth of the full one.
    """
    layers: list[nn.Module] = []

    # -- Average every four rows: the edges run straight down. ---------------
    layers.append(nn.AvgPool2d(kernel_size=(4, 1)))                        # (N, 6, 71, 428)

    # -- Four blocks over 3x3 neighbourhoods. Each halves the rows. -----------
    layers.append(nn.Conv2d(IN_CHANNELS, 8, kernel_size=3, padding=1))    # (N, 8, 71, 428)
    layers.append(nn.BatchNorm2d(8))
    layers.append(nn.ReLU())
    layers.append(nn.MaxPool2d(kernel_size=(2, 1)))                       # (N, 8, 35, 428)

    layers.append(nn.Conv2d(8, 16, kernel_size=3, padding=1))
    layers.append(nn.BatchNorm2d(16))
    layers.append(nn.ReLU())
    layers.append(nn.MaxPool2d(kernel_size=(2, 1)))                       # (N, 16, 17, 428)

    layers.append(nn.Conv2d(16, 24, kernel_size=3, padding=1))
    layers.append(nn.BatchNorm2d(24))
    layers.append(nn.ReLU())
    layers.append(nn.MaxPool2d(kernel_size=(2, 1)))                       # (N, 24, 8, 428)

    layers.append(nn.Conv2d(24, 32, kernel_size=3, padding=1))
    layers.append(nn.BatchNorm2d(32))
    layers.append(nn.ReLU())
    layers.append(nn.MaxPool2d(kernel_size=(2, 1)))                       # (N, 32, 4, 428)

    # -- Collapse the rows: each column keeps 32 numbers about itself. --------
    layers.append(nn.AdaptiveAvgPool2d((1, None)))                         # (N, 32, 1, 428)

    # -- Eight blocks along the strip, ever wider: ~260 columns each way. -----
    layers.append(nn.Conv2d(32, 32, kernel_size=(1, 3), padding=(0, 1), dilation=(1, 1)))
    layers.append(nn.BatchNorm2d(32))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(32, 32, kernel_size=(1, 3), padding=(0, 2), dilation=(1, 2)))
    layers.append(nn.BatchNorm2d(32))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(32, 32, kernel_size=(1, 3), padding=(0, 4), dilation=(1, 4)))
    layers.append(nn.BatchNorm2d(32))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(32, 32, kernel_size=(1, 3), padding=(0, 8), dilation=(1, 8)))
    layers.append(nn.BatchNorm2d(32))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(32, 32, kernel_size=(1, 3), padding=(0, 16), dilation=(1, 16)))
    layers.append(nn.BatchNorm2d(32))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(32, 32, kernel_size=(1, 3), padding=(0, 32), dilation=(1, 32)))
    layers.append(nn.BatchNorm2d(32))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(32, 32, kernel_size=(1, 3), padding=(0, 64), dilation=(1, 64)))
    layers.append(nn.BatchNorm2d(32))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    layers.append(nn.Conv2d(32, 32, kernel_size=(1, 3), padding=(0, 128), dilation=(1, 128)))
    layers.append(nn.BatchNorm2d(32))
    layers.append(nn.ReLU())
    layers.append(nn.Dropout(0.1))

    # -- One answer per column: four class scores. ---------------------------
    layers.append(nn.Conv2d(32, len(CLASSES), kernel_size=1))             # (N, 4, 1, 428)

    return nn.Sequential(*layers)


#: The models by the name a weights file records (`__meta__["model"]`).
MODELS = {"full": build_model, "small": build_model_small}


def parameter_count(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())

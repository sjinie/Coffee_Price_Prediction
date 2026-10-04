"""Research networks for standardized h-day cumulative log-return regression.

Input is (batch, lookback, features); output is (batch,) in target-scaler units.
The notebook owns feature/target scaling, horizon, dates, and training. These are
scalar-regression adaptations, not official forecasting reproductions.

NLinear: channel-wise last-value normalization/restoration, then a learned
cross-channel scalar head; https://github.com/cure-lab/LTSF-Linear
PatchTST: channel-independent shared patch encoder and flattened patch head,
then a scalar cross-channel head. d_model=16, heads=4, layers=1, patch=12,
stride=6, dropout=0.1; https://github.com/yuqinie98/PatchTST/blob/main/PatchTST_supervised/layers/PatchTST_backbone.py
TimesNet: per-sample non-DC FFT top-2 periods, 2D inception convolutions,
amplitude-weighted aggregation, residual and LayerNorm. d_model=8, layers=1,
kernels=(1,3,5); https://github.com/thuml/Time-Series-Library/blob/main/models/TimesNet.py

PatchTST/TimesNet omit input-window denormalization: input features and the
standardized scalar target do not share units, so restoring an input channel
onto the output would be invalid. TimesNet also omits multi-step forecasting
projection; its final head directly regresses the scalar target.
"""

from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F

from coffee_service.features import LOOKBACK
from coffee_service.modeling import DLinear


class NLinear(nn.Module):
    def __init__(self, n_features: int, lookback: int):
        super().__init__()
        self.linear = nn.Linear(lookback, 1)
        self.head = nn.Linear(n_features, 1)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        last = values[:, -1:, :].detach()
        channels = (values - last).transpose(1, 2)
        restored = self.linear(channels).squeeze(-1) + last.squeeze(1)
        return self.head(restored).squeeze(-1)


class PatchTST(nn.Module):
    def __init__(self, n_features: int, lookback: int):
        super().__init__()
        self.patch_len = 12
        self.stride = 6
        patch_count = (lookback - self.patch_len) // self.stride + 1
        self.patch = nn.Linear(self.patch_len, 16)
        self.position = nn.Parameter(torch.zeros(1, patch_count, 16))
        self.dropout = nn.Dropout(0.1)
        self.encoder = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(16, 4, dim_feedforward=64,
                                       dropout=0.1, batch_first=True),
            num_layers=1,
            enable_nested_tensor=False,
        )
        self.channel_head = nn.Linear(patch_count * 16, 1)
        self.head = nn.Linear(n_features, 1)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        batch, _, channels = values.shape
        patches = values.transpose(1, 2).unfold(-1, self.patch_len, self.stride)
        tokens = self.patch(patches).reshape(batch * channels, -1, 16)
        encoded = self.encoder(self.dropout(tokens + self.position))
        per_channel = self.channel_head(encoded.flatten(1)).reshape(batch, channels)
        return self.head(per_channel).squeeze(-1)


class _Inception2d(nn.Module):
    def __init__(self, in_channels: int, out_channels: int):
        super().__init__()
        self.convolutions = nn.ModuleList(
            nn.Conv2d(in_channels, out_channels, kernel_size=k, padding=k // 2)
            for k in (1, 3, 5)
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return sum(conv(values) for conv in self.convolutions) / len(self.convolutions)


class TimesNet(nn.Module):
    def __init__(self, n_features: int, lookback: int):
        super().__init__()
        self.lookback = lookback
        self.embedding = nn.Linear(n_features, 8)
        self.conv = nn.Sequential(_Inception2d(8, 16), nn.GELU(), _Inception2d(16, 8))
        self.norm = nn.LayerNorm(8)
        self.head = nn.Linear(lookback * 8, 1)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        embedded = self.embedding(values)
        amplitudes = torch.fft.rfft(embedded, dim=1).abs().mean(dim=-1)
        non_dc = amplitudes[:, 1:]
        indices = non_dc.topk(2, dim=1).indices + 1  # DC is never a candidate.
        constant = non_dc.amax(dim=1) <= 1e-6
        indices[constant] = torch.tensor([1, 2], device=values.device)
        weights = amplitudes.gather(1, indices).softmax(dim=1)
        periods = self.lookback // indices
        combined = torch.zeros_like(embedded)
        for rank in range(2):
            for period in periods[:, rank].unique().tolist():
                selected = (periods[:, rank] == period).nonzero(as_tuple=True)[0]
                samples = embedded.index_select(0, selected)
                padded_length = ((self.lookback + period - 1) // period) * period
                padded = F.pad(samples, (0, 0, 0, padded_length - self.lookback))
                grid = padded.reshape(-1, padded_length // period, period, 8)
                grid = grid.permute(0, 3, 1, 2)
                convolved = self.conv(grid).permute(0, 2, 3, 1)
                transformed = convolved.reshape(-1, padded_length, 8)[:, :self.lookback]
                weighted = transformed * weights.index_select(0, selected)[:, rank, None, None]
                combined = combined.index_add(0, selected, weighted)
        return self.head(self.norm(combined + embedded).flatten(1)).squeeze(-1)


def make_network(name: str, n_features: int, lookback: int = 60) -> nn.Module:
    """Construct one research model; DLinear retains its production 60-step shape."""
    if n_features < 1 or lookback < 1:
        raise ValueError("n_features and lookback must be positive")
    if name == "DLinear":
        if lookback != LOOKBACK:
            raise ValueError(f"DLinear requires lookback={LOOKBACK}")
        return DLinear(n_features)
    if name == "NLinear":
        return NLinear(n_features, lookback)
    if name == "PatchTST":
        if lookback < 12:
            raise ValueError("PatchTST requires lookback >= 12")
        return PatchTST(n_features, lookback)
    if name == "TimesNet":
        if lookback < 4:
            raise ValueError("TimesNet requires lookback >= 4")
        return TimesNet(n_features, lookback)
    raise ValueError(f"unknown network: {name}")

"""Shape, gradients, and sample independence for research model adapters."""

import pytest
import torch
from torch.nn import functional as F

from coffee_service.modeling import DLinear
from data_code.single_model_networks import NLinear, TimesNet, make_network


@pytest.mark.parametrize("name", ["DLinear", "NLinear", "PatchTST", "TimesNet"])
def test_finite_forward_backward_and_eval_batch_invariance(name):
    torch.manual_seed(7)
    model = make_network(name, n_features=3)
    values = torch.randn(2, 60, 3, requires_grad=True)
    prediction = model(values)
    assert prediction.shape == (2,)
    assert torch.isfinite(prediction).all()
    prediction.square().mean().backward()
    assert values.grad is not None and torch.isfinite(values.grad).all()
    assert all(parameter.grad is not None and torch.isfinite(parameter.grad).all()
               for parameter in model.parameters())

    model.eval()
    with torch.no_grad():
        together = model(values.detach())
        separate = torch.cat([model(values[i:i + 1].detach()) for i in range(2)])
    torch.testing.assert_close(together, separate, rtol=1e-5, atol=1e-6)


def test_nlinear_restores_each_channel_before_scalar_head():
    model = NLinear(n_features=2, lookback=60)
    with torch.no_grad():
        model.linear.weight.zero_()
        model.linear.bias.zero_()
        model.head.weight.copy_(torch.tensor([[2.0, -3.0]]))
        model.head.bias.zero_()
    values = torch.zeros(1, 60, 2)
    values[0, -1] = torch.tensor([4.0, 5.0])
    torch.testing.assert_close(model(values), torch.tensor([-7.0]))


def test_timesnet_constant_window_has_finite_periods_and_gradients():
    model = TimesNet(n_features=3, lookback=60)
    values = torch.ones(2, 60, 3, requires_grad=True)
    result = model(values)
    assert torch.isfinite(result).all()
    result.sum().backward()
    assert torch.isfinite(values.grad).all()


def test_timesnet_grouping_matches_samplewise_period_convolution():
    torch.manual_seed(7)
    model = TimesNet(n_features=3, lookback=60)
    time = torch.arange(60, dtype=torch.float32)
    periodic = torch.sin(2 * torch.pi * time / 60)[:, None].repeat(1, 3)
    values = torch.stack((torch.ones(60, 3), periodic, torch.randn(60, 3))).requires_grad_()

    embedded = model.embedding(values)
    expected = []
    for sample in embedded:
        amplitudes = torch.fft.rfft(sample, dim=0).abs().mean(-1)
        non_dc = amplitudes[1:]
        indices = non_dc.topk(2).indices + 1
        if non_dc.max() <= 1e-6:
            indices = torch.tensor([1, 2])
        weights = amplitudes[indices].softmax(0)
        transformed = []
        for index in indices:
            period = 60 // int(index)
            length = ((60 + period - 1) // period) * period
            padded = F.pad(sample, (0, 0, 0, length - 60))
            grid = padded.reshape(length // period, period, 8).permute(2, 0, 1)[None]
            output = model.conv(grid).squeeze(0).permute(1, 2, 0)
            transformed.append(output.reshape(-1, 8)[:60])
        combined = (torch.stack(transformed, -1) * weights).sum(-1) + sample
        expected.append(model.norm(combined).flatten())
    reference = model.head(torch.stack(expected)).squeeze(-1)
    grouped = model(values)
    torch.testing.assert_close(grouped, reference, rtol=1e-5, atol=1e-6)
    parameter = model.conv[0].convolutions[0].weight
    grouped_grad = torch.autograd.grad(grouped.sum(), (values, parameter), retain_graph=True)
    reference_grad = torch.autograd.grad(reference.sum(), (values, parameter))
    for actual, baseline in zip(grouped_grad, reference_grad):
        torch.testing.assert_close(actual, baseline, rtol=1e-5, atol=1e-6)


def test_factory_reuses_production_dlinear_and_rejects_bad_shapes():
    assert isinstance(make_network("DLinear", 3), DLinear)
    with pytest.raises(ValueError, match="lookback=60"):
        make_network("DLinear", 3, lookback=48)
    with pytest.raises(ValueError, match="unknown network"):
        make_network("Other", 3)

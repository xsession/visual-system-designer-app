import math
import pytest

from vsd.local.filtering import UnsafeFilterExpression, apply_filter, apply_pipeline, build_plot_data


def points():
    return [
        {"timestamp": float(i), "channel": "adc0", "value": float(i), "kind": "analog", "metadata": {}}
        for i in range(8)
    ]


def test_safe_expression_and_pipeline():
    assert len(apply_filter(points(), "value >= 3 and channel == 'adc0'")) == 5
    with pytest.raises(UnsafeFilterExpression):
        apply_filter(points(), "__import__('os').system('id')")
    filtered = apply_pipeline(points(), [{"op": "moving_average", "window": 3}])
    assert filtered[2]["value"] == pytest.approx(1.0)


@pytest.mark.parametrize("kind", ["time", "scope", "bar", "histogram", "spectrum", "logic"])
def test_plot_builders(kind):
    result = build_plot_data(points(), kind, bins=4)
    assert result["mode"] == kind
    assert result

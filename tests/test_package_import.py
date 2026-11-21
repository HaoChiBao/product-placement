"""Core package import must not require the optional suggest (OpenCV) extra."""

from __future__ import annotations

import sys


def test_import_product_placement_without_loading_suggest():
    # Drop cached modules so we can observe what import pulls in.
    for name in list(sys.modules):
        if name == "product_placement" or name.startswith("product_placement."):
            del sys.modules[name]

    import product_placement

    assert product_placement.__version__
    assert callable(product_placement.swap)
    assert "product_placement.suggest" not in sys.modules
    assert "product_placement.vision" not in sys.modules


def test_suggest_lazy_attr_still_resolves():
    import product_placement

    suggest = product_placement.suggest
    Moment = product_placement.Moment
    assert callable(suggest)
    assert Moment.__name__ == "Moment"

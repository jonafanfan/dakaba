"""Shared helpers for building synthetic scenes."""
import numpy as np
import pytest


@pytest.fixture
def scene_image(tmp_path):
    """A real on-disk image, for the paths that go through PIL and cv2 for real."""
    import cv2

    h, w, cell = 240, 320, 16
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            img[y : y + cell, x : x + cell] = 225 if ((y // cell) + (x // cell)) % 2 == 0 else 30
    path = tmp_path / "scene.png"
    assert cv2.imwrite(str(path), img)
    return str(path)

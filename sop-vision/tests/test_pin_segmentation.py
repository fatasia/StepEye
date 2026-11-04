"""固定切块协议检查；合成图不作为针脚模型准确率证据。"""
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from visual.pin_segmentation import MEAN, STD, segment, tiles


class Session:
    def get_inputs(self):
        return [SimpleNamespace(name='images', type='tensor(float)', shape=[1, 3, 512, 512])]

    def get_outputs(self):
        return [SimpleNamespace(type='tensor(float)', shape=[1, 2, 512, 512])]

    def run(self, _, feed):
        # Invert normalization to check that stitching preserves every input pixel.
        gray = feed['images'][:, :1] * STD[:, :1] + MEAN[:, :1]
        return [np.concatenate([.5 - gray, gray - .5], axis=1)]


def test_gray_normalization_and_reflection():
    rgb = Image.new('RGB', (3, 2), (100, 200, 40))
    y, x, tensor = next(tiles(rgb))
    expected = np.asarray(rgb.convert('L'), dtype=np.float32)[0, 0] / 255
    assert (y, x) == (0, 0) and tensor.shape == (1, 3, 512, 512)
    assert tensor.dtype == np.float32 and tensor.flags.c_contiguous
    np.testing.assert_allclose(tensor[0, :, 0, 0], (expected - MEAN[0, :, 0, 0]) / STD[0, :, 0, 0])
    image = Image.fromarray(np.array([[0, 50, 100], [150, 200, 250]], dtype=np.uint8))
    tensor = next(tiles(image))[2]
    restored = np.rint((tensor[0, 0] * STD[0, 0, 0, 0] + MEAN[0, 0, 0, 0]) * 255)
    np.testing.assert_array_equal(restored[:3, :5], [[0, 50, 100, 50, 0], [150, 200, 250, 200, 150], [0, 50, 100, 50, 0]])


@pytest.mark.parametrize('size', [(1, 1), (512, 512), (517, 1025)])
def test_stitching_preserves_pixels_and_original_size(size):
    width, height = size
    values = ((np.indices((height, width)).sum(axis=0) % 2) * 255).astype(np.uint8)
    mask, info = segment(Image.fromarray(values), Session())
    np.testing.assert_array_equal(mask, values // 255)
    assert info['patch_count'] == ((width + 511) // 512) * ((height + 511) // 512)
    assert info['foreground_pixels'] == int((values > 0).sum())
    assert 'defect' not in info and 'confidence' not in info


def test_rejects_wrong_graph_and_nonfinite_output():
    session = Session()
    session.get_outputs = lambda: [SimpleNamespace(type='tensor(float)', shape=[1, 1, 512, 512])]
    with pytest.raises(ValueError, match='输入输出'):
        segment(Image.new('L', (2, 2)), session)
    session = Session()
    session.run = lambda *_: [np.full((1, 2, 512, 512), np.nan, dtype=np.float32)]
    with pytest.raises(ValueError, match='输出无效'):
        segment(Image.new('L', (2, 2)), session)

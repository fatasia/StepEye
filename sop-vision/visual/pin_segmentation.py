"""PAINE PCB 插针分割预处理；保留原图尺度，不把掩膜当缺陷结论。"""
from __future__ import annotations

import time

import numpy as np
from PIL import Image

PATCH_SIZE = 512
MEAN = np.array([.485, .456, .406], dtype=np.float32)[None, :, None, None]
STD = np.array([.229, .224, .225], dtype=np.float32)[None, :, None, None]


def tiles(image: Image.Image):
    """按原始像素切块；仅右/下边缘反射补齐，每次只创建一个输入张量。"""
    gray = np.asarray(image.convert('L'))
    height, width = gray.shape
    if not height or not width:
        raise ValueError('针脚图片不能为空')
    size = PATCH_SIZE
    padded = np.pad(gray, ((0, (-height) % size), (0, (-width) % size)), mode='reflect')
    for y in range(0, height, size):
        for x in range(0, width, size):
            patch = padded[y:y + size, x:x + size]
            tensor = np.repeat(patch[None, None], 3, axis=1).astype(np.float32) / 255
            yield y, x, np.ascontiguousarray((tensor - MEAN) / STD)


def segment(image: Image.Image, session):
    """调用方持有单次 ORT session；逐块推理后裁回原尺寸，不创建后台任务。"""
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (len(inputs) != 1 or inputs[0].type != 'tensor(float)'
            or inputs[0].shape != [1, 3, PATCH_SIZE, PATCH_SIZE]
            or len(outputs) != 1 or outputs[0].type != 'tensor(float)'
            or outputs[0].shape != [1, 2, PATCH_SIZE, PATCH_SIZE]):
        raise ValueError('针脚分割模型输入输出不匹配')
    mask = np.empty((image.height, image.width), dtype=np.uint8)
    inference_s, count = 0., 0
    for y, x, tensor in tiles(image):
        started = time.perf_counter()
        logits = session.run(None, {inputs[0].name: tensor})[0]
        inference_s += time.perf_counter() - started
        if logits.shape != (1, 2, PATCH_SIZE, PATCH_SIZE) or not np.isfinite(logits).all():
            raise ValueError('针脚分割输出无效')
        patch = logits.argmax(axis=1)[0].astype(np.uint8)
        h, w = min(PATCH_SIZE, image.height - y), min(PATCH_SIZE, image.width - x)
        mask[y:y + h, x:x + w] = patch[:h, :w]
        count += 1
    return mask, {'patch_count': count, 'inference_ms': round(inference_s * 1000, 3),
                  'foreground_pixels': int(np.count_nonzero(mask)), 'task': 'pin_segmentation'}

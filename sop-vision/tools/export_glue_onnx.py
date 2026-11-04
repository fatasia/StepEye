"""将固定胶路 Darknet 数据权重转为标准 ONNX；不运行作者代码或训练器。"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import onnx
from onnx import helper as h, numpy_helper as nh

WEIGHTS_SHA = 'c51a715e886f585a1046a6b09f62270d212a8715a0e5428490c7897a88ced3a3'
CFG_SHA = '44dbebf92ad2dba2ebcc3da31d2058cd5a94b37ad9901c3e00973b1866cca3bb'


def blocks(text):
    result = []
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('['):
            result.append({'type': line.strip('[]')})
        else:
            key, value = line.split('=', 1)
            result[-1][key.strip()] = value.strip()
    return result


class Graph:
    def __init__(self):
        self.nodes, self.constants = [], []
        self.serial = 0

    def constant(self, value, dtype=np.float32):
        name = f'constant_{len(self.constants)}'
        self.constants.append(nh.from_array(np.asarray(value, dtype=dtype), name))
        return name

    def op(self, kind, *inputs, **attrs):
        name = f'{kind}_{self.serial}'
        self.serial += 1
        self.nodes.append(h.make_node(kind, inputs, [name], name=name, **attrs))
        return name

    def slice(self, source, start, end, axis):
        return self.op('Slice', source, self.constant([start], np.int64),
                       self.constant([end], np.int64), self.constant([axis], np.int64))

    def head(self, source, cfg, height, width):
        anchors = np.array([float(v) for v in cfg['anchors'].split(',')]).reshape(-1, 2)
        chosen = anchors[[int(v) for v in cfg['mask'].split(',')]]
        raw = self.op('Reshape', source, self.constant([1, 3, 7, height, width], np.int64))
        raw = self.op('Transpose', raw, perm=[0, 3, 4, 1, 2])
        yy, xx = np.mgrid[:height, :width]
        grid = np.stack([xx, yy], -1)[None, :, :, None]
        scale = float(cfg['scale_x_y'])
        xy = self.op('Sigmoid', self.slice(raw, 0, 2, 4))
        xy = self.op('Mul', xy, self.constant(scale))
        xy = self.op('Add', xy, self.constant(grid - (scale - 1) / 2))
        xy = self.op('Div', xy, self.constant([width, height]))
        wh = self.op('Exp', self.slice(raw, 2, 4, 4))
        wh = self.op('Mul', wh, self.constant(chosen[None, None, None] / 416))
        obj = self.op('Sigmoid', self.slice(raw, 4, 5, 4))
        cls = self.op('Sigmoid', self.slice(raw, 5, 7, 4))
        cls = self.op('Mul', cls, obj)
        joined = self.op('Concat', xy, wh, obj, cls, axis=4)
        return self.op('Reshape', joined, self.constant([1, -1, 7], np.int64))


def export(cfg_path, weights_path, output):
    cfg_path, weights_path, output = map(Path, (cfg_path, weights_path, output))
    if output.exists():
        raise ValueError('输出已存在，保留原有模型')
    for path, digest in [(cfg_path, CFG_SHA), (weights_path, WEIGHTS_SHA)]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('固定胶路源文件SHA不匹配')
    cfg = blocks(cfg_path.read_text())
    # Version 0.2 uses a 64-bit seen counter after the three int32 version fields.
    data = weights_path.read_bytes()
    version = np.frombuffer(data[:12], dtype='<i4')
    if tuple(version) != (0, 2, 5):
        raise ValueError(f'不支持的Darknet头: {version}')
    weights = np.frombuffer(data, dtype='<f4', offset=20)
    offset = 0

    def take(shape):
        nonlocal offset
        size = int(np.prod(shape))
        result = weights[offset:offset + size].reshape(shape).copy()
        offset += size
        return result

    graph = Graph()
    previous, channels, height, width = 'images', 3, 416, 416
    layers, heads = [], []
    for index, item in enumerate(cfg[1:]):
        kind = item['type']
        if kind == 'convolutional':
            count, kernel, stride = (int(item[k]) for k in ('filters', 'size', 'stride'))
            bias = take((count,))
            bn = item.get('batch_normalize') == '1'
            if bn:
                scale, mean, variance = (take((count,)) for _ in range(3))
            matrix = take((count, channels, kernel, kernel))
            padding = kernel // 2 if item.get('pad') == '1' else 0
            args = [previous, graph.constant(matrix)]
            if not bn:
                args.append(graph.constant(bias))
            previous = graph.op('Conv', *args, kernel_shape=[kernel, kernel],
                                strides=[stride, stride], pads=[padding] * 4)
            if bn:
                previous = graph.op('BatchNormalization', previous, graph.constant(scale),
                                    graph.constant(bias), graph.constant(mean), graph.constant(variance), epsilon=1e-5)
            if item['activation'] == 'leaky':
                previous = graph.op('LeakyRelu', previous, alpha=.1)
            elif item['activation'] != 'linear':
                raise ValueError('不支持的激活')
            height = (height + 2 * padding - kernel) // stride + 1
            width = (width + 2 * padding - kernel) // stride + 1
            channels = count
        elif kind == 'route':
            indices = [int(v) for v in item['layers'].split(',')]
            chosen = [layers[index + i if i < 0 else i] for i in indices]
            previous = chosen[0][0] if len(chosen) == 1 else graph.op('Concat', *(v[0] for v in chosen), axis=1)
            channels, height, width = sum(v[1] for v in chosen), chosen[0][2], chosen[0][3]
            if 'groups' in item:
                count = channels // int(item['groups'])
                start = count * int(item['group_id'])
                previous = graph.slice(previous, start, start + count, 1)
                channels = count
        elif kind == 'maxpool':
            size, stride = int(item['size']), int(item['stride'])
            previous = graph.op('MaxPool', previous, kernel_shape=[size, size], strides=[stride, stride])
            height, width = height // stride, width // stride
        elif kind == 'upsample':
            stride = int(item['stride'])
            previous = graph.op('Resize', previous, '', graph.constant([1, 1, stride, stride]),
                                mode='nearest', coordinate_transformation_mode='asymmetric', nearest_mode='floor')
            height, width = height * stride, width * stride
        elif kind == 'yolo':
            heads.append(graph.head(previous, item, height, width))
        else:
            raise ValueError(f'不支持的层: {kind}')
        layers.append((previous, channels, height, width))
    if offset != len(weights) or len(heads) != 2:
        raise ValueError('权重长度或输出头不匹配')
    predictions = graph.op('Concat', *heads, axis=1)
    graph.nodes.append(h.make_node('Identity', [predictions], ['predictions']))
    model = h.make_model(h.make_graph(graph.nodes, 'glue-yolov4-tiny-fixed',
        [h.make_tensor_value_info('images', onnx.TensorProto.FLOAT, [1, 3, 416, 416])],
        [h.make_tensor_value_info('predictions', onnx.TensorProto.FLOAT, [1, 2535, 7])], graph.constants),
        opset_imports=[h.make_opsetid('', 13)], producer_name='StepEye fixed Darknet exporter')
    model.ir_version = 8
    h.set_model_props(model, {'adapter': 'glue_yolov4', 'class_scores': 'include_objectness',
                             'preprocessing': 'direct_resize_rgb_416_div255', 'usage': 'evaluation_only'})
    onnx.checker.check_model(model)
    output.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, output)
    return {'bytes': output.stat().st_size, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cfg', required=True)
    parser.add_argument('--weights', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(export(args.cfg, args.weights, args.output))

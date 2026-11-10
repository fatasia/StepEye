"""Public model inspection contracts; fixtures are not recognition benchmarks."""
import importlib.util
from pathlib import Path
import pytest

path=Path(__file__).resolve().parents[1]/'tools/public_model_probe.py'
spec=importlib.util.spec_from_file_location('public_model_probe',path)
probe=importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)


def model(tmp_path,size=640,domain='',names="{0: 'part'}"):
    onnx=pytest.importorskip('onnx')
    import numpy as np
    arr=np.zeros((1,5,10),np.float32); arr[0,:,0]=[size/2,size/2,size/2,size/2,.9]
    node=onnx.helper.make_node('Constant',[],['output'],domain=domain,value=onnx.numpy_helper.from_array(arr))
    graph=onnx.helper.make_graph([node],'fixture',[onnx.helper.make_tensor_value_info('images',onnx.TensorProto.FLOAT,[1,3,size,size])],[onnx.helper.make_tensor_value_info('output',onnx.TensorProto.FLOAT,[1,5,10])])
    m=onnx.helper.make_model(graph,opset_imports=[onnx.helper.make_opsetid('',13)]); m.ir_version=8
    onnx.helper.set_model_props(m,{'names':names,'task':'detect'})
    p=tmp_path/'model.onnx'; onnx.save(m,p); return p


def test_inspect_640_and_320_contract(tmp_path):
    r=probe.inspect_onnx(model(tmp_path))
    assert r['adapter_compatible'] and r['classes']==['part'] and len(r['sha256'])==64
    assert probe.inspect_onnx(model(tmp_path,320))['adapter_compatible']


def test_unknown_ops_and_executable_class_metadata_rejected(tmp_path):
    with pytest.raises(ValueError): probe.inspect_onnx(model(tmp_path,domain='custom.op'))
    with pytest.raises((ValueError,SyntaxError)): probe.inspect_onnx(model(tmp_path,names="__import__('os').system('not executed')"))


def test_native_fixed_320_probe(tmp_path):
    from PIL import Image
    image=tmp_path/'sample.png'; Image.new('RGB',(80,80),'gray').save(image)
    r=probe.probe(model(tmp_path,320),image)
    assert r['provider']=='CPUExecutionProvider' and r['validation']=='single_image_smoke_only'
    assert r['adapter_compatible']
    assert r['targets'][0]['xyxy_norm']==pytest.approx([.25,.25,.75,.75])

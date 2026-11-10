import numpy as np
import pytest
from types import SimpleNamespace as NS
from visual.kairos_rtdetr import preprocess,decode,validate_native,KairosRTDetrBackend


def test_preprocess_stretch_rgb_no_padding():
    frame=np.zeros((20,40,3),np.uint8);frame[:]=[10,20,255]
    x=preprocess(frame)
    assert x.shape==(1,3,640,640) and x.dtype==np.float32
    np.testing.assert_allclose(x[0,:,0,0],[1,20/255,10/255])
    np.testing.assert_equal(x[0,:,0,0],x[0,:,-1,-1])


def test_decode_sigmoid_nms_original_geometry():
    hits=decode(np.array([[[4.],[3.],[-8.]]]),np.array([[[.5,.5,.5,.5],[.5,.5,.5,.5],[.1,.1,.1,.1]]]),200,100)
    assert len(hits)==1 and hits[0].xyxy==(50,25,150,75) and hits[0].label=='water'
    with pytest.raises(ValueError):decode(np.array([[[np.nan]]]),np.zeros((1,1,4)),100,100)


def test_native_dynamic_batch_and_query():
    inp=[NS(name='pixel_values',shape=['batch',3,640,640],type='tensor(float)')]
    outs=[NS(name='logits',shape=['batch','n',1],type='tensor(float)'),NS(name='pred_boxes',shape=['batch','n',4],type='tensor(float)')]
    validate_native(inp,outs)
    outs[0].shape[-1]=2
    with pytest.raises(ValueError):validate_native(inp,outs)


def test_real_onnx_protocol(tmp_path):
    o=pytest.importorskip('onnx');h=o.helper
    outputs={'logits':np.array([[[4.],[3.]]],np.float32),'pred_boxes':np.array([[[.5,.5,.5,.5],[.5,.5,.5,.5]]],np.float32)}
    nodes=[h.make_node('Constant',[],[k],value=o.numpy_helper.from_array(v)) for k,v in outputs.items()]
    g=h.make_graph(nodes,'kairos-test',[h.make_tensor_value_info('pixel_values',o.TensorProto.FLOAT,['batch',3,640,640])],
                   [h.make_tensor_value_info(k,o.TensorProto.FLOAT,list(v.shape)) for k,v in outputs.items()])
    m=h.make_model(g,opset_imports=[h.make_opsetid('',13)]);m.ir_version=8
    path=tmp_path/'model.onnx';o.save(m,path)
    detector=KairosRTDetrBackend(path,['water'])
    assert detector.detect(np.zeros((100,200,3),np.uint8))[0].xyxy==(50,25,150,75)
    detector.close()
    with pytest.raises(RuntimeError):detector.detect(np.zeros((10,10,3),np.uint8))
    with pytest.raises(ValueError):KairosRTDetrBackend(path,['person'])


def test_decode_limit_clip_and_empty():
    boxes=np.array([[[.1,.1,.4,.4],[.8,.8,.2,.2],[.5,.5,-1.,.2]]],np.float32)
    logits=np.array([[[5.],[4.],[3.]]],np.float32)
    hits=decode(logits,boxes,100,50,max_results=1)
    assert len(hits)==1 and hits[0].xyxy[:2]==(0.,0.)
    assert decode(logits,boxes,100,50,threshold=1)==[]
    with pytest.raises(ValueError):decode(logits,boxes[:,:2],100,50)


def test_invalid_images_and_native_layout():
    for image in [np.zeros((10,10),np.uint8),np.zeros((2,2,3),np.float32),np.zeros((0,2,3),np.uint8)]:
        with pytest.raises(ValueError):preprocess(image)
    inp=[NS(name='pixel_values',shape=[2,3,640,640],type='tensor(float)')]
    with pytest.raises(ValueError):validate_native(inp,[])

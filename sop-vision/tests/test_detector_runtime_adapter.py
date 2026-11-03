"""Runtime adapter wiring: Constant graphs test protocol, not defect accuracy."""
import numpy as np
import pytest
from visual.dsl import DetectorCfg,Zone
from visual.onnx_detector import create_detector
from visual.perception.base import Context,build_geoms


def graph(tmp_path,classification=False):
    onnx=pytest.importorskip('onnx')
    pred=np.zeros((1,300,6),np.float32)
    pred[0,0]=[160,160,240,240,.9,0]
    pred[0,1]=[300,300,400,400,.8,1]
    if classification:pred=np.array([[.3,.7]],np.float32)
    node=onnx.helper.make_node('Constant',[],['output'],value=onnx.numpy_helper.from_array(pred))
    g=onnx.helper.make_graph([node],'fixture',[onnx.helper.make_tensor_value_info('images',onnx.TensorProto.FLOAT,[1,3,640,640])],
                            [onnx.helper.make_tensor_value_info('output',onnx.TensorProto.FLOAT,list(pred.shape))])
    m=onnx.helper.make_model(g,opset_imports=[onnx.helper.make_opsetid('',13)]);m.ir_version=8
    onnx.helper.set_model_props(m,{'names':"{0: 'part', 1: 'other'}",'end2end':str(not classification),
                                  'task':'classify' if classification else 'detect'})
    p=tmp_path/'fixture.onnx';onnx.save(m,p);return p


def test_runtime_e2e_class_filter_tracks_events_and_reset(tmp_path):
    cfg=DetectorCfg(model=str(graph(tmp_path)),adapter='yolo_e2e',classes=['part'],class_names=['part','other'],enter_frames=2,exit_frames=2)
    geoms=build_geoms([Zone(id='z',polygon=[[0,0],[1,0],[1,1],[0,1]])],640,640)
    detector=create_detector(cfg,geoms);ctx=Context(geoms);frame=np.zeros((640,640,3),np.uint8)
    try:
        assert detector.process(frame,0,ctx)==[]
        assert len(ctx.object_tracks)==1 and ctx.object_tracks[0].label=='part'
        first=ctx.object_tracks[0].track_id;ctx.reset()
        events=detector.process(frame,.1,ctx)
        assert ctx.object_tracks[0].track_id==first
        assert any(e.type=='object_enter' and e.name=='part' for e in events)
        assert any(e.type=='object_count' and e.value==1 for e in events)
        ctx.reset();assert detector.process(frame,.1,ctx)==[] and not ctx.object_tracks
        detector.reset_observations();ctx.reset();assert detector.process(frame,.2,ctx)==[]
        assert ctx.object_tracks[0].track_id!=first
        ctx.reset();detector.process(frame,.3,ctx)
        detector.backend.detect=lambda frame:[]
        ctx.reset();detector.process(frame,.4,ctx);events=detector.process(frame,.5,ctx)
        assert any(e.type=='object_exit' for e in events)
    finally:detector.close()


def test_runtime_rejects_classifier_and_implicit_e2e(tmp_path):
    path=graph(tmp_path,classification=True)
    with pytest.raises(ValueError,match='分类器'):create_detector(DetectorCfg(model=str(path),class_names=['part','other']),{})
    path=graph(tmp_path)
    with pytest.raises(ValueError,match='端到端'):create_detector(DetectorCfg(model=str(path),class_names=['part','other']),{})
    with pytest.raises(ValueError):DetectorCfg(adapter='classifier')
    with pytest.raises(ValueError,match='仅用于ONNX'):create_detector(DetectorCfg(adapter='yolo_e2e',model='x.tflite'),{})
    assert DetectorCfg().adapter=='yolo_raw'

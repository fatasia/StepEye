"""端到端独立适配回归；Constant图仅验证契约，不代表训练效果。"""
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from visual.detector_registry import create_backend
from visual.label_jobs import LabelEngines, fingerprint
from visual.label_predict import predict
from visual.model_routes import ModelTrials
from visual.public_models import register_public_models


def model(tmp_path, *, row=None, names=None, end2end=True):
    onnx=pytest.importorskip("onnx")
    prediction=np.zeros((1,300,6),np.float32)
    prediction[0,0]=row or [160,240,480,400,.9,0]
    prediction[0,1]=[160,240,480,400,.8,0]
    node=onnx.helper.make_node("Constant",[],["detections"],value=onnx.numpy_helper.from_array(prediction))
    graph=onnx.helper.make_graph([node],"fixture",
        [onnx.helper.make_tensor_value_info("images",onnx.TensorProto.FLOAT,[1,3,640,640])],
        [onnx.helper.make_tensor_value_info("detections",onnx.TensorProto.FLOAT,[1,300,6])])
    graph_model=onnx.helper.make_model(graph,opset_imports=[onnx.helper.make_opsetid("",13)]);graph_model.ir_version=8
    onnx.helper.set_model_props(graph_model,{"end2end":str(end2end),"names":repr(names or {0:"part",1:"other"})})
    path=tmp_path/"fixture.onnx";onnx.save(graph_model,path);return path


def image():
    data=io.BytesIO();Image.new("RGB",(400,200),"gray").save(data,"PNG");data.seek(0);return data


def test_explicit_backend_maps_letterbox_confidence_and_preserves_no_nms_duplicates(tmp_path):
    path=model(tmp_path)
    backend=create_backend(path,["part","other"],.5,max_results=10,adapter="yolo_e2e")
    try:
        detections=backend.detect(np.zeros((200,400,3),np.uint8))
        assert len(detections)==2
        assert detections[0].xyxy==pytest.approx((100,50,300,150))
        assert detections[0].confidence==pytest.approx(.9) and detections[0].label=="part"
    finally: backend.close()
    with pytest.raises(RuntimeError): backend.detect(np.zeros((200,400,3),np.uint8))


def test_raw_adapter_never_silently_decodes_six_column_end_to_end_as_two_class_raw(tmp_path):
    path=model(tmp_path)
    with pytest.raises(ValueError,match="端到端"): create_backend(path,["part","other"])
    with pytest.raises(ValueError,match="未登记"): create_backend(path,["part"],adapter="automatic")


@pytest.mark.parametrize("row",[[0,0,640,640,.9,1.5],[0,0,640,640,.9,2],
                               [0,0,640,640,1.1,0],[0,0,640,640,float("nan"),0]])
def test_invalid_class_probability_or_nan_fails_closed(tmp_path,row):
    backend=create_backend(model(tmp_path,row=row),["part","other"],adapter="yolo_e2e")
    try:
        with pytest.raises(ValueError): backend.detect(np.zeros((200,400,3),np.uint8))
    finally: backend.close()


def test_class_order_must_match_metadata_and_false_e2e_not_accepted(tmp_path):
    path=model(tmp_path)
    with pytest.raises(ValueError,match="类别顺序"): create_backend(path,["other","part"],adapter="yolo_e2e")
    path=model(tmp_path,end2end=False)
    with pytest.raises(ValueError,match="端到端"): create_backend(path,["part","other"],adapter="yolo_e2e")


def test_predict_reuses_registry_for_machine_suggestions(tmp_path):
    path=model(tmp_path);source=tmp_path/"image.png";source.write_bytes(image().getvalue())
    targets=predict({"model_path":str(path),"classes":["part","other"],"project_classes":["part"],
                     "adapter":"yolo_e2e","threshold":.5,"image_path":str(source)})
    assert len(targets)==2 and targets[0]["label_id"]==0
    assert targets[0]["xyxy_norm"]==pytest.approx([.25,.25,.75,.75])
    assert targets[0]["confidence"]==pytest.approx(.9)


def test_quality_manifest_and_real_trial_subprocess_keep_adapter_and_license(tmp_path):
    folder=tmp_path/"models/public";folder.mkdir(parents=True)
    path=model(folder)
    entry={"id":"pcb-fixture","name":"Fixture","model_path":"models/public/fixture.onnx",
           "classes":["part","other"],"sha256":fingerprint(path),"adapter":"yolo_e2e",
           "adapter_compatible":True,"license_status":"fixture-not-a-released-model","source":{"revision":"fixture"}}
    (folder/"quality-models.json").write_text(json.dumps({"schema_version":1,"models":[entry]}))
    engines=LabelEngines();assert register_public_models(engines,tmp_path)==1
    registered=engines.snapshot("pcb-fixture")
    assert registered["adapter"]=="yolo_e2e" and registered["usage"]=="evaluation_only"
    assert registered["commercial_review_required"] is True
    result=ModelTrials(engines).trial("pcb-fixture",image(),.5)
    assert len(result["targets"])==2 and result["model_sha256"]==fingerprint(path)
    assert result["targets"][0]["xyxy_norm"]==pytest.approx([.25,.25,.75,.75])

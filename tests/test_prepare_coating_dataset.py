"""转换使用小型格式fixture；不把fixture训练为模型或声称真实效果。"""
import csv
import io
import json
from pathlib import Path
import zipfile

from PIL import Image
import pytest

from tools.prepare_coating_dataset import fingerprint, prepare
from visual.training_worker import inspect_training_dataset


def source(tmp_path, *, missing_val_class=False, bad_label=False):
    path=tmp_path/"source.zip"
    rows=[]
    with zipfile.ZipFile(path,"w") as z:
        for i,(group,cid) in enumerate([("R1",0),("R1",1),("R7",0),("R7",1),("R7",None)],1):
            name=f"image_{i}.jpg";buf=io.BytesIO()
            Image.new("RGB",(32,24),(i*35,20,30)).save(buf,"JPEG")
            z.writestr("detection/images/"+name,buf.getvalue())
            rows.append([f"{group}-700um-center_frame_{i}_patch_0.png",name])
            if cid is not None and not (missing_val_class and i==4):
                line=f"{cid} .5 .5 .3 .3\n"
                if bad_label and i==1: line="0 nan .5 .3 .3"
                z.writestr(f"detection/labels/image_{i}.txt",line)
        buf=io.StringIO();writer=csv.writer(buf);writer.writerow(["original_file_name","file_name"]);writer.writerows(rows)
        z.writestr("classification/labels.csv",buf.getvalue())
    return path


def test_two_classes_keep_source_rolls_separate_and_do_not_make_missing_labels_background(tmp_path):
    archive=source(tmp_path);output=tmp_path/"dataset"
    report=prepare(archive,output,expected_sha=fingerprint(archive))
    assert report["status"]=="ready_for_experimental_training"
    assert report["images"]=={"train":2,"val":2}
    assert report["excluded_missing_labels"]==1
    assert report["local_human_reviewed"] is False and report["production_ready"] is False
    meta=json.loads((output/"metadata.json").read_text())
    assert all(a["source_group"]==("R1" if a["split"]=="train" else "R7") for a in meta["source_assets"])
    assert meta["reviewed_negatives"]==[]
    assert "not reviewed in StepEye" in meta["annotation_origin"]
    assert not (output/"manifest.json").exists()  # 不伪造平台人工确认修订。
    checked=inspect_training_dataset(output/"dataset.yaml")
    assert checked["classes"]==["pinhole","unclassified"]
    assert checked["images"]==report["images"]


def test_pinhole_task_explicitly_filters_other_class_without_creating_empty_negatives(tmp_path):
    archive=source(tmp_path);output=tmp_path/"pinhole"
    report=prepare(archive,output,mode="pinhole",expected_sha=fingerprint(archive))
    assert report["classes"]==["pinhole"] and report["images"]=={"train":1,"val":1}
    assert report["excluded_outside_target"]==2
    assert all(p.read_text().strip() for p in (output/"labels").rglob("*.txt"))
    assert inspect_training_dataset(output/"dataset.yaml")["classes"]==["pinhole"]


def test_missing_class_in_held_out_batch_is_blocked_not_randomly_resplit(tmp_path):
    archive=source(tmp_path,missing_val_class=True);output=tmp_path/"two-class"
    report=prepare(archive,output,expected_sha=fingerprint(archive))
    assert report["status"]=="blocked_missing_class"
    assert report["missing_class_splits"]==["val:unclassified"]
    assert report["train_groups"]==["R1"] and report["val_groups"]==["R7"]
    with pytest.raises(ValueError,match="正样本"): inspect_training_dataset(output/"dataset.yaml")


def test_output_is_deterministic_and_cannot_overwrite(tmp_path):
    archive=source(tmp_path);sha=fingerprint(archive)
    a,b=tmp_path/"a",tmp_path/"b"
    assert prepare(archive,a,expected_sha=sha)==prepare(archive,b,expected_sha=sha)
    for name in ["metadata.json","classes.txt","dataset.yaml","preparation.json"]:
        assert (a/name).read_bytes()==(b/name).read_bytes()
    with pytest.raises(ValueError,match="覆盖"): prepare(archive,a,expected_sha=sha)


@pytest.mark.parametrize("options",[{"expected_sha":"0"*64},{"train_groups":["R1"],"val_groups":["R1"]},
                                     {"train_groups":["random-frame"]},{"mode":"random"}])
def test_bad_hash_or_overlapping_unrecognized_groups_cannot_write_dataset(tmp_path,options):
    archive=source(tmp_path);output=tmp_path/"out"
    kwargs={"expected_sha":fingerprint(archive)}|options
    with pytest.raises(ValueError): prepare(archive,output,**kwargs)
    assert not output.exists()


def test_nan_annotation_is_rejected_before_output_creation(tmp_path):
    archive=source(tmp_path,bad_label=True);output=tmp_path/"out"
    with pytest.raises(ValueError): prepare(archive,output,expected_sha=fingerprint(archive))
    assert not output.exists()

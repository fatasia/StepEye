"""Conflicting duplicates are rejected before training; quarantine keeps old splits."""
import importlib.util
import json
from pathlib import Path
import shutil

from PIL import Image
import pytest

from visual.training_worker import inspect_training_dataset
from test_training_worker import dataset

spec = importlib.util.spec_from_file_location('quarantine_conflicts', Path(__file__).parents[1]/'tools/quarantine_detection_conflicts.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_same_image_conflicting_labels_rejected(tmp_path):
    yaml = dataset(tmp_path)
    root = yaml.parent
    shutil.copyfile(root/'images/train/train.png', root/'images/train/copy.png')
    (root/'labels/train/copy.txt').write_text('0 .5 .5 .2 .2')
    with pytest.raises(ValueError, match='标注冲突'):
        inspect_training_dataset(yaml)


def test_same_annotations_decimal_format_and_order_are_not_conflicts(tmp_path):
    yaml = dataset(tmp_path)
    root = yaml.parent
    shutil.copyfile(root/'images/train/train.png', root/'images/train/copy.png')
    (root/'labels/train/train.txt').write_text('0 .5 .5 .4 .4\n0 .2 .2 .1 .1\n')
    (root/'labels/train/copy.txt').write_text('0 0.200000 0.200000 .1 .1\n0 .500000 .5 .400000 .4\n')
    assert inspect_training_dataset(yaml)['images']['train'] == 2


def test_quarantine_excludes_all_conflicts_and_unreviewed_empty_without_split_changes(tmp_path):
    yaml = dataset(tmp_path)
    previous = yaml.parent
    source = tmp_path/'source';source.mkdir()
    for split in ('train','val','test'):
        (previous/'images'/split).mkdir(exist_ok=True)
        (previous/'labels'/split).mkdir(exist_ok=True)
    Image.new('RGB',(20,20),'blue').save(previous/'images/test/test.png')
    (previous/'labels/test/test.txt').write_text('0 .5 .5 .4 .4')
    Image.new('RGB',(20,20),'yellow').save(previous/'images/train/good.png')
    (previous/'labels/train/good.txt').write_text('0 .5 .5 .4 .4')
    Image.new('RGB',(20,20),'black').save(previous/'images/train/background.png')
    (previous/'labels/train/background.txt').write_text('')
    (previous/'metadata.json').write_text(json.dumps({'dataset_version_id':'old',
        'reviewed_negatives':['labels/train/background.txt']}))
    (previous/'classes.txt').write_text('part\n')
    for split in ('train','val','test'):
        for image in (previous/'images'/split).iterdir():
            shutil.copyfile(image,source/image.name)
            shutil.copyfile(previous/'labels'/split/image.with_suffix('.txt').name,source/image.with_suffix('.txt').name)
    shutil.copyfile(source/'train.png',source/'second_annotation.png')
    (source/'second_annotation.txt').write_text('0 .5 .5 .2 .2\n')
    before=inspect_training_dataset(yaml)['dataset_sha256']
    output=tmp_path/'quarantine'
    report=module.quarantine(source,previous,output)
    assert len(report['conflicts']) == 1
    assert {r['reason'] for r in report['excluded']} == {'conflicting_source_annotations','empty_source_label_not_independently_reviewed'}
    assert report['splits']['train']['images'] == 1
    assert [r['image'] for r in report['retained'] if r['split']=='test'] == ['images/test/test.png']
    assert inspect_training_dataset(yaml)['dataset_sha256'] == before
    assert json.loads((output/'metadata.json').read_text())['reviewed_negatives'] == []
    with pytest.raises(ValueError,match='Output exists'):
        module.quarantine(source,previous,output)

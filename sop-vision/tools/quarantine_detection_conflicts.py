"""Quarantine contradictory duplicate images; never select the richest annotation."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.training_worker import IMAGE_EXTS, fingerprint, inspect_training_dataset


def label_signature(path):
    """Compare numeric YOLO rows, not ordering or textual decimal precision."""
    rows = []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5 or not fields[0].isdigit():
            raise ValueError(f'Expected YOLO box labels: {path.name}')
        rows.append((int(fields[0]), *(round(float(v), 6) for v in fields[1:])))
    return tuple(sorted(rows))


def audit_flat(source):
    """Keep all source annotation variants in the review report, including empty ones."""
    source = Path(source)
    groups = defaultdict(list)
    for image in sorted(source.iterdir()):
        if not image.is_file() or image.suffix.lower() not in IMAGE_EXTS:
            continue
        label = image.with_suffix('.txt')
        rows = label_signature(label)
        groups[fingerprint(image)].append({'image': image.name, 'label': label.name,
            'label_sha256': fingerprint(label), 'boxes': rows})
    conflicts, duplicates = [], []
    for digest, variants in groups.items():
        if len(variants) < 2:
            continue
        item = {'image_sha256': digest, 'variants': variants}
        (conflicts if len({tuple(v['boxes']) for v in variants}) > 1 else duplicates).append(item)
    return {'source_images': sum(len(v) for v in groups.values()),
            'conflicts': conflicts, 'consistent_duplicates': duplicates}


def quarantine(source, previous, output):
    """Preserve existing splits; reject every conflicting copy, never promote test data."""
    previous, output = Path(previous), Path(output)
    if output.exists():
        raise ValueError('Output exists; preserve frozen versions')
    parent = inspect_training_dataset(previous/'dataset.yaml')
    report = audit_flat(source)
    conflict_hashes = {g['image_sha256'] for g in report['conflicts']}
    selected, excluded, counts = [], [], defaultdict(Counter)
    for split in ('train', 'val', 'test'):
        for image in sorted((previous/'images'/split).rglob('*')):
            if not image.is_file():
                continue
            relative = image.relative_to(previous/'images'/split)
            label = previous/'labels'/split/relative.with_suffix('.txt')
            digest = fingerprint(image)
            rows = label_signature(label)
            reason = ('conflicting_source_annotations' if digest in conflict_hashes else
                      'empty_source_label_not_independently_reviewed' if not rows else None)
            identity = {'image': f'images/{split}/{relative.as_posix()}',
                        'split': split, 'image_sha256': digest, 'label_sha256': fingerprint(label)}
            if reason:
                excluded.append({**identity, 'reason': reason})
            else:
                selected.append((image, label, relative, identity))
                counts[split].update(parent['classes'][row[0]] for row in rows)
    for split in ('train', 'val', 'test'):
        if set(counts[split]) != set(parent['classes']):
            raise ValueError(f'{split} lacks declared classes after quarantine')
    report.update(parent_dataset_sha256=parent['dataset_sha256'],
        parent_dataset_version=parent['dataset_version'], excluded=excluded,
        split_rule='preserve parent split of every retained image; no new sampling',
        label_policy='exclude all contradictory source copies; no annotation chosen automatically',
        reviewed_negatives=[], remaining_label_quality='not independently reannotated; no accuracy claim')
    for image, label, relative, identity in selected:
        split = identity['split']
        for source_file, destination in ((image, output/'images'/split/relative),
                                        (label, output/'labels'/split/relative.with_suffix('.txt'))):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination)
        if (fingerprint(output/identity['image']) != identity['image_sha256'] or
                fingerprint(output/'labels'/split/relative.with_suffix('.txt')) != identity['label_sha256']):
            raise ValueError('Source changed during copy')
    for name in ('dataset.yaml', 'classes.txt'):
        shutil.copyfile(previous/name, output/name)
    metadata = json.loads((previous/'metadata.json').read_text(encoding='utf8'))
    metadata.update(dataset_version_id='SWS1-CONFLICT-QUARANTINE-V1', reviewed_negatives=[],
                    parent_dataset_sha256=parent['dataset_sha256'],
                    notes='All conflicting source copies and unreviewed empty source labels excluded; parent splits preserved.')
    (output/'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf8')
    report['retained'] = [row[3] for row in selected]
    report['splits'] = {split: {'images': sum(r[3]['split'] == split for r in selected),
                               'boxes_by_class': dict(counts[split])} for split in counts}
    checked = inspect_training_dataset(output/'dataset.yaml')
    report['dataset_sha256'] = checked['dataset_sha256']
    (output/'quarantine.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'previous', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    report = quarantine(**vars(parser.parse_args()))
    print(json.dumps({'conflict_groups': len(report['conflicts']), 'excluded_images': len(report['excluded']),
                      'splits': report['splits'], 'dataset_sha256': report['dataset_sha256']}))

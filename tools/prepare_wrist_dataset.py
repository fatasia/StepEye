"""Prepare close-up wrist-strap YOLO data from audited public Edge Impulse rows.

Author test remains untouched. Validation uses whole project-created-minute groups from
training; this does not make the author's same-session test an independent site.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil

import yaml

CLASSES = ['ring', 'watch', 'wrist-strap']


def prepare(audit_path: Path, rows_path: Path, output: Path):
    if output.exists():
        raise ValueError('Output exists; preserve previous dataset')
    audit = json.loads(audit_path.read_text(encoding='utf8'))
    rows = {r['id']: r for r in json.loads(rows_path.read_text(encoding='utf8'))}
    assets = audit['assets']
    if audit['classes'] != CLASSES or not assets:
        raise ValueError('Unexpected wrist class contract')
    groups = sorted({rows[a['id']]['created'][:16] for a in assets if a['split'] == 'training'})
    if len(groups) < 3:
        raise ValueError('Need three creation-time groups to separate validation')
    # Fixed creation-time grouping; these timestamps do not prove capture sessions.
    val_groups = set(groups[:2])
    base = audit_path.parent
    result = {'source': audit['source'], 'license': audit['license'], 'classes': CLASSES,
              'author_test_scope': 'same-session holdout, not independent factory validation',
              'validation_groups': sorted(val_groups), 'images': [], 'splits': {},
              'excluded_training_exact_duplicates': []}
    heldout_hashes = {a['pixel_sha256'] for a in assets if a['split'] == 'testing'
                      or rows[a['id']]['created'][:16] in val_groups}
    selected = []
    for asset in assets:
        group = rows[asset['id']]['created'][:16]
        split = 'test' if asset['split'] == 'testing' else 'val' if group in val_groups else 'train'
        if split == 'train' and asset['pixel_sha256'] in heldout_hashes:
            result['excluded_training_exact_duplicates'].append(asset['id'])
            continue
        path = (base / asset['path']).resolve()
        if not path.is_relative_to(base.resolve()):
            raise ValueError('Image outside audited dataset')
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
            raise ValueError('Image SHA differs from audit')
        lines = []
        for target in asset['targets']:
            x0, y0, x1, y1 = target['xyxy_norm']
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError('Invalid audited box')
            cid = CLASSES.index(target['label'])
            lines.append(f'{cid} {(x0+x1)/2:.10f} {(y0+y1)/2:.10f} {x1-x0:.10f} {y1-y0:.10f}')
        selected.append((asset, path, split, group, lines))
    for split in ['train', 'val', 'test']:
        subset = [a for a, _, s, _, _ in selected if s == split]
        box_counts = Counter(t['label'] for a in subset for t in a['targets'])
        positive_images = Counter(label for a in subset for label in set(t['label'] for t in a['targets']))
        if not subset or any(box_counts[c] == 0 for c in CLASSES):
            raise ValueError(f'{split} lacks one or more declared classes')
        result['splits'][split] = {'images': len(subset), 'boxes_by_class': dict(box_counts),
                                   'positive_images_by_class': dict(positive_images)}
    for asset, source, split, group, lines in selected:
        stem = str(asset['id'])
        image = output / 'images' / split / f'{stem}.jpg'
        label = output / 'labels' / split / f'{stem}.txt'
        image.parent.mkdir(parents=True, exist_ok=True)
        label.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, image)
        label.write_text('\n'.join(lines) + '\n', encoding='utf8')
        result['images'].append({'id': asset['id'], 'split': split, 'source_group': group,
                                 'source_sha256': asset['sha256']})
    (output / 'data.yaml').write_text(yaml.safe_dump({'path': str(output.resolve()),
        'train': 'images/train', 'val': 'images/val', 'test': 'images/test', 'names': CLASSES},
        sort_keys=False), encoding='utf8')
    # Platform training never receives the author's test split.
    (output / 'dataset.yaml').write_text(yaml.safe_dump({'path': '.',
        'train': 'images/train', 'val': 'images/val', 'names': CLASSES}), encoding='utf8')
    (output / 'source.json').write_text(json.dumps(result, indent=2), encoding='utf8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', required=True, type=Path)
    parser.add_argument('--rows', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = prepare(args.audit, args.rows, args.output)
    print(json.dumps({k:v for k,v in result.items() if k != 'images'}, indent=2))

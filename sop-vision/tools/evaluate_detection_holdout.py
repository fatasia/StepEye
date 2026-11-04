"""Fixed single-operating-point evaluation of a LabelStore dataset ZIP."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from visual.detection_holdout import run_holdout

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','model','output','model-sha256','dataset-sha256'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--adapter',choices=['yolo_raw','yolo_e2e','kairos_rtdetr'],default='yolo_raw')
    p.add_argument('--confidence',type=float,default=.25)
    p.add_argument('--iou',type=float,default=.5)
    args=vars(p.parse_args())
    report=run_holdout(**args)
    print(json.dumps({'output':args['output'],'summary':report['evaluation']['summary']}))

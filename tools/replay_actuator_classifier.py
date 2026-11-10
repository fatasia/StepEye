"""Replay one held-out record per class through the actuator report API."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import tempfile

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.mechanism_actuator_classifier import ActuatorClassifierService
from visual.mechanism_actuator_classifier_routes import actuator_classifier_router
from visual.mechanism_routes import MechanismReports, router


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source_run
    metadata = json.loads((source / 'records.json').read_text())
    data = np.load(source / 'replay.npz', allow_pickle=False)
    selected = [next(r for r in metadata if r['label'] == label and r['repeat'] == 9) for label in range(4)]
    body = {'title': 'Cranfield holdout replay', 'dataset': 'Cranfield linear actuator',
            'sample_rate_hz': 25, 'channels': ['setpoint_mm', 'error_mm', 'current_A'], 'records': [
                {'id': str(r['id']), 'complete_record': True, 'load': r['load'], 'profile': r['profile'],
                 'values': data['x'][data['record_ids'] == r['id']].reshape(2000, 3).tolist()} for r in selected]}
    with tempfile.TemporaryDirectory() as directory:
        store = MechanismReports(Path(directory) / 'reports.db')
        app = FastAPI()
        app.include_router(router(store))
        app.include_router(actuator_classifier_router(store, ActuatorClassifierService(Path(__file__).resolve().parents[1])))
        try:
            with TestClient(app) as client:
                response = client.post('/api/mechanism/actuator-classifier/analyses', json=body)
                response.raise_for_status()
                report = response.json()
                assert client.get('/api/mechanism/analyses/' + report['id']).json() == report
                assert client.get('/api/mechanism/analyses').json()['total'] == 1
                assert client.get('/api/mechanism/analyses/' + report['id'] + '/export').status_code == 200
            predicted = np.array([r['probabilities'] for r in report['result']['records']])
            expected = data['probabilities'][[r['id'] for r in selected]]
            delta = float(np.max(np.abs(predicted - expected)))
            assert delta < 1e-4
            evidence = {'source_run': source.name, 'replay_sha256': hashlib.sha256((source/'replay.npz').read_bytes()).hexdigest(),
                        'records': selected, 'result': report['result'], 'max_abs_error_vs_source': delta,
                        'api_create_detail_list_export': 'passed', 'scope': 'four-record integration check, not new accuracy evaluation'}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
            print(json.dumps({'records': len(selected), 'max_abs_error': delta, 'elapsed_ms': report['result']['elapsed_ms']}))
        finally:
            store.close()


if __name__ == '__main__':
    main()

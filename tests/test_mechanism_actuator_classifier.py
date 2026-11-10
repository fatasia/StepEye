import pytest
from pydantic import ValidationError
from visual.mechanism_actuator_classifier import ActuatorClassifierBody, ActuatorClassifierService


def payload():
    return {'dataset': 'Cranfield linear actuator', 'sample_rate_hz': 25,
            'channels': ['setpoint_mm', 'error_mm', 'current_A'],
            'records': [{'id': 'record', 'complete_record': True, 'load': '20', 'profile': 'sin',
                         'values': [[1., 0., 0.]] * 2000}]}


@pytest.mark.parametrize('change', ['short', 'channels', 'rate', 'incomplete', 'nan', 'unknown_load', 'duplicate'])
def test_contract(change):
    body = payload()
    if change == 'short': body['records'][0]['values'].pop()
    if change == 'channels': body['channels'].reverse()
    if change == 'rate': body['sample_rate_hz'] = 100
    if change == 'incomplete': body['records'][0]['complete_record'] = False
    if change == 'nan': body['records'][0]['values'][0] = [float('nan'), 0., 0.]
    if change == 'unknown_load': body['records'][0]['load'] = '30'
    if change == 'duplicate': body['records'].append(body['records'][0])
    with pytest.raises(ValidationError):
        ActuatorClassifierBody.model_validate(body)


def test_missing_and_slot_release(tmp_path):
    service = ActuatorClassifierService(tmp_path)
    assert not service.capabilities()['available']
    service._slot.acquire()
    with pytest.raises(RuntimeError): service.analyze(payload())
    service._slot.release()
    with pytest.raises(ValueError, match='未安装'): service.analyze(payload())
    assert service._slot.acquire(False)
    service._slot.release()

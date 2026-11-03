"""User CRUD invariants, credential invalidation and cross-connection admin safety."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from visual.auth import AuthError, AuthStore

@pytest.fixture
def auth(tmp_path):
    store=AuthStore(tmp_path/'auth.db')
    store.set_user('admin','password123','admin')
    yield store
    store.close()


def test_last_admin_cannot_delete_or_demote(auth):
    session=auth.login('admin','password123')
    for operation in (lambda:auth.delete_user('admin'), lambda:auth.set_user('admin','password456','viewer')):
        with pytest.raises(AuthError) as e: operation()
        assert e.value.status==409
        assert auth.list_users()[0]['role']=='admin'
        assert auth.verify_session(session)['role']=='admin'
    assert auth.delete_user('missing') is False


def test_reset_revokes_sessions_and_tokens_and_keeps_creation_time(auth):
    token=auth.issue_mcp_token('admin','fixture',['read'],[])['token']
    sessions=[auth.login('admin','password123') for _ in range(2)]
    created=auth.list_users()[0]['created']
    auth.set_user('admin','new-password','admin')
    assert all(auth.verify_session(s) is None for s in sessions)
    assert auth.verify_mcp_token(token) is None
    assert auth.list_users()[0]['created']==created
    with pytest.raises(AuthError): auth.login('admin','password123')
    assert auth.verify_session(auth.login('admin','new-password'))['role']=='admin'


def test_roles_and_crud(auth):
    auth.set_user('second','password123','admin')
    session=auth.login('second','password123')
    auth.set_user('second','password456','operator')
    assert auth.verify_session(session) is None
    assert auth.verify_session(auth.login('second','password456'))['role']=='operator'
    assert auth.delete_user('second') and not auth.delete_user('second')
    auth.set_user('质检员_01','password123','viewer')
    assert auth.verify_session(auth.login('质检员_01','password123'))['role']=='viewer'
    with pytest.raises(ValueError): auth.set_user('invalid','password123','superadmin')


@pytest.mark.parametrize('username',['',' ',' leading','trailing ','a\n','a\x00','a\x7f','a/b','a\\b','a@b','a'*65,None])
def test_username_rejected(auth,username):
    with pytest.raises(ValueError): auth.set_user(username,'password123','viewer')
    assert auth.count()==1


def test_password_size_and_failed_login_rollback(auth):
    with pytest.raises(ValueError): auth.set_user('new','x'*257)
    with pytest.raises(AuthError): auth.login('admin','x'*257)
    with pytest.raises(AuthError): auth.login('admin','bad')
    auth.set_user('new','password123','viewer')
    assert auth.count()==2


def test_concurrent_last_admin_check_across_connections(tmp_path):
    first=AuthStore(tmp_path/'auth.db'); first.set_user('a','password123','admin'); first.set_user('b','password123','admin')
    second=AuthStore(tmp_path/'auth.db'); barrier=threading.Barrier(2)
    def remove(store,name):
        barrier.wait()
        try: return store.delete_user(name)
        except AuthError as e: return e.status
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(remove,first,'a'),pool.submit(remove,second,'b')]
            results=[f.result(5) for f in futures]
        assert sorted(results)==[True,409]
        assert len(first.list_users())==1 and first.list_users()[0]['role']=='admin'
    finally: second.close(); first.close()

def test_user_rest_last_admin_missing_and_password_reset(tmp_path):
    from fastapi.testclient import TestClient
    from visual.server import build_app
    store=AuthStore(tmp_path/'auth.db'); store.set_user('admin','password123','admin')
    app=build_app([],tmp_path/'data',auth=store)
    try:
        with TestClient(app) as client:
            def login(password='password123'):
                assert client.post('/api/auth/login',json={'username':'admin','password':password}).status_code==200
            login()
            assert client.delete('/api/users/admin').status_code==409
            assert client.post('/api/users',json={'username':'admin','password':'password456','role':'viewer'}).status_code==409
            assert client.delete('/api/users/missing').status_code==404
            assert client.delete('/api/users/bad%20name').status_code==400
            assert client.post('/api/users',json={'username':'bad name','password':'password123'}).status_code==400
            assert client.post('/api/users',json={'username':'admin','password':'password456','role':'admin'}).status_code==200
            assert client.get('/api/users').status_code==401
            login('password456')
            assert client.post('/api/users',json={'username':'second','password':'password123','role':'admin'}).status_code==200
            assert client.delete('/api/users/second').status_code==200
            assert client.get('/api/users').json()[0]['username']=='admin'
    finally: store.close()

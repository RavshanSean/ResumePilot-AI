import logging
import threading

from starlette.datastructures import UploadFile
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_read_failure_isolated_and_uploads_closed(monkeypatch, caplog):
    original_read = UploadFile.read
    original_close = UploadFile.close
    closed = []

    async def read(file, size=-1):
        if file.filename == 'private-name.pdf':
            raise OSError('private internal details')
        return await original_read(file, size)

    async def close(file):
        closed.append(file.filename)
        await original_close(file)

    monkeypatch.setattr(UploadFile, 'read', read)
    monkeypatch.setattr(UploadFile, 'close', close)
    monkeypatch.setattr(main, 'parse_resume', lambda name, data: 'Synthetic evidence')
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        response = client.post('/api/analyze', files=[('files', ('private-name.pdf', b'data')), ('files', ('valid.pdf', b'data'))])
    assert response.status_code == 200
    result = response.json()
    assert result['candidates'][0]['id'] == '1'
    assert result['errors'][0]['message'] == 'This resume could not be processed. Please try again.'
    assert {'private-name.pdf', 'valid.pdf'} <= set(closed)
    assert 'private internal details' not in response.text
    assert 'private-name.pdf' not in caplog.text
    assert 'Synthetic evidence' not in caplog.text
    assert 'candidates=1 errors=1' in caplog.text


def test_parser_runs_off_event_loop(monkeypatch):
    def parse(name, data):
        assert threading.current_thread().name == 'AnyIO worker thread'
        return 'Synthetic evidence'
    monkeypatch.setattr(main, 'parse_resume', parse)
    result = client.post('/api/analyze', files=[('files', ('resume.pdf', b'data'))]).json()
    assert result['candidates'][0]['evidence'] == 'Synthetic evidence'


def test_password_protected_pdf_reports_per_file_error():
    import pymupdf
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72,72), 'Private synthetic resume')
        data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='user')
    response = client.post('/api/analyze', files=[('files', ('protected.pdf', data))])
    assert response.status_code == 200
    assert not response.json()['candidates']
    assert 'unencrypted' in response.json()['errors'][0]['message']

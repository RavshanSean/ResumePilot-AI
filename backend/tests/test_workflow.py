"""Synthetic upload-to-results contract, also runnable through the real Vite proxy."""
import io
import os

import httpx
import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(params=['in-process', 'backend', 'vite-proxy'])
def api(request):
    if request.param == 'in-process':
        with TestClient(app) as client:
            yield client
        return
    if os.getenv('RESUMEPILOT_LIVE_TESTS') != '1':
        pytest.skip('Set RESUMEPILOT_LIVE_TESTS=1 with backend and Vite running for loopback checks.')
    # Deliberately fixed to loopback: never send these uploads to external hosts.
    port = 8000 if request.param == 'backend' else 5173
    with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=30) as client:
        yield client


def pdf(text):
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), text)
        return document.tobytes()


def docx():
    document = Document()
    document.add_paragraph('Synthetic DOCX: Python APIs')
    document.add_table(rows=1, cols=1).cell(0, 0).text = 'React experience in a table'
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def test_three_pdf_upload_to_results(api):
    health = api.get('/api/health')
    assert health.status_code == 200
    assert health.json()['ai_enabled'] is False
    files = [('files', (f'resume-{i}.pdf', pdf(f'Synthetic candidate {i}: Python APIs'), 'application/pdf')) for i in range(3)]
    response = api.post('/api/analyze', files=files, data={'requirements': 'Python API experience'})
    assert response.status_code == 200
    result = response.json()
    assert result['mode'] == 'demonstration'
    assert not result['errors']
    assert len(result['candidates']) == 3
    for i, candidate in enumerate(result['candidates']):
        # These fields are consumed by the unchanged frontend's original-file binding.
        assert candidate['id'] == str(i)
        assert candidate['filename'] == files[i][1][0]
        assert f'Synthetic candidate {i}' in candidate['evidence']
        assert candidate['status'] == 'Needs review'
        assert candidate['demo'] is True
        assert len(candidate['evidence']) <= 2000


def test_mixed_batch_preserves_original_indexes_and_docx_tables(api):
    response = api.post('/api/analyze', files=[
        ('files', ('resume.pdf', pdf('First synthetic candidate'), 'application/pdf')),
        ('files', ('broken.pdf', b'not a pdf', 'application/pdf')),
        ('files', ('resume.pdf', pdf('Second synthetic candidate'), 'application/pdf')),
        ('files', ('resume.docx', docx(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
    ])
    assert response.status_code == 200
    result = response.json()
    assert [candidate['id'] for candidate in result['candidates']] == ['0', '2', '3']
    assert result['errors'][0]['filename'] == 'broken.pdf'
    assert 'Second synthetic candidate' in result['candidates'][1]['evidence']
    assert 'React experience in a table' in result['candidates'][2]['evidence']


def test_cors_allowed_origins_and_unapproved_origin(api):
    for origin in ('http://localhost:5173', 'http://127.0.0.1:5173'):
        response = api.options('/api/analyze', headers={
            'Origin': origin, 'Access-Control-Request-Method': 'POST',
        })
        # Vite answers preflight itself with 204; FastAPI answers with 200.
        assert response.status_code in (200, 204)
        assert response.headers['access-control-allow-origin'] == origin
    response = api.options('/api/analyze', headers={
        'Origin': 'https://unapproved.invalid', 'Access-Control-Request-Method': 'POST',
    })
    assert response.status_code in (400, 204)
    assert 'access-control-allow-origin' not in response.headers


def test_request_validation_and_size_limit(api):
    assert api.post('/api/analyze', data={'requirements': 'Python'}).status_code == 422
    response = api.post('/api/analyze', files=[('files', ('oversized.pdf', b'x' * (10 * 1024 * 1024 + 1)))])
    assert response.status_code == 200
    assert not response.json()['candidates']
    assert '10 MB' in response.json()['errors'][0]['message']
    response = api.post('/api/analyze', files=[('files', ('resume.pdf', pdf('Synthetic resume')))], data={'requirements': 'x' * 20001})
    assert response.status_code == 400

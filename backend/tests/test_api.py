import io
import pymupdf
from docx import Document
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_discloses_demo():
    assert client.get('/api/health').json()['ai_enabled'] is False


def test_docx_extracts_evidence():
    doc = Document()
    doc.add_paragraph('Built Python APIs for five years.')
    buffer = io.BytesIO()
    doc.save(buffer)
    result = client.post('/api/analyze', files=[('files', ('candidate.docx', buffer.getvalue()))]).json()
    assert 'Built Python APIs' in result['candidates'][0]['evidence']
    assert result['candidates'][0]['status'] == 'Needs review'
    assert result['mode'] == 'demonstration'


def test_pdf_and_invalid_file():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), 'Python engineer')
    data = doc.tobytes()
    doc.close()
    result = client.post('/api/analyze', files=[('files', ('valid.pdf', data)), ('files', ('bad.pdf', b'broken'))]).json()
    assert 'Python engineer' in result['candidates'][0]['evidence']
    assert len(result['errors']) == 1


def test_rejects_unsupported_and_empty():
    for name, content in [('file.txt', b'text'), ('empty.pdf', b'')]:
        assert client.post('/api/analyze', files=[('files', (name, content))]).json()['errors']


def test_batch_limit():
    response = client.post('/api/analyze', files=[('files', (f'{n}.pdf', b'a')) for n in range(31)])
    assert response.status_code == 400


def test_unreadable_pdf_and_long_requirements():
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    assert client.post('/api/analyze', files=[('files', ('scan.pdf', data))]).json()['errors']
    assert client.post('/api/analyze', data={'requirements': 'x' * 20001}, files=[('files', ('file.pdf', b'x'))]).status_code == 400

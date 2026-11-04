"""Previews through the API: images, resolution and gray PNGs."""

import io
import json

import pytest
from PIL import Image
from flask.testing import FlaskClient

from tests.helpers import EXAMPLE_FORMDATA, make_client, TEXT, verify_image


class TestLabelDesignerAPI:
    @pytest.fixture(autouse=True)
    def client(self, tmp_path):
        yield make_client(tmp_path)

    def test_generate_preview(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': 'Left',
                'size': '60',
                'align': 'left'
            },
            {
                'font': 'Droid Sans,Mono',
                'text': '-- LONG MONO TEXT --',
                'size': '50',
                'align': 'center'
            }
        ])

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'simple.png')

    def test_generate_preview_high_res(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['high_res'] = 1
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': 'Left',
                'size': '60',
                'align': 'left'
            },
            {
                'font': 'Droid Sans,Mono',
                'text': '-- LONG MONO TEXT --',
                'size': '50',
                'align': 'center'
            }
        ])

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'simple_high_res.png')

    def test_generate_preview_inverted(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': '!!! LEFT !!!',
                'size': '50',
                'align': 'left',
                'inverted': 'true'
            },
            {
                'font': 'DejaVu Sans,Book',
                'text': '!!! CENTER !!!',
                'size': '50',
                'align': 'center',
                'inverted': 'true'
            },
            {
                'font': 'DejaVu Sans,Book',
                'text': '!!! RIGHT !!!',
                'size': '50',
                'align': 'right',
                'inverted': 'true'
            },
            {
                'font': 'Droid Sans,Mono',
                'text': '-- LONG MONO TEXT --',
                'size': '50',
                'align': 'center'
            }
        ])

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'inverted_text.png')

    def test_generate_preview_rotated(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['orientation'] = 'rotated'
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': 'Left',
                'size': '60',
                'align': 'left'
            },
            {
                'font': 'Droid Sans,Mono',
                'text': '-- LONG MONO TEXT --',
                'size': '50',
                'align': 'center'
            }
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'rotated.png')

    def test_concurrent_preview_requests(self, client: FlaskClient):
        """
        Spawn multiple threads that each create their own test client instance
        and post to the preview endpoint. Using threads avoids pickling issues
        with Flask test clients when using multiprocessing.Process.
        """
        from threading import Thread

        results = []
        # Protect shared list
        import threading
        results_lock = threading.Lock()

        def make_request(i: int):
            # Each thread creates its own test client to avoid sharing state
            app = client.application
            data = EXAMPLE_FORMDATA.copy()
            data['text'] = json.dumps([
                {'font': 'DejaVu Sans,Book', 'text': str(i), 'size': '32', 'align': 'center'}
            ])
            with app.test_client() as local_client:
                resp = local_client.post('/labeldesigner/api/preview', data=data)
                with results_lock:
                    results.append(resp.status_code)

        threads = [Thread(target=make_request, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 50
        assert all(code == 200 for code in results)


def test_gray_preview_is_made_without_looking_at_each_pixel(tmp_path, monkeypatch):
    # Whether red or color is chosen says it: a large label needed several
    # copies of its pixels to find out again
    def split(self):
        raise AssertionError('split')
    monkeypatch.setattr(Image.Image, 'split', split)
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, text=TEXT))
    assert response.status_code == 200 and Image.open(io.BytesIO(response.data)).mode == 'L'


@pytest.mark.parametrize('settings, mode', [
    ({}, 'L'),
    # With red or color chosen it stays in color, even with nothing red on it
    ({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Red', 'size': '40', 'color': 'red'}])}, 'RGB'),
    ({'print_color': 'red'}, 'RGB'),
    # A frame is red only where it is drawn
    ({'border_color': 'red', 'border_thickness': '0'}, 'L'),
    ({'border_color': 'red', 'border_thickness': '2'}, 'RGB'),
    ({'print_type': 'image', 'image_mode': 'colored'}, 'RGB'),
    ({'print_type': 'image', 'image_mode': 'red_and_black'}, 'RGB'),
    ({'print_type': 'image', 'image_mode': 'grayscale'}, 'L'),
])
def test_preview_without_red_or_color_is_stored_in_gray(tmp_path, settings, mode):
    # The same pixels in half the bytes, the reference images check them
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Gray', 'size': '40'}]))
    data.update(settings)
    if data.get('print_type') == 'image':
        image = Image.new('L', (200, 100), 255)
        image.paste(0, (50, 25, 150, 75))
        out = io.BytesIO()
        image.save(out, 'PNG')
        data['image'] = (io.BytesIO(out.getvalue()), 'gray.png', 'image/png')
    response = client.post('/labeldesigner/api/preview', data=data, content_type='multipart/form-data')
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).mode == mode

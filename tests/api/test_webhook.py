"""The webhook that prints the images sent to it."""

import base64
import io

from PIL import Image

from app.labeldesigner.enums import LabelOrientation

from tests.helpers import _red_square, _upload, make_client


def test_webhook_accepts_pdf_in_json(tmp_path):
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    buffer = io.BytesIO()
    _red_square().save(buffer, 'PDF')
    payload = {'password': 'secret', 'images': [{'data': base64.b64encode(buffer.getvalue()).decode('ascii'), 'mime': 'application/pdf'}]}
    response = client.post('/labeldesigner/api/webhook/print', json=payload)
    assert response.status_code == 200
    assert response.get_json() == {'success': True, 'count': 1}


def test_webhook_authentication(tmp_path):
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    url = '/labeldesigner/api/webhook/print'
    # Wrong passwords are refused, including ones with non-ASCII characters
    assert client.post(url, headers={'Authorization': 'Bearer pässwort'}).status_code == 401
    # The password is not accepted in the URL, it would end up in access logs
    assert client.post(url + '?password=secret').status_code == 401
    # Header, form field and JSON body are accepted (no images sent, though)
    for kwargs in ({'headers': {'Authorization': 'Bearer secret'}}, {'data': {'password': 'secret'}}, {'json': {'password': 'secret'}}):
        response = client.post(url, **kwargs)
        assert response.status_code == 400
        assert response.get_json()['message'] == 'No images provided'


def test_webhook_image_count_is_limited(tmp_path):
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    client.application.config['PRINT_MAX_COUNT'] = 2
    image = base64.b64encode(_upload(_red_square(), 'red.png').stream.read()).decode('ascii')
    response = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': [image] * 3})
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Too many images, at most 2 per request'


def test_webhook_refuses_images_too_large_for_the_label(tmp_path):
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    # Scaled to the label width, this thin image would get 69 million pixels high
    image = base64.b64encode(_upload(Image.new('L', (1, 100000), 'black'), 'thin.png').stream.read()).decode('ascii')
    response = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': [image]})
    assert response.status_code == 400


def test_webhook_turns_images_on_die_cut_labels(tmp_path, monkeypatch):
    from app.labeldesigner import routes
    labels = []
    monkeypatch.setattr(routes.PrinterQueue, 'add_label_to_queue', lambda self, label, **kwargs: labels.append(label))
    monkeypatch.setattr(routes.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    images = [base64.b64encode(_upload(Image.new('L', size, 'black'), 'image.png').stream.read()).decode('ascii')
              for size in ((1200, 1800), (1800, 1200))]
    payload = {'password': 'secret', 'label_size': '103x164', 'model': 'QL-1100', 'orientation': 'standard', 'images': images}
    assert client.post('/labeldesigner/api/webhook/print', json=payload).get_json() == {'success': True, 'count': 2}
    assert [(label._width, label._height, label.label_orientation) for label in labels] == [
        (1200, 1822, LabelOrientation.ROTATED), (1822, 1200, LabelOrientation.STANDARD)]


def test_webhook_takes_a_threshold_of_0(tmp_path, monkeypatch):
    # 0 was taken for left out, the threshold was the default
    from app.labeldesigner import routes
    thresholds = []
    convert = routes._convert_image
    monkeypatch.setattr(routes, '_convert_image', lambda img, mode, threshold=70, contrast=False: thresholds.append(threshold) or convert(img, mode, threshold, contrast))
    monkeypatch.setattr(routes.PrinterQueue, 'add_label_to_queue', lambda self, label, **kwargs: None)
    monkeypatch.setattr(routes.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = base64.b64encode(_upload(_red_square(), 'image.png').stream.read()).decode('ascii')
    for given, used in ((0, 0), ('0', 0), (None, client.application.config['IMAGE_DEFAULT_BW_THRESHOLD'])):
        payload = {'password': 'secret', 'label_size': '62', 'image_mode': 'black_and_white', 'bw_threshold': given, 'images': [image]}
        assert client.post('/labeldesigner/api/webhook/print', json=payload).get_json() == {'success': True, 'count': 1}
        assert int(thresholds.pop()) == used


def test_webhook_refuses_an_unknown_image_mode(tmp_path, monkeypatch):
    # It printed in black and white, bw still does
    from app.labeldesigner import routes
    monkeypatch.setattr(routes.PrinterQueue, 'add_label_to_queue', lambda self, label, **kwargs: None)
    monkeypatch.setattr(routes.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = base64.b64encode(_upload(_red_square(), 'image.png').stream.read()).decode('ascii')
    payload = {'password': 'secret', 'label_size': '62', 'images': [image]}
    response = client.post('/labeldesigner/api/webhook/print', json=dict(payload, image_mode='sepia'))
    assert response.status_code == 400
    assert response.get_json() == {'success': False, 'message': 'image_mode must be grayscale, black_and_white, red_and_black or colored'}
    assert client.post('/labeldesigner/api/webhook/print', json=dict(payload, image_mode='bw')).get_json() == {'success': True, 'count': 1}

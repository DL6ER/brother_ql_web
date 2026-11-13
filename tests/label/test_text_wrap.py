"""Long lines wrapped into lines that fit the label: where they wrap, how
wrapped text is fitted, and where lines do not wrap."""

import json

import pytest

from app.labeldesigner import label as label_module
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import EXAMPLE_FORMDATA, make_client, pixels, verify_image

SENTENCE = 'Store in a cool and dry place away from direct sunlight'
WORD = 'Donaudampfschifffahrtsgesellschaftskapitaen'


def lines(*texts, size=40, **settings):
    return json.dumps([dict({'font': 'DejaVu Sans,Book', 'text': text, 'size': str(size), 'align': 'center'}, **settings)
                       for text in texts])


def preview(client, **data):
    return client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, **data))


def drawn(label) -> list:
    """The lines of a label generated before, as they are drawn."""
    return [entry[2]['text'] for entry in label._measure()[0]]


@pytest.mark.parametrize('settings, reference', [
    # On a die-cut label, at the size as set
    ({'label_size': '62x29', 'text': lines(SENTENCE), 'text_fit': 'none'}, 'wrap_die_cut.png'),
    # Endless tape grows with the lines
    ({'label_size': '62', 'text': json.dumps(json.loads(lines('Pantry', size=70)) + json.loads(lines(SENTENCE, size=50))),
      'text_fit': 'none'}, 'wrap_endless.png'),
    # The lines a to-do line wrapped into start where its text starts,
    # without a box
    ({'label_size': '62', 'text': lines('Buy milk, bread, butter and a bag of coffee beans', 'Call the plumber', align='left', checkbox=True),
      'text_fit': 'none'}, 'wrap_to_do.png'),
    # Beside a code, in the room left of it, shrunk where it is too large
    ({'label_size': '62x29', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_position': 'left',
      'text': lines(SENTENCE), 'text_fit': 'shrink'}, 'wrap_beside_a_code.png'),
], ids=['die_cut', 'endless', 'to_do', 'beside_a_code'])
def test_long_lines_wrap_into_lines_that_fit(tmp_path, settings, reference):
    client = make_client(tmp_path)
    one_line = preview(client, **settings)
    wrapped = preview(client, text_wrap='1', **settings)
    assert wrapped.status_code == 200 and 'X-Label-Warning' not in wrapped.headers
    # Without wrapping the text is cut off or made small
    assert pixels(one_line.data) != pixels(wrapped.data)
    verify_image(wrapped.data, reference)


@pytest.mark.parametrize('text_fit', ['shrink', 'fill'])
@pytest.mark.parametrize('values', [
    {'text': lines(SENTENCE, size=90)},
    # A value filled in when printing, of a length nobody knew before
    {'text': lines('{{blank:Product}}', size=90), 'blanks': json.dumps({'Product': SENTENCE})},
], ids=['text', 'blank'])
def test_wrapped_text_is_fitted_at_the_largest_size_that_fits(tmp_path, text_fit, values):
    # Lines wrapped anew at each size do not shrink in proportion
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62x29', text_fit=text_fit, **values)
    with client.application.test_request_context():
        def fitted(**more):
            label = create_label_from_request(dict(data, **more))
            label.generate()
            assert label.warnings == []
            return label
        label, one_line = fitted(text_wrap='1'), fitted()
        size = label._line_size(label.text[0])
        assert size > one_line._line_size(one_line.text[0]) and len(drawn(label)) > 1
        if text_fit == 'shrink':
            assert size < 90
        # One size more does not fit
        space = label._text_space(label._width, label._height, 0, 0, 0)
        assert label._fits_room(label._measure()[1], *space)
        label._text_scale = (size + 1.5) / 90
        assert label._line_size(label.text[0]) == size + 1
        assert not label._fits_room(label._measure()[1], *space)


@pytest.mark.parametrize('settings', [
    # Along endless tape the label grows with the lines
    {'label_size': '62', 'orientation': 'rotated', 'text_fit': 'none'},
    # Filled on endless tape, each line is as wide as the tape
    {'label_size': '62', 'text_fit': 'fill'},
    # Lines that fit stay as they are
    {'label_size': '62x29', 'text_fit': 'none', 'text': lines('Store cool', 'and dry')},
], ids=['along_endless_tape', 'filled_on_endless_tape', 'lines_that_fit'])
def test_lines_that_do_not_wrap(tmp_path, settings):
    client = make_client(tmp_path)
    data = dict({'text': lines(SENTENCE, size=50)}, **settings)
    assert pixels(preview(client, **data).data) == pixels(preview(client, text_wrap='1', **data).data)


@pytest.mark.parametrize('settings', [
    {'orientation': 'rotated', 'text_fit': 'none'},
    {'text_fit': 'fill'},
], ids=['along_the_tape', 'filled'])
def test_lines_wrap_on_endless_labels_of_a_fixed_length(tmp_path, settings):
    # The label does not grow with them
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62', label_length='40', text=lines(SENTENCE, size=50), **settings)
    with client.application.test_request_context():
        def generated(**more):
            label = create_label_from_request(dict(data, **more))
            label.generate()
            return label
        label = generated(text_wrap='1')
        assert label.warnings == [] and len(drawn(label)) > 1
        assert len(drawn(generated())) == 1

def test_a_word_longer_than_a_line(tmp_path):
    # As set it is broken where it reaches the edge, shrunk it is kept whole
    # and made smaller instead
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62', text=lines(WORD + ' und mehr', size=70), text_wrap='1')
    with client.application.test_request_context():
        def lines_of(text_fit):
            label = create_label_from_request(dict(data, text_fit=text_fit))
            label.generate()
            assert label.warnings == []
            return drawn(label)
        as_set = lines_of('none')
        assert WORD not in as_set and ''.join(as_set).replace(' ', '') == WORD + 'undmehr'
        assert lines_of('shrink') == [WORD, 'und mehr']


def test_a_code_that_takes_the_text_takes_it_as_written(tmp_path, monkeypatch):
    # Not as it is wrapped
    codes = []
    original = label_module._qr_image
    monkeypatch.setattr(label_module, '_qr_image', lambda data, *args: codes.append(data) or original(data, *args))
    client = make_client(tmp_path)
    response = preview(client, label_size='62x29', print_type='qrcode_text', code_text='', image_position='left',
                       text=lines(SENTENCE, 'Pantry'), text_wrap='1', text_fit='shrink')
    assert response.status_code == 200 and set(codes) == {SENTENCE + '\nPantry'}


def test_lines_of_a_request_are_not_taken_for_wrapped_ones(tmp_path):
    # Only wrapping marks the lines a to-do line wrapped into
    client = make_client(tmp_path)
    plain = lines('Call the plumber', align='left')
    marked = json.dumps([dict(json.loads(plain)[0], todo_continued=True)])
    assert pixels(preview(client, text=plain).data) == pixels(preview(client, text=marked).data)


@pytest.mark.parametrize('value, wraps', [('1', True), ('true', True), ('0', False), ('', False), (None, False)])
def test_text_wrap_is_a_flag(tmp_path, value, wraps):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62x29', text=lines(SENTENCE), text_fit='none')
    if value is not None:
        data['text_wrap'] = value
    with client.application.test_request_context():
        label = create_label_from_request(data)
        label.generate()
        assert (len(drawn(label)) > 1) == wraps


def test_text_wrap_that_is_no_flag_is_refused(tmp_path):
    client = make_client(tmp_path)
    response = preview(client, text=lines(SENTENCE), text_wrap='often')
    assert response.status_code == 400 and response.get_json()['message'] == 'text_wrap must be 0 or 1'

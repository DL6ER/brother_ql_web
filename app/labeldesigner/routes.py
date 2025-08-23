import logging
import os

from flask import current_app, render_template, request, make_response

from brother_ql.devicedependent import label_type_specs, label_sizes, two_color_support
from brother_ql.devicedependent import ENDLESS_LABEL, DIE_CUT_LABEL, ROUND_DIE_CUT_LABEL

from . import bp
from app.utils import convert_image_to_bw, convert_image_to_grayscale, convert_image_to_red_and_black, pdffile_to_image, imgfile_to_image, image_to_png_bytes
from app import FONTS

from .label import SimpleLabel, LabelContent, LabelOrientation, LabelType
from .printer import PrinterQueue

LINE_SPACINGS = (100, 150, 200, 250, 300)

# Don't change as brother_ql is using this DPI value
DEFAULT_DPI = 300

LABEL_SIZES = [(
    name,
    label_type_specs[name]['name'],
    (label_type_specs[name]['kind'] in (
        ROUND_DIE_CUT_LABEL,))  # True if round label
) for name in label_sizes]


@bp.route('/')
def index():
    RED_SUPPORT = current_app.config['PRINTER_MODEL'] in two_color_support
    return render_template('labeldesigner.html',
                           font_family_names=FONTS.fontlist(),
                           label_sizes=LABEL_SIZES,
                           red_support=RED_SUPPORT,
                           default_label_size=current_app.config['LABEL_DEFAULT_SIZE'],
                           default_font_size=current_app.config['LABEL_DEFAULT_FONT_SIZE'],
                           default_orientation=current_app.config['LABEL_DEFAULT_ORIENTATION'],
                           default_qr_size=current_app.config['LABEL_DEFAULT_QR_SIZE'],
                           default_image_mode=current_app.config['IMAGE_DEFAULT_MODE'],
                           default_bw_threshold=current_app.config['IMAGE_DEFAULT_BW_THRESHOLD'],
                           default_font_family=current_app.config['LABEL_DEFAULT_FONT_FAMILY'],
                           line_spacings=LINE_SPACINGS,
                           default_line_spacing=current_app.config['LABEL_DEFAULT_LINE_SPACING'],
                           default_dpi=DEFAULT_DPI,
                           default_margin_top=current_app.config['LABEL_DEFAULT_MARGIN_TOP'],
                           default_margin_bottom=current_app.config['LABEL_DEFAULT_MARGIN_BOTTOM'],
                           default_margin_left=current_app.config['LABEL_DEFAULT_MARGIN_LEFT'],
                           default_margin_right=current_app.config['LABEL_DEFAULT_MARGIN_RIGHT'],
                           printer_path=current_app.config['PRINTER_PRINTER'],
                           printer_model=current_app.config['PRINTER_MODEL']
                           )


@bp.route('/api/font/styles', methods=['POST', 'GET'])
def get_font_styles():
    font = request.values.get(
        'font', current_app.config['LABEL_DEFAULT_FONT_FAMILY'])
    return FONTS.fonts[font]


@bp.route('/api/preview', methods=['POST', 'GET'])
def get_preview_from_image():
    # Set log level if provided
    log_level = request.values.get('log_level')
    if log_level:
        import logging
        level = getattr(logging, log_level.upper(), None)
        if isinstance(level, int):
            current_app.logger.setLevel(level)
    label = create_label_from_request(request)
    im = label.generate(rotate=True)

    return_format = request.values.get('return_format', 'png')

    if return_format == 'base64':
        import base64
        response = make_response(base64.b64encode(image_to_png_bytes(im)))
        response.headers.set('Content-type', 'text/plain')
        return response
    else:
        response = make_response(image_to_png_bytes(im))
        response.headers.set('Content-type', 'image/png')
        return response


@bp.route('/api/print', methods=['POST', 'GET'])
def print_text():
    """
    API to print a label

    returns: JSON

    Ideas for additional URL parameters:
    - alignment
    """

    return_dict = {'success': False}

    try:
        # Set log level if provided
        log_level = request.values.get('log_level')
        if log_level:
            import logging
            level = getattr(logging, log_level.upper(), None)
            if isinstance(level, int):
                current_app.logger.setLevel(level)
        printer = create_printer_from_request(request)
        label = create_label_from_request(request)
        print_count = int(request.values.get('print_count', 1))
        cut_once = int(request.values.get('cut_once', 0)) == 1
    except Exception as e:
        return_dict['message'] = str(e)
        current_app.logger.error('Exception happened: %s', e)
        return return_dict

    printer.add_label_to_queue(label, print_count, cut_once)

    try:
        printer.process_queue()
    except Exception as e:
        return_dict['message'] = str(e)
        current_app.logger.error('Exception happened: %s', e)
        return return_dict

    return_dict['success'] = True
    return return_dict


def create_printer_from_request(request):
    d = request.values
    context = {
        'label_size': d.get('label_size', '62')
    }

    return PrinterQueue(
        model = current_app.config['PRINTER_MODEL'],
        device_specifier = current_app.config['PRINTER_PRINTER'],
        label_size = context['label_size']
    )

# Parse text form data from frontend
def parse_text_form(form):
    parsed = []
    for key, value in form.items():
        if key.startswith('text['):
            # Get index of first ']'
            end = key.index(']')
            # Extract index this line corresponds to
            idx = int(key[5:end])
            # Get index of property's ']'
            second_end=key[end+1:].index(']')
            # Extract property name
            prop = key[end+2:end+1+second_end]

            # Create entry if it doesn't exist
            if idx > len(parsed)-1:
                parsed.append({})

            # Assign value to the corresponding property
            parsed[idx][prop] = value

    return parsed

def create_label_from_request(request):
    d=request.values
    context={
        'label_size': d.get('label_size', '62'),
        'print_type': d.get('print_type', 'text'),
        'label_orientation': d.get('orientation', 'standard'),
        'kind': label_type_specs[d.get('label_size', "62")]['kind'],
        'margin_top': float(d.get('margin_top', 24))/100.,
        'margin_bottom': float(d.get('margin_bottom', 45))/100.,
        'margin_left': float(d.get('margin_left', 35))/100.,
        'margin_right': float(d.get('margin_right', 35))/100.,
        'text': parse_text_form(request.form),
        'qrcode_size': int(d.get('qrcode_size', 10)),
        'qrcode_correction': d.get('qrcode_correction', 'L'),
        'image_mode': d.get('image_mode', "grayscale"),
        'image_bw_threshold': int(d.get('image_bw_threshold', 70)),
        'image_fit': int(d.get('image_fit', 1)) > 0,
        'print_color': d.get('print_color', 'black'),
    }

    def get_label_dimensions(label_size):
        try:
            ls = label_type_specs[context['label_size']]
        except KeyError:
            raise LookupError("Unknown label_size")
        return ls['dots_printable']

    def get_font_path(font_family_name, font_style_name):
        try:
            if font_family_name is None or font_style_name is None:
                font_family_name = current_app.config['LABEL_DEFAULT_FONT_FAMILY']
                font_style_name = current_app.config['LABEL_DEFAULT_FONT_STYLE']
            if font_family_name not in FONTS.fonts:
                raise LookupError("Unknown font family: %s" % font_family_name)
            if font_style_name not in FONTS.fonts[font_family_name]:
                font_style_name = current_app.config['LABEL_DEFAULT_FONT_STYLE']
            if font_style_name not in FONTS.fonts[font_family_name]:
                raise LookupError("Unknown font style: %s" % font_style_name)
            font_path = FONTS.fonts[font_family_name][font_style_name]
        except KeyError:
            raise LookupError("Couln't find the font & style")
        return font_path

    def get_uploaded_image(image):
        try:
            name, ext = os.path.splitext(image.filename)
            if ext.lower() in ('.png', '.jpg', '.jpeg'):
                image = imgfile_to_image(image)
                if context['image_mode'] == 'grayscale':
                    return convert_image_to_grayscale(image)
                elif context['image_mode'] == 'red_and_black':
                    return convert_image_to_red_and_black(image)
                elif context['image_mode'] == 'colored':
                    return image
                else:
                    return convert_image_to_bw(image, context['image_bw_threshold'])
            elif ext.lower() in ('.pdf'):
                image = pdffile_to_image(image, DEFAULT_DPI)
                if context['image_mode'] == 'grayscale':
                    return convert_image_to_grayscale(image)
                else:
                    return convert_image_to_bw(image, context['image_bw_threshold'])
            else:
                return None
        except AttributeError:
            return None

    if context['print_type'] == 'text':
        label_content = LabelContent.TEXT_ONLY
    elif context['print_type'] == 'qrcode':
        label_content = LabelContent.QRCODE_ONLY
    elif context['print_type'] == 'qrcode_text':
        label_content = LabelContent.TEXT_QRCODE
    elif context['image_mode'] == 'grayscale':
        label_content = LabelContent.IMAGE_GRAYSCALE
    elif context['image_mode'] == 'red_black':
        label_content = LabelContent.IMAGE_RED_BLACK
    elif context['image_mode'] == 'colored':
        label_content = LabelContent.IMAGE_COLORED
    else:
        label_content = LabelContent.IMAGE_BW

    if context['label_orientation'] == 'rotated':
        label_orientation = LabelOrientation.ROTATED
    else:
        label_orientation = LabelOrientation.STANDARD

    if context['kind'] == ENDLESS_LABEL:
        label_type = LabelType.ENDLESS_LABEL
    elif context['kind'] == DIE_CUT_LABEL:
        label_type = LabelType.DIE_CUT_LABEL
    else:
        label_type = LabelType.ROUND_DIE_CUT_LABEL

    width, height = get_label_dimensions(context['label_size'])
    if height > width:
        width, height = height, width
    if label_orientation == LabelOrientation.ROTATED:
        height, width = width, height

    # For each line in text, we determine and add the font path
    for line in context['text']:
        line['font_path'] = get_font_path(line['font_family'], line['font_style'])

    return SimpleLabel(
        width=width,
        height=height,
        label_content=label_content,
        label_orientation=label_orientation,
        label_type=label_type,
        label_margin=(
            int(context['margin_left']),
            int(context['margin_right']),
            int(context['margin_top']),
            int(context['margin_bottom'])
        ),
        fore_color=
            (255, 0, 0) if 'red' in context['label_size'] and context['print_color'] == 'red'
            else (0, 0, 0),
        text=context['text'],
        qr_size=context['qrcode_size'],
        qr_correction=context['qrcode_correction'],
        image=get_uploaded_image(request.files.get('image', None)),
        image_fit=context['image_fit'],
    )

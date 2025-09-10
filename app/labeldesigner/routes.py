import os
import logging
from typing import Any
import barcode
from . import bp
from app import FONTS
from PIL import Image
from werkzeug.datastructures import FileStorage
from .printer import PrinterQueue, get_ptr_status
from brother_ql.labels import ALL_LABELS, FormFactor
from .label import SimpleLabel, LabelContent, LabelOrientation, LabelType
from flask import Request, current_app, json, jsonify, render_template, request, make_response
from app.utils import (
    convert_image_to_bw, convert_image_to_grayscale, convert_image_to_red_and_black,
    pdffile_to_image, imgfile_to_image, image_to_png_bytes
)

LINE_SPACINGS = (100, 150, 200, 250, 300)
DEFAULT_DPI = 300
HIGH_RES_DPI = 600

@bp.errorhandler(ValueError)
def handle_value_error(e):
    return jsonify({"error": str(e)}), 400

@bp.route('/')
def index():
    label_sizes = [
        (label.identifier, label.name, label.form_factor == FormFactor.ROUND_DIE_CUT, label.tape_size)
        for label in ALL_LABELS
    ]
    return render_template(
        'labeldesigner.html',
        fonts=FONTS.fontlist(),
        label_sizes=label_sizes,
        default_label_size=current_app.config['LABEL_DEFAULT_SIZE'],
        default_font_size=current_app.config['LABEL_DEFAULT_FONT_SIZE'],
        default_orientation=current_app.config['LABEL_DEFAULT_ORIENTATION'],
        default_qr_size=current_app.config['LABEL_DEFAULT_QR_SIZE'],
        default_image_mode=current_app.config['IMAGE_DEFAULT_MODE'],
        default_bw_threshold=current_app.config['IMAGE_DEFAULT_BW_THRESHOLD'],
        default_font_family=FONTS.get_default_font()[0],
        default_font_style=FONTS.get_default_font()[1],
        line_spacings=LINE_SPACINGS,
        default_line_spacing=current_app.config['LABEL_DEFAULT_LINE_SPACING'],
        default_dpi=DEFAULT_DPI,
        default_margin_top=current_app.config['LABEL_DEFAULT_MARGIN_TOP'],
        default_margin_bottom=current_app.config['LABEL_DEFAULT_MARGIN_BOTTOM'],
        default_margin_left=current_app.config['LABEL_DEFAULT_MARGIN_LEFT'],
        default_margin_right=current_app.config['LABEL_DEFAULT_MARGIN_RIGHT']
    )


@bp.route('/api/barcodes', methods=['GET'])
def get_barcodes():
    barcodes = [code.upper() for code in barcode.PROVIDED_BARCODES]
    barcodes.insert(0, 'QR')  # Add QR at the top
    return {'barcodes': barcodes}


@bp.route('/api/preview', methods=['POST'])
def preview_from_image():
    log_level = request.values.get('log_level')
    if log_level:
        level = getattr(logging, log_level.upper(), None)
        if isinstance(level, int):
            current_app.logger.setLevel(level)
    try:
        label = create_label_from_request(request)
        im = label.generate(rotate=True)
    except Exception as e:
        current_app.logger.exception(e)
        error = 413 if "too long" in str(e) else 400
        return make_response(jsonify({'message': str(e)}), error)

    return_format = request.values.get('return_format', 'png')
    response_data = image_to_png_bytes(im)
    if return_format == 'base64':
        import base64
        response_data = base64.b64encode(response_data)
        content_type = 'text/plain'
    else:
        content_type = 'image/png'
    response = make_response(response_data)
    response.headers.set('Content-type', content_type)
    return response


@bp.route('/api/printer_status', methods=['GET'])
def get_printer_status():
    return get_ptr_status(current_app.config)


@bp.route('/api/print', methods=['POST', 'GET'])
def print_label():
    """
    API to print a label
    returns: JSON
    """
    return_dict = {'success': False}
    try:
        log_level = request.values.get('log_level')
        if log_level:
            level = getattr(logging, log_level.upper(), None)
            if isinstance(level, int):
                current_app.logger.setLevel(level)
        printer = create_printer_from_request(request)
        print_count = int(request.values.get('print_count', 1))
        if print_count < 1:
            raise ValueError("print_count must be greater than 0")
        cut_once = int(request.values.get('cut_once', 0)) == 1
        high_res = int(request.values.get('high_res', 0)) != 0
    except Exception as e:
        return_dict['message'] = str(e)
        current_app.logger.exception(e)
        return make_response(jsonify(return_dict), 400)

    try:
        for i in range(print_count):
            label = create_label_from_request(request, i)
            # Cut only if we
            # - always cut, or
            # - we cut only once and this is the last label to be generated
            cut = not cut_once or (cut_once and i == print_count - 1)
            printer.add_label_to_queue(label, cut, high_res)
        status = printer.process_queue(current_app.config['PRINTER_OFFLINE'])
    except Exception as e:
        return_dict['message'] = str(e)
        current_app.logger.exception(e)
        return make_response(jsonify(return_dict), 400)

    return_dict['success'] = status
    return return_dict


def create_printer_from_request(request: Request):
    label_size = request.values.get('label_size', '62')
    return PrinterQueue(
        model=current_app.config['PRINTER_MODEL'],
        device_specifier=current_app.config['PRINTER_PRINTER'],
        label_size=label_size
    )


def parse_text_form(input: str) -> Any:
    """Parse text form data from frontend."""
    if not input:
        return []
    return json.loads(input)


def create_label_from_request(request: Request, counter: int = 0):
    d = request.values
    label_size = d.get('label_size', "62")
    kind = next((label.form_factor for label in ALL_LABELS if label.identifier == label_size), None)
    if kind is None:
        raise LookupError("Unknown label_size")
    context = {
        'label_size': label_size,
        'print_type': d.get('print_type', 'text'),
        'label_orientation': d.get('orientation', 'standard'),
        'kind': kind,
        'margin_top': int(d.get('margin_top', 12)),
        'margin_bottom': int(d.get('margin_bottom', 12)),
        'margin_left': int(d.get('margin_left', 20)),
        'margin_right': int(d.get('margin_right', 20)),
        'border_thickness': int(d.get('border_thickness', 1)),
        'border_roundness': int(d.get('border_roundness', 0)),
        'border_distanceX': int(d.get('border_distance_x', 0)),
        'border_distanceY': int(d.get('border_distance_y', 0)),
        'border_color': d.get('border_color', 'black'),
        'text': parse_text_form(d.get('text', '')),
        'barcode_type': d.get('barcode_type', 'QR'),
        'qrcode_size': int(d.get('qrcode_size', 10)),
        'qrcode_correction': d.get('qrcode_correction', 'L'),
        'image_mode': d.get('image_mode', "grayscale"),
        'image_bw_threshold': int(d.get('image_bw_threshold', 70)),
        'image_fit': int(d.get('image_fit', 1)) > 0,
        'print_color': d.get('print_color', 'black'),
        'timestamp': int(d.get('timestamp', 0)),
        'high_res': int(d.get('high_res', 0)) != 0
    }

    def get_label_dimensions(label_size: str, high_res: bool = False):
        dimensions = next((label.dots_printable for label in ALL_LABELS if label.identifier == label_size), None)
        if dimensions is None:
            raise LookupError("Unknown label_size")
        if high_res:
            return [2 * dimensions[0], 2 * dimensions[1]]
        return dimensions

    def get_uploaded_image(image: FileStorage) -> Image.Image:
        name, ext = os.path.splitext(image.filename)
        ext = ext.lower()

        # Try to open as PDF
        if ext == '.pdf':
            image = pdffile_to_image(image, DEFAULT_DPI)
            if context['image_mode'] == 'grayscale':
                return convert_image_to_grayscale(image)
            else:
                return convert_image_to_bw(image, context['image_bw_threshold'])

        # Try to read with PIL
        exts = Image.registered_extensions()
        supported_extensions = {ex for ex, f in exts.items() if f in Image.OPEN}
        current_app.logger.info(f"Supported image extensions: {supported_extensions}")
        if ext in supported_extensions:
            image = imgfile_to_image(image)
            if context['image_mode'] == 'grayscale':
                return convert_image_to_grayscale(image)
            elif context['image_mode'] == 'red_and_black':
                return convert_image_to_red_and_black(image)
            elif context['image_mode'] == 'colored':
                return image
            else:
                return convert_image_to_bw(image, context['image_bw_threshold'])

        raise ValueError("Unsupported file type")

    print_type = context['print_type']
    image_mode = context['image_mode']
    if print_type == 'text':
        label_content = LabelContent.TEXT_ONLY
    elif print_type == 'qrcode':
        label_content = LabelContent.QRCODE_ONLY
    elif print_type == 'qrcode_text':
        label_content = LabelContent.TEXT_QRCODE
    elif image_mode == 'grayscale':
        label_content = LabelContent.IMAGE_GRAYSCALE
    elif image_mode == 'red_black':
        label_content = LabelContent.IMAGE_RED_BLACK
    elif image_mode == 'colored':
        label_content = LabelContent.IMAGE_COLORED
    else:
        label_content = LabelContent.IMAGE_BW

    label_orientation = LabelOrientation.ROTATED if context['label_orientation'] == 'rotated' else LabelOrientation.STANDARD
    if context['kind'] == FormFactor.ENDLESS:
        label_type = LabelType.ENDLESS_LABEL
    elif context['kind'] == FormFactor.DIE_CUT:
        label_type = LabelType.DIE_CUT_LABEL
    else:
        label_type = LabelType.ROUND_DIE_CUT_LABEL

    width, height = get_label_dimensions(context['label_size'], context['high_res'])
    if height > width:
        width, height = height, width
    if label_orientation == LabelOrientation.ROTATED:
        height, width = width, height

    # For each line in text, we determine and add the font path
    for line in context['text']:
        if 'size' not in line or not str(line['size']).isdigit():
            raise ValueError("Font size is required")
        if int(line['size']) < 1:
            raise ValueError("Font size must be at least 1")
        line['path'] = FONTS.get_path(line.get('font', ''))
        if len(line.get('text', '')) > 10_000:
            raise ValueError("Text is too long")

    fore_color = (255, 0, 0) if context['print_color'] == 'red' else (0, 0, 0)
    border_color = (255, 0, 0) if context['border_color'] == 'red' else (0, 0, 0)

    uploaded = request.files.get('image', None)
    image = get_uploaded_image(uploaded) if uploaded is not None else None

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
        fore_color=fore_color,
        text=context['text'],
        barcode_type=context['barcode_type'],
        qr_size=context['qrcode_size'],
        qr_correction=context['qrcode_correction'],
        image=image,
        image_fit=context['image_fit'],
        border_thickness=context['border_thickness'],
        border_roundness=context['border_roundness'],
        border_distance=(context['border_distanceX'], context['border_distanceY']),
        border_color=border_color,
        timestamp=context['timestamp'],
        counter=counter
    )

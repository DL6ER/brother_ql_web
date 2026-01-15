import os
import logging
import barcode
from . import bp
from app import FONTS
from PIL import Image
from werkzeug.datastructures import FileStorage
from .printer import PrinterQueue, get_ptr_status
from brother_ql.labels import ALL_LABELS, FormFactor
from .label import SimpleLabel, LabelContent, LabelOrientation, LabelType
from flask import Request, current_app, json, jsonify, render_template, request, make_response
from werkzeug.utils import secure_filename
from app.utils import (
    convert_image_to_bw, convert_image_to_grayscale, convert_image_to_red_and_black, fill_first_line_fields,
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


# --- Label repository utilities and API -------------------------------------------------
def _get_repo_dir():
    repo = current_app.config.get('LABEL_REPOSITORY_DIR')
    if not repo:
        # default to a folder inside the app root
        repo = os.path.join(current_app.root_path, 'labels')
    os.makedirs(repo, exist_ok=True)
    return repo


@bp.route('/api/repository/list', methods=['GET'])
def repo_list():
    repo = _get_repo_dir()
    files = []
    for name in sorted(os.listdir(repo)):
        if not name.lower().endswith('.json'):
            continue
        path = os.path.join(repo, name)
        stat = os.stat(path)
        entry = {'name': name, 'mtime': int(stat.st_mtime), 'size': stat.st_size}
        # Read label metadata
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        # Parse label size (support both snake_case and legacy camelCase)
        label_size = data.get('label_size') or data.get('labelSize')
        if label_size:
            label_size_human = next(
                (label.name for label in ALL_LABELS if label.identifier == label_size), None
            )
            if label_size_human:
                label_size = f"{label_size_human}"
            entry['label_size'] = str(label_size)
        else:
            entry['label_size'] = None
        files.append(entry)
    return {'files': files}


@bp.route('/api/repository/save', methods=['POST'])
def repo_save():
    # Expect JSON payload
    data = request.get_json(force=True, silent=True)
    name = None
    if data is not None:
        name = data.get('name') or request.values.get('name') or None
    if data is None:
        return make_response(jsonify({'success': False, 'message': 'No JSON payload provided'}), 400)
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name provided'}), 400)
    filename = secure_filename(name)
    if not filename.lower().endswith('.json'):
        filename = filename + '.json'
    repo = _get_repo_dir()
    path = os.path.join(repo, filename)
    try:
        # Extract text properties
        text = data.get('fontSettingsPerLine', [])
        if isinstance(text, str):
            data['text'] = json.loads(text)

        # Accept JSON image payloads that include raw base64 image data in
        # fields `image_data` (base64 string), `image_mime` and optional
        # `image_name` so clients can submit images using pure JSON payloads.
        try:
            img_b64 = data.get('image_data')
            if isinstance(img_b64, str) and len(img_b64) > 0:
                img_mime = data.get('image_mime', 'image/png')
                ext = {
                    'image/png': '.png',
                    'image/jpeg': '.jpg',
                    'image/jpg': '.jpg',
                    'image/gif': '.gif',
                    'application/pdf': '.pdf'
                }.get(img_mime, '.png')
                base = os.path.splitext(filename)[0]
                image_name = secure_filename(data.get('image_name') or (base + '_image' + ext))
                image_path = os.path.join(repo, image_name)
                import base64 as _b64
                with open(image_path, 'wb') as imgfh:
                    imgfh.write(_b64.b64decode(img_b64))
                data['image'] = image_name
        except Exception:
            current_app.logger.exception('Failed to store base64 image from JSON')

        # Remove raw image data from JSON before saving
        if 'image_data' in data:
            del data['image_data']

        # Remove redundant information about zeroth line font settings
        for key in ['font_size', 'font_inverted', 'font', 'font_align', 'font_checkbox', 'font_color', 'line_spacing', 'fontSettingsPerLine']:
            if key in data:
                del data[key]

        # Finally, save the JSON file
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to save file'}), 500)
    return {'success': True, 'name': filename}


@bp.route('/api/repository/load', methods=['GET'])
def repo_load():
    name = request.values.get('name')
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name specified'}), 400)
    filename = secure_filename(name)
    repo = _get_repo_dir()
    path = os.path.join(repo, filename)
    if not os.path.exists(path):
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        text = data.get('text', [])
        data['text'] = json.dumps(text)
        data = fill_first_line_fields(text, data)
        # If the saved JSON references an image file, include the image as
        # base64 in the response so the frontend can populate the
        # Dropzone control when loading a template.
        try:
            image_ref = data.get('image')
            if isinstance(image_ref, str) and len(image_ref) > 0:
                repo = _get_repo_dir()
                image_path = os.path.join(repo, secure_filename(image_ref))
                if os.path.exists(image_path):
                    import base64 as _b64
                    with open(image_path, 'rb') as imgfh:
                        b = imgfh.read()
                    # Guess mime from extension
                    _, ext = os.path.splitext(image_path)
                    ext = ext.lower()
                    mime = {
                        '.png': 'image/png',
                        '.jpg': 'image/jpeg',
                        '.jpeg': 'image/jpeg',
                        '.gif': 'image/gif',
                        '.pdf': 'application/pdf'
                    }.get(ext, 'application/octet-stream')
                    data['image_name'] = image_ref
                    data['image_mime'] = mime
                    data['image_data'] = _b64.b64encode(b).decode('ascii')
        except Exception:
            current_app.logger.exception('Failed to include repository image in load response')
        return data
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to load file'}), 500)


@bp.route('/api/repository/delete', methods=['POST'])
def repo_delete():
    jdata = request.get_json(force=True, silent=True) or {}
    name = jdata.get('name') or request.values.get('name')
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name specified'}), 400)
    filename = secure_filename(name)
    repo = _get_repo_dir()
    path = os.path.join(repo, filename)
    if not os.path.exists(path):
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    try:
        os.remove(path)
        # Also remove any associated image files stored alongside the JSON
        try:
            base = os.path.splitext(filename)[0]
            for f in os.listdir(repo):
                if f.startswith(base + '_image'):
                    try:
                        os.remove(os.path.join(repo, f))
                    except Exception:
                        current_app.logger.exception(f'Failed to remove associated image {f}')
        except Exception:
            current_app.logger.exception('Failed to cleanup associated images')
        return {'success': True}
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to delete file'}), 500)


def _load_repo_json(name: str):
    filename = secure_filename(name)
    repo = _get_repo_dir()
    path = os.path.join(repo, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(name)
    with open(path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
        data['text'] = json.dumps(data.get('text', []))
        return data


@bp.route('/api/repository/preview', methods=['GET', 'POST'])
def repo_preview():
    name = request.values.get('name')
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name specified'}), 400)
    try:
        data = _load_repo_json(name)
    except FileNotFoundError:
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to preview file'}), 500)

    # allow override printer via query param
    if request.values.get('printer'):
        data['printer'] = request.values.get('printer')

    try:
        label = create_label_from_request(data)
        im = label.generate(rotate=True)
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'message': str(e)}), 400)

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


@bp.route('/api/repository/print', methods=['POST'])
def repo_print():
    # Print a saved repository template by name. The server will load the JSON
    # and perform the same printing logic as the /api/print endpoint.
    jdata = request.get_json(force=True, silent=True) or {}
    name = jdata.get('name') or request.values.get('name')
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name specified'}), 400)
    try:
        data = _load_repo_json(name)
    except FileNotFoundError:
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to load file'}), 500)

    # Allow overriding printer or other request-like parameters via form/query
    if request.values.get('printer'):
        data['printer'] = request.values.get('printer')

    # Prepare printer queue using requested or default device/model and label_size from data
    try:
        device = request.values.get('printer') or current_app.config['PRINTER_PRINTER']
        model = request.values.get('model') or current_app.config['PRINTER_MODEL']
        label_size = data.get('label_size') or current_app.config['LABEL_DEFAULT_SIZE']
        printer = PrinterQueue(model=model, device_specifier=device, label_size=label_size)

        # Determine printing options (print_count, cut_once, high_res)
        print_count = int(request.values.get('print_count') or data.get('print_count') or 1)
        if print_count < 1:
            raise ValueError("print_count must be greater than 0")
        cut_once = int(request.values.get('cut_once') or data.get('cut_once') or 0) == 1
        high_res = int(request.values.get('high_res') or data.get('high_res') or 0) != 0
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': str(e)}), 400)

    status = ""
    try:
        for i in range(print_count):
            label = create_label_from_request(data, {}, i)
            cut = not cut_once or (cut_once and i == print_count - 1)
            printer.add_label_to_queue(label, cut, high_res)
        status = printer.process_queue()
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': str(e)}), 400)

    result = {'success': len(status) == 0}
    if len(status) > 0:
        result['message'] = status
        return make_response(jsonify(result), 400)
    return result


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
        values = request.values.to_dict(flat=True)
        files = request.files.to_dict(flat=True)
        label = create_label_from_request(values, files)
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

    status = ""
    try:
        for i in range(print_count):
            values = request.values.to_dict(flat=True)
            files = request.files.to_dict(flat=True)
            label = create_label_from_request(values, files, i)
            # Cut only if we
            # - always cut, or
            # - we cut only once and this is the last label to be generated
            cut = not cut_once or (cut_once and i == print_count - 1)
            printer.add_label_to_queue(label, cut, high_res)
        status = printer.process_queue()
    except Exception as e:
        return_dict['message'] = str(e)
        current_app.logger.exception(e)
        return make_response(jsonify(return_dict), 400)

    return_dict['success'] = len(status) == 0
    if len(status) > 0:
        return_dict['message'] = status
        return make_response(jsonify(return_dict), 400)
    return return_dict


def create_printer_from_request(request: Request):
    label_size = request.values.get('label_size', '62')
    # Allow overriding the device specifier via the request (frontend selection)
    device = request.values.get('printer') or current_app.config['PRINTER_PRINTER']
    # Allow overriding model via request if provided
    model = request.values.get('model') or current_app.config['PRINTER_MODEL']
    return PrinterQueue(
        model=model,
        device_specifier=device,
        label_size=label_size
    )


def create_label_from_request(d: dict = {}, files: dict = {}, counter: int = 0):
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
        'text': json.loads(d.get('text', '[]')),
        'barcode_type': d.get('barcode_type', 'QR'),
        'qrcode_size': int(d.get('qrcode_size', 10)),
        'qrcode_correction': d.get('qrcode_correction', 'L'),
        'image_mode': d.get('image_mode', "grayscale"),
        'image_bw_threshold': int(d.get('image_bw_threshold', 70)),
        'image_fit': int(d.get('image_fit', 1)) > 0,
        'image_scaling_factor': float(d.get('image_scaling_factor', 100.0)),
        'image_rotation': int(d.get('image_rotation', 0)),
        'print_color': d.get('print_color', 'black'),
        'timestamp': int(d.get('timestamp', 0)),
        'high_res': int(d.get('high_res', 0)) != 0,
        'code_text': d.get('code_text', '').strip(),
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
            current_app.logger.error(line)
            raise ValueError("Font size is required")
        if int(line['size']) < 1:
            raise ValueError("Font size must be at least 1")
        line['path'] = FONTS.get_path(line.get('font', ''))
        if len(line.get('text', '')) > 10_000:
            raise ValueError("Text is too long")

    fore_color = (255, 0, 0) if context['print_color'] == 'red' else (0, 0, 0)
    border_color = (255, 0, 0) if context['border_color'] == 'red' else (0, 0, 0)

    uploaded = files.get('image', None)
    image = None
    if uploaded is not None:
        image = get_uploaded_image(uploaded)
    else:
        # If no uploaded FileStorage was provided but the data references an
        # image filename (stored in repository), attempt to load it from the
        # repository directory and convert it consistent with uploaded images.
        image_ref = d.get('image')
        if isinstance(image_ref, str) and len(image_ref) > 0:
            try:
                repo = _get_repo_dir()
                image_path = os.path.join(repo, secure_filename(image_ref))
                if os.path.exists(image_path):
                    # Open image file with PIL
                    with open(image_path, 'rb') as fh:
                        pil_img = imgfile_to_image(fh)
                    # Apply same conversions as get_uploaded_image would
                    if context['image_mode'] == 'grayscale':
                        image = convert_image_to_grayscale(pil_img)
                    elif context['image_mode'] == 'red_and_black':
                        image = convert_image_to_red_and_black(pil_img)
                    elif context['image_mode'] == 'colored':
                        image = pil_img
                    else:
                        image = convert_image_to_bw(pil_img, context['image_bw_threshold'])
            except Exception:
                current_app.logger.exception('Failed to load repository image')

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
        image_scaling_factor=context['image_scaling_factor'],
        image_rotation=context['image_rotation'],
        border_thickness=context['border_thickness'],
        border_roundness=context['border_roundness'],
        border_distance=(context['border_distanceX'], context['border_distanceY']),
        border_color=border_color,
        timestamp=context['timestamp'],
        counter=counter,
        code_text=context['code_text']
    )

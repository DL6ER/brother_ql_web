import os
import re
import hmac
import math
import uuid
import time
import base64
import secrets
import logging
import barcode
import threading
from collections import OrderedDict
from io import BytesIO
from typing import BinaryIO
from urllib.parse import urlparse
from . import bp
from app import get_fonts
from app.fonts import FontNotFound
from PIL import Image
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import HTTPException
from .printer import MODELS_WITHOUT_600_DPI, PrinterQueue, get_ptr_status, resolution_problem
from brother_ql.labels import ALL_LABELS, FormFactor
from .label import MAX_TEXT_LENGTH, SimpleLabel, LabelContent, LabelOrientation, LabelType, check_image_size
from .label_names import label_name, label_printers, label_size_problem
from flask import Request, current_app, json, jsonify, redirect, render_template, request, make_response, url_for
from werkzeug.security import safe_join
from werkzeug.utils import secure_filename
from app.utils import (
    convert_image_to_bw, convert_image_to_grayscale, convert_image_to_red_and_black, fill_first_line_fields,
    pdffile_to_image, imgfile_to_image, image_to_png_bytes, pdf_page_count, selected_pdf_pages, PageSelectionError
)

LINE_SPACINGS = (100, 150, 200, 250, 300)
DEFAULT_DPI = 300
HIGH_RES_DPI = 600

# Image types the repository stores next to the label JSON files
REPO_IMAGE_EXTENSIONS = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/gif': '.gif',
    'application/pdf': '.pdf'
}
REPO_IMAGE_MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.pdf': 'application/pdf'
}

# Printers a request may select besides the configured one. The backend opens
# and writes to whatever it is given, so arbitrary paths must not get through.
PRINTER_DEVICE_PATTERNS = (
    re.compile(r'(file://)?/dev/usb/lp\d+'),
    re.compile(r'usb://0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'),
    re.compile(r'tcp://[A-Za-z0-9.-]+(:9100)?'),
)

@bp.errorhandler(ValueError)
def handle_value_error(e):
    return jsonify({"error": str(e)}), 400


@bp.before_request
def reject_cross_origin_requests():
    """Refuse state-changing requests that another website sends through the
    browser of a visitor (cross-site request forgery).

    Browsers add an Origin header to cross-origin form posts and fetches. Such
    requests are only accepted if they carry the X-Requested-With header (set
    by the label designer), which a foreign page cannot add without a CORS
    preflight that this server never grants. Clients without an Origin header
    (curl, scripts, webhooks) are not affected."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    # Android sends what is shared with the installed app from its own share
    # menu, possibly with "Origin: null". It is only kept for the page.
    if request.endpoint == 'labeldesigner.share':
        return None
    origin = request.headers.get('Origin')
    if not origin or request.headers.get('X-Requested-With'):
        return None
    if urlparse(origin).netloc == request.host:
        return None
    current_app.logger.warning(f"Rejected cross-origin {request.method} {request.path} from {origin}")
    return make_response(jsonify({'success': False, 'message': 'Cross-origin request rejected'}), 403)


def open_image(file: FileStorage | BinaryIO, filename: str, dpi: int) -> Image.Image:
    """Open an uploaded or stored image, of a PDF the first page."""
    return open_images(file, filename, dpi, first_only=True)[0][0]


def _is_pdf(file: FileStorage | BinaryIO, filename: str) -> bool:
    """PDFs are recognized by extension or content."""
    stream = file.stream if isinstance(file, FileStorage) else file
    stream.seek(0)
    return os.path.splitext(filename or '')[1].lower() == '.pdf' or stream.read(5) == b'%PDF-'


def open_images(file: FileStorage | BinaryIO, filename: str, dpi: int, pdf_pages: str = '',
                first_only: bool = False, copies: int = 1) -> tuple[list[Image.Image], int | None]:
    """Open an uploaded or stored image. PDFs are rendered with the given
    resolution, one image for each page pdf_pages selects (only the first of
    them with first_only). The second value is the page count of a PDF,
    None for other files. Files without extension are left to Pillow to
    identify."""
    ext = os.path.splitext(filename or '')[1].lower()
    if _is_pdf(file, filename):
        page_count = pdf_page_count(file)
        pages = selected_pdf_pages(pdf_pages, page_count)
        if first_only:
            pages = pages[:1]
        elif len(pages) * copies > current_app.config['PRINT_MAX_COUNT']:
            # Checked before any page is rendered
            raise PageSelectionError(f"A print job must not have more than {current_app.config['PRINT_MAX_COUNT']} labels, "
                                     f"{len(pages)} pages and {copies} copies would be {len(pages) * copies}")
        return [pdffile_to_image(file, dpi, page) for page in pages], page_count
    if ext and ext not in {ex for ex, f in Image.registered_extensions().items() if f in Image.OPEN}:
        raise ValueError("Unsupported file type")
    return [imgfile_to_image(file)], None


# What is shared with the installed app from the share menu of a phone
# (share_target in the manifest), kept until the label designer fetches it.
# The page fetches it right away, the limits only matter if it never does.
SHARE_MAX_AGE = 600
SHARE_MAX_ITEMS = 5
_shared: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_shared_lock = threading.Lock()
_URL = re.compile(r'https?://\S+')


@bp.route('/share', methods=['POST'])
def share():
    """Receive a file, link or text from the share menu and open the label
    designer with it: a file becomes the image, a link a QR code, a text the
    label text."""
    item = {}
    file = request.files.get('file')
    if file and file.filename:
        data = file.read()
        if data:
            item = {'name': file.filename, 'type': file.mimetype or 'application/octet-stream',
                    'data': base64.b64encode(data).decode('ascii')}
    if not item:
        # Android puts a shared link into the text, often after a title
        text = request.form.get('text', '').strip()
        url = request.form.get('url', '').strip() or (text if _URL.fullmatch(text) else '')
        if url:
            item = {'url': url}
        elif text:
            item = {'text': text[:MAX_TEXT_LENGTH]}
    if not item:
        return redirect(url_for('.index'), code=303)
    token = secrets.token_urlsafe(16)
    now = time.monotonic()
    with _shared_lock:
        for key in [key for key, (stored, _) in _shared.items() if now - stored > SHARE_MAX_AGE]:
            del _shared[key]
        _shared[token] = (now, item)
        while len(_shared) > SHARE_MAX_ITEMS:
            _shared.popitem(last=False)
    return redirect(url_for('.index', shared=token), code=303)


@bp.route('/share', methods=['GET'])
def shared():
    """Hand what was shared to the label designer, once."""
    token = request.args.get('token', '')
    with _shared_lock:
        stored = _shared.pop(token, None)
    if stored is None or time.monotonic() - stored[0] > SHARE_MAX_AGE:
        return make_response(jsonify({'message': 'The shared content is no longer available, share it again.'}), 404)
    return jsonify(stored[1])


@bp.route('/')
def index():
    debug = current_app.logger.isEnabledFor(logging.DEBUG)
    fonts = get_fonts()
    label_sizes = [
        (label.identifier, label_name(label), label.form_factor == FormFactor.ROUND_DIE_CUT, label.tape_size, label.dots_printable,
         ','.join(label_printers(label)))
        for label in ALL_LABELS
    ]
    return render_template(
        'labeldesigner.html',
        fonts=fonts.fontlist(),
        label_sizes=label_sizes,
        debug=debug,
        default_label_size=current_app.config['LABEL_DEFAULT_SIZE'],
        default_font_size=current_app.config['LABEL_DEFAULT_FONT_SIZE'],
        default_orientation=current_app.config['LABEL_DEFAULT_ORIENTATION'],
        default_qr_size=current_app.config['LABEL_DEFAULT_QR_SIZE'],
        default_image_mode=current_app.config['IMAGE_DEFAULT_MODE'],
        default_bw_threshold=current_app.config['IMAGE_DEFAULT_BW_THRESHOLD'],
        default_font_family=fonts.get_default_font()[0],
        default_font_style=fonts.get_default_font()[1],
        line_spacings=LINE_SPACINGS,
        default_line_spacing=current_app.config['LABEL_DEFAULT_LINE_SPACING'],
        default_dpi=DEFAULT_DPI,
        models_without_600_dpi=MODELS_WITHOUT_600_DPI,
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


def _remove_repo_images(repo: str, base: str, keep: str | None = None):
    """Remove the image files stored for the label named base, except keep."""
    for ext in set(REPO_IMAGE_EXTENSIONS.values()):
        if base + '_image' + ext == keep:
            continue
        path = os.path.join(repo, base + '_image' + ext)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                current_app.logger.exception(f'Failed to remove associated image {path}')


def _repo_path(repo: str, name: str) -> str:
    """Path of a file in the label repository. The callers sanitize the
    names, this makes sure that no path leaves the repository anyway."""
    path = safe_join(repo, name)
    if path is None:
        raise ValueError(f'Invalid file name: {name}')
    return path


def _stage_file(directory: str, content: bytes) -> str:
    """Write content to a new hidden file with a unique name in directory, so
    that concurrent saves do not write into the same temporary file."""
    tmp_path = os.path.join(directory, f'.{uuid.uuid4().hex}.tmp')
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(content)
    except BaseException:
        os.remove(tmp_path)
        raise
    return tmp_path


def _write_atomically(path: str, content: bytes):
    """Write a file so that it either has the old or the new content, also
    when writing fails part way through (e.g. disk full)."""
    tmp_path = _stage_file(os.path.dirname(path), content)
    try:
        os.replace(tmp_path, path)
    except BaseException:
        os.remove(tmp_path)
        raise


def _json_body() -> dict:
    data = request.get_json(force=True, silent=True)
    return data if isinstance(data, dict) else {}


@bp.route('/api/repository/list', methods=['GET'])
def repo_list():
    repo = _get_repo_dir()
    files = []
    for name in sorted(os.listdir(repo)):
        if not name.lower().endswith('.json'):
            continue
        path = os.path.join(repo, name)
        # Read label metadata, a single broken file must not break the list
        try:
            stat = os.stat(path)
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            current_app.logger.warning(f'Skipping unreadable repository file {name}: {e}')
            continue
        if not isinstance(data, dict):
            current_app.logger.warning(f'Skipping repository file {name}: not a label')
            continue
        entry = {'name': name, 'mtime': int(stat.st_mtime), 'size': stat.st_size}
        # Parse label size (support both snake_case and legacy camelCase)
        label_size = data.get('label_size') or data.get('labelSize')
        if label_size:
            label_size_human = next(
                (label_name(label) for label in ALL_LABELS if label.identifier == label_size), None
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
    if not isinstance(data, dict):
        return make_response(jsonify({'success': False, 'message': 'No JSON payload provided'}), 400)
    name = data.get('name') or request.values.get('name') or None
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name provided'}), 400)
    filename = secure_filename(name)
    if not filename:
        return make_response(jsonify({'success': False, 'message': 'Invalid name'}), 400)
    if not filename.lower().endswith('.json'):
        filename = filename + '.json'
    repo = _get_repo_dir()

    # Accept JSON image payloads that include raw base64 image data in
    # fields `image_data` (base64 string), `image_mime` and optional
    # `image_name` so clients can submit images using pure JSON payloads.
    # Decode before touching the stored files, so a broken payload does not
    # destroy the existing image.
    image_bytes = None
    img_b64 = data.get('image_data')
    if isinstance(img_b64, str) and len(img_b64) > 0:
        try:
            image_bytes = base64.b64decode(img_b64)
        except ValueError:
            return make_response(jsonify({'success': False, 'message': 'Invalid image data'}), 400)

    try:
        # Extract text properties. The label designer sends them as JSON string
        # in fontSettingsPerLine, API clients may send text as list or string.
        text = data.get('fontSettingsPerLine', data.get('text', []))
        if isinstance(text, str):
            text = json.loads(text)
        data['text'] = text

        # The image file is always named after the label, a name chosen by
        # the client could overwrite or refer to images of other labels
        base = os.path.splitext(filename)[0]
        own_images = {base + '_image' + ext for ext in REPO_IMAGE_EXTENSIONS.values()}
        image_name = None
        if image_bytes is not None:
            ext = REPO_IMAGE_EXTENSIONS.get(data.get('image_mime', 'image/png'), '.png')
            image_name = base + '_image' + ext
            data['image'] = image_name
        elif data.get('image') not in own_images:
            data.pop('image', None)

        # Remove raw image data from JSON before saving
        if 'image_data' in data:
            del data['image_data']

        # Remove redundant information about zeroth line font settings
        for key in ['font_size', 'font_inverted', 'font', 'font_align', 'font_checkbox', 'font_color', 'line_spacing', 'fontSettingsPerLine']:
            if key in data:
                del data[key]

        # Finally, save the JSON file. The new image is written beforehand but
        # only put in place once the label was saved, and images stored with
        # another file type before are removed after that.
        staged_image = _stage_file(repo, image_bytes) if image_bytes is not None else None
        try:
            _write_atomically(_repo_path(repo, filename), json.dumps(data, ensure_ascii=False, indent=2, default=str).encode('utf-8'))
            if staged_image is not None:
                os.replace(staged_image, _repo_path(repo, image_name))
                staged_image = None
                _remove_repo_images(repo, base, keep=image_name)
        finally:
            if staged_image is not None:
                os.remove(staged_image)
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
                    with open(image_path, 'rb') as imgfh:
                        b = imgfh.read()
                    # Guess mime from extension
                    ext = os.path.splitext(image_path)[1].lower()
                    data['image_name'] = image_ref
                    data['image_mime'] = REPO_IMAGE_MIME_TYPES.get(ext, 'application/octet-stream')
                    data['image_data'] = base64.b64encode(b).decode('ascii')
        except Exception:
            current_app.logger.exception('Failed to include repository image in load response')
        return data
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to load file'}), 500)


@bp.route('/api/repository/delete', methods=['POST'])
def repo_delete():
    name = _json_body().get('name') or request.values.get('name')
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name specified'}), 400)
    filename = secure_filename(name)
    repo = _get_repo_dir()
    path = os.path.join(repo, filename)
    if not os.path.exists(path):
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    try:
        os.remove(path)
        # Also remove the image file stored alongside the JSON
        _remove_repo_images(repo, os.path.splitext(filename)[0])
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
        im = label.generate()
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'message': str(e)}), 400)

    return_format = request.values.get('return_format', 'png')
    response_data = image_to_png_bytes(im)
    if return_format == 'base64':
        response_data = base64.b64encode(response_data)
        content_type = 'text/plain'
    else:
        content_type = 'image/png'
    response = make_response(response_data)
    response.headers.set('Content-type', content_type)
    if label.warnings:
        response.headers.set('X-Label-Warning', ' '.join(label.warnings))
    return response


@bp.route('/api/repository/print', methods=['POST'])
def repo_print():
    # Print a saved repository template by name. The server will load the JSON
    # and perform the same printing logic as the /api/print endpoint.
    name = _json_body().get('name') or request.values.get('name')
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
        device, model = _printer(request.values.get('printer'), request.values.get('model'))
        label_size = data.get('label_size') or current_app.config['LABEL_DEFAULT_SIZE']
        printer = PrinterQueue(model=model, device_specifier=device, label_size=label_size)

        # Determine printing options (print_count, cut_once, high_res)
        print_count = _check_print_count(int(request.values.get('print_count') or data.get('print_count') or 1))
        cut_once = int(request.values.get('cut_once') or data.get('cut_once') or 0) == 1
        high_res = int(request.values.get('high_res') or data.get('high_res') or 0) != 0
        # The label and its image have to be generated with the resolution
        # it is printed with
        data['high_res'] = high_res
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': str(e)}), 400)

    status = ""
    try:
        # Of a PDF each selected page is a label of its own
        images, _ = load_label_images(data, {}, copies=print_count)
        labels = [image for _ in range(print_count) for image in images]
        for i, image in enumerate(labels):
            label = create_label_from_request(data, {}, i, image)
            cut = not cut_once or (cut_once and i == len(labels) - 1)
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


def _check_print_count(print_count: int) -> int:
    if print_count < 1:
        raise ValueError("print_count must be greater than 0")
    max_count = current_app.config['PRINT_MAX_COUNT']
    if print_count > max_count:
        raise ValueError(f"print_count must not exceed {max_count}")
    return print_count


@bp.route('/api/barcodes', methods=['GET'])
def get_barcodes():
    barcodes = [code.upper() for code in barcode.PROVIDED_BARCODES]
    barcodes.insert(0, 'QR')  # Add QR at the top
    return {'barcodes': barcodes}


@bp.route('/api/preview', methods=['POST'])
def preview_from_image():
    try:
        values = request.values.to_dict(flat=True)
        files = request.files.to_dict(flat=True)
        # Of a PDF the first selected page
        images, page_count = load_label_images(values, files, first_only=True)
        label = create_label_from_request(values, files, image=images[0])
        im = label.generate()
    except HTTPException:
        # e.g. 413 for a request larger than MAX_CONTENT_LENGTH
        raise
    except Exception as e:
        _log_refused_request('Preview', e)
        error = 413 if "too long" in str(e) else 400
        return make_response(jsonify({'message': str(e)}), error)

    return_format = request.values.get('return_format', 'png')
    response_data = image_to_png_bytes(im)
    if return_format == 'base64':
        response_data = base64.b64encode(response_data)
        content_type = 'text/plain'
    else:
        content_type = 'image/png'
    response = make_response(response_data)
    response.headers.set('Content-type', content_type)
    # e.g. text that does not fit on the label, shown by the label designer
    if label.warnings:
        response.headers.set('X-Label-Warning', ' '.join(label.warnings))
    # The label designer offers to choose pages of a PDF with several
    if page_count:
        response.headers.set('X-PDF-Pages', str(page_count))
    # The orientation setting has no effect, the label designer says so
    if label.auto_orientation:
        response.headers.set('X-Label-Orientation', 'auto')
    return response


# Description of the API (OpenAPI), kept next to the routes it describes
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'openapi.json'), encoding='utf-8') as _spec_file:
    OPENAPI_SPEC = json.load(_spec_file)


@bp.route('/api/openapi.json', methods=['GET'])
def openapi_spec():
    return jsonify(OPENAPI_SPEC)


@bp.route('/api/printer_status', methods=['GET'])
def get_printer_status():
    return get_ptr_status(current_app.config)


@bp.route('/api/print', methods=['POST'])
def print_label():
    """
    API to print a label
    returns: JSON
    """
    return_dict = {'success': False}
    try:
        printer = create_printer_from_request(request)
        print_count = _check_print_count(int(request.values.get('print_count', 1)))
        cut_once = int(request.values.get('cut_once', 0)) == 1
        high_res = int(request.values.get('high_res', 0)) != 0
    except HTTPException:
        raise
    except Exception as e:
        return_dict['message'] = str(e)
        current_app.logger.exception(e)
        return make_response(jsonify(return_dict), 400)

    status = ""
    try:
        values = request.values.to_dict(flat=True)
        # Every uploaded image, not only the first
        files = request.files
        # Decode the image once, all labels of the job share it. Of a PDF
        # each selected page is a label of its own, the copies are collated.
        images, _ = load_label_images(values, files, copies=print_count)
        labels = [image for _ in range(print_count) for image in images]
        for i, image in enumerate(labels):
            label = create_label_from_request(values, files, i, image)
            # Cut only if we
            # - always cut, or
            # - we cut only once and this is the last label to be generated
            cut = not cut_once or (cut_once and i == len(labels) - 1)
            printer.add_label_to_queue(label, cut, high_res)
        status = printer.process_queue()
    except Exception as e:
        return_dict['message'] = str(e)
        _log_refused_request('Print job', e)
        return make_response(jsonify(return_dict), 400)

    return_dict['success'] = len(status) == 0
    if len(status) > 0:
        return_dict['message'] = status
        return make_response(jsonify(return_dict), 400)
    return return_dict


def _log_refused_request(what: str, error: Exception):
    """Log why a request was refused. Invalid input like a missing font size
    or an unknown font raises ValueError or LookupError, it is logged without
    a traceback, which would look like a crash in the log. Everything else
    is logged with its traceback."""
    if isinstance(error, (ValueError, LookupError)):
        current_app.logger.warning('%s refused: %s', what, error)
        current_app.logger.debug('%s refused', what, exc_info=True)
    else:
        current_app.logger.exception(error)


def _printer(requested_device, requested_model) -> tuple[str, str]:
    """Return the device and model selected by a request, or the configured
    ones. With PRINTERS, the model of a listed device is the configured one."""
    printers = current_app.config.get('PRINTERS') or []
    if printers:
        configured, model = printers[0][1], printers[0][0]
    else:
        configured, model = current_app.config['PRINTER_PRINTER'], current_app.config['PRINTER_MODEL']
    device = requested_device or configured
    listed = [d for _, d in printers]
    if device not in (configured, 'simulation', '?', *listed) and \
            not any(pattern.fullmatch(device) for pattern in PRINTER_DEVICE_PATTERNS):
        raise ValueError(f"Unsupported printer: {device}")
    model = next((m for m, d in printers if d == device), model)
    return device, requested_model or model


def create_printer_from_request(request: Request):
    label_size = request.values.get('label_size', '62')
    # Allow overriding the device specifier via the request (frontend selection)
    device, model = _printer(request.values.get('printer'), request.values.get('model'))
    return PrinterQueue(
        model=model,
        device_specifier=device,
        label_size=label_size
    )


# Default for create_label_from_request(): load the image from the request
_LOAD_IMAGE = object()


def create_label_from_request(d: dict = {}, files: dict = {}, counter: int = 0, image=_LOAD_IMAGE):
    label_size = d.get('label_size', "62")
    kind = next((label.form_factor for label in ALL_LABELS if label.identifier == label_size), None)
    if kind is None:
        raise LookupError("Unknown label_size")
    context = {
        'label_size': label_size,
        'print_type': d.get('print_type', 'text'),
        'label_orientation': d.get('orientation', 'standard'),
        'kind': kind,
        # Same defaults as the label designer uses
        'margin_top': int(d.get('margin_top', current_app.config['LABEL_DEFAULT_MARGIN_TOP'])),
        'margin_bottom': int(d.get('margin_bottom', current_app.config['LABEL_DEFAULT_MARGIN_BOTTOM'])),
        'margin_left': int(d.get('margin_left', current_app.config['LABEL_DEFAULT_MARGIN_LEFT'])),
        'margin_right': int(d.get('margin_right', current_app.config['LABEL_DEFAULT_MARGIN_RIGHT'])),
        'border_thickness': int(d.get('border_thickness', 0)),
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
        'image_crop': int(d.get('image_crop', 0)) > 0,
        'image_scaling_factor': float(d.get('image_scaling_factor', 100.0)),
        'image_rotation': int(d.get('image_rotation', 0)),
        'print_color': d.get('print_color', 'black'),
        'timestamp': int(d.get('timestamp', 0)),
        'high_res': int(d.get('high_res', 0)) != 0,
        'code_text': d.get('code_text', '').strip(),
        'shrink_text': int(d.get('shrink_text', 0)) != 0,
    }

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
    elif image_mode == 'red_and_black':
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

    width, height = _get_label_dimensions(context['label_size'], context['high_res'])
    if height > width:
        width, height = height, width
    if label_orientation == LabelOrientation.ROTATED:
        height, width = width, height

    # For each line in text, we determine and add the font path
    fonts = get_fonts()
    default_font = ','.join(fonts.get_default_font())
    warnings = []
    for line in context['text']:
        if 'size' not in line or not str(line['size']).isdigit():
            raise ValueError("Font size is required")
        if int(line['size']) < 1:
            raise ValueError("Font size must be at least 1")
        # Lines without a font, e.g. from the API, use the default font
        if not line.get('font'):
            line['font'] = default_font
        try:
            line['path'] = fonts.get_path(line['font'])
        except FontNotFound:
            # e.g. a label saved on an installation with other fonts
            warning = f'The font {_font_name(line["font"])} is not installed, {_font_name(default_font)} is used instead.'
            if warning not in warnings:
                current_app.logger.warning(warning)
                warnings.append(warning)
            line['font'] = default_font
            line['path'] = fonts.get_path(default_font)
        if len(line.get('text', '')) > MAX_TEXT_LENGTH:
            raise ValueError("Text is too long")

    fore_color = (255, 0, 0) if context['print_color'] == 'red' else (0, 0, 0)
    border_color = (255, 0, 0) if context['border_color'] == 'red' else (0, 0, 0)

    if image is _LOAD_IMAGE:
        image = load_label_image(d, files)

    # In "standard" orientation die-cut labels are landscape, so a portrait
    # image (e.g. a shipping label on a 103 x 164 mm label) came out small and
    # sideways. With auto-fit, an image without text is placed in the
    # orientation that makes it largest. Without auto-fit the chosen
    # orientation is kept.
    image_label = label_content in (LabelContent.IMAGE_BW, LabelContent.IMAGE_GRAYSCALE, LabelContent.IMAGE_RED_BLACK, LabelContent.IMAGE_COLORED)
    has_text = any(str(line.get('text', '')).strip() for line in context['text'])
    auto_orientation = (label_type == LabelType.DIE_CUT_LABEL and image_label and image is not None
                        and context['image_fit'] and not has_text)
    if auto_orientation:
        margins = (context['margin_left'], context['margin_right'], context['margin_top'], context['margin_bottom'])
        width, height, label_orientation = _fitting_orientation(
            image, width, height, label_orientation, margins, context['image_crop'], context['image_rotation'])

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
        image_crop=context['image_crop'],
        image_scaling_factor=context['image_scaling_factor'],
        image_rotation=context['image_rotation'],
        border_thickness=context['border_thickness'],
        border_roundness=context['border_roundness'],
        border_distance=(context['border_distanceX'], context['border_distanceY']),
        border_color=border_color,
        timestamp=context['timestamp'],
        counter=counter,
        code_text=context['code_text'],
        template_env_vars=_template_env_vars(),
        warnings=warnings,
        shrink_text=context['shrink_text'],
        auto_orientation=auto_orientation
    )


def _font_name(font: str) -> str:
    """A font "<family>,<style>" as the font dropdown shows it."""
    family, _, style = font.partition(',')
    return f'{family} ({style})'


def load_label_image(d: dict, files: dict) -> Image.Image | None:
    """Return the uploaded image, or the image stored with a repository label,
    converted to the image mode of the request. Of a PDF the first selected
    page."""
    return load_label_images(d, files, first_only=True)[0][0]


def load_label_images(d: dict, files: dict, first_only: bool = False,
                      copies: int = 1) -> tuple[list[Image.Image | None], int | None]:
    """The images of a label like load_label_image(), of a PDF one for each
    page pdf_pages selects, e.g. "1-3, 5" or "all", each printed as a label
    of its own. The second value is the page count of a PDF, None
    otherwise. Labels without image get [None]."""
    image_mode = d.get('image_mode', 'grayscale')
    bw_threshold = int(d.get('image_bw_threshold', 70))
    # PDFs are rendered at the resolution the label is printed with
    dpi = HIGH_RES_DPI if int(d.get('high_res', 0)) != 0 else DEFAULT_DPI
    pdf_pages = str(d.get('pdf_pages') or '')
    # Pages only make several labels of an image label
    first_only = first_only or d.get('print_type', 'text') != 'image'

    # Several uploads are printed one after the other, each as a label of its
    # own (of a PDF the selected pages). The page count is that of the first.
    uploads = files.getlist('image') if hasattr(files, 'getlist') else [files['image']] if files.get('image') is not None else []
    if uploads:
        if first_only:
            uploads = uploads[:1]
        elif len(uploads) > 1:
            # Checked before any file is rendered
            labels = sum(len(selected_pdf_pages(pdf_pages, pdf_page_count(upload))) if _is_pdf(upload, upload.filename) else 1
                         for upload in uploads)
            max_count = current_app.config['PRINT_MAX_COUNT']
            if labels * copies > max_count:
                raise PageSelectionError(f"A print job must not have more than {max_count} labels, {len(uploads)} files "
                                         f"with {labels} labels and {copies} copies would be {labels * copies}")
        images, page_count = [], None
        for i, upload in enumerate(uploads):
            opened, count = open_images(upload, upload.filename, dpi, pdf_pages, first_only, copies)
            images += [_convert_image(image, image_mode, bw_threshold) for image in opened]
            if i == 0:
                page_count = count
        return images, page_count

    # If no uploaded FileStorage was provided but the data references an
    # image filename (stored in repository), attempt to load it from the
    # repository directory and convert it consistent with uploaded images.
    image_ref = d.get('image')
    if isinstance(image_ref, str) and len(image_ref) > 0:
        try:
            image_path = os.path.join(_get_repo_dir(), secure_filename(image_ref))
            if os.path.exists(image_path):
                with open(image_path, 'rb') as fh:
                    images, page_count = open_images(fh, image_path, dpi, pdf_pages, first_only, copies)
                    return [_convert_image(image, image_mode, bw_threshold) for image in images], page_count
        except PageSelectionError:
            raise
        except Exception:
            current_app.logger.exception('Failed to load repository image')
    return [None], None


def _placed_image_size(image: Image.Image, crop: bool, rotation: int) -> tuple[float, float]:
    """Size of the image after the crop and rotation the label applies."""
    if crop:
        image = SimpleLabel._crop_image_to_content(image)
    angle = math.radians(rotation)
    cos, sin = abs(math.cos(angle)), abs(math.sin(angle))
    return image.width * cos + image.height * sin, image.width * sin + image.height * cos


def _fitting_orientation(image: Image.Image, width: int, height: int, orientation: LabelOrientation,
                         margins: tuple = (0, 0, 0, 0), crop: bool = False, rotation: int = 0) -> tuple:
    """Canvas size and orientation of a die-cut label that show the fitted
    image largest: the given ones or the ones turned by 90 degrees."""
    image_width, image_height = _placed_image_size(image, crop, rotation)
    margin_left, margin_right, margin_top, margin_bottom = margins

    def fitted_scale(canvas_width, canvas_height):
        return min(max(canvas_width - margin_left - margin_right, 1) / image_width,
                   max(canvas_height - margin_top - margin_bottom, 1) / image_height)

    if fitted_scale(height, width) > fitted_scale(width, height):
        turned = LabelOrientation.STANDARD if orientation == LabelOrientation.ROTATED else LabelOrientation.ROTATED
        return height, width, turned
    return width, height, orientation


def _template_env_vars() -> frozenset:
    """Environment variables label templates are allowed to insert."""
    allowed = current_app.config.get('LABEL_TEMPLATE_ENV_VARS', '')
    if isinstance(allowed, str):
        allowed = allowed.split(',')
    return frozenset(name.strip() for name in allowed if name.strip())


def _get_label_dimensions(label_size: str, high_res: bool = False):
    dimensions = next((label.dots_printable for label in ALL_LABELS if label.identifier == label_size), None)
    if dimensions is None:
        raise LookupError("Unknown label_size")
    if high_res:
        return (2 * dimensions[0], 2 * dimensions[1])
    return dimensions


def _convert_image(img: Image.Image, image_mode: str, bw_threshold: int = 70) -> Image.Image:
    if image_mode == 'grayscale':
        return convert_image_to_grayscale(img)
    elif image_mode == 'red_and_black':
        return convert_image_to_red_and_black(img)
    elif image_mode == 'colored':
        return img
    else:
        return convert_image_to_bw(img, bw_threshold)


def _scale_image_to_label(img: Image.Image, label_size: str, orientation: str,
                          high_res: bool = False) -> Image.Image:
    width, height = _get_label_dimensions(label_size, high_res)
    # Normalize: width = printable width, height = printable height
    if height > width:
        width, height = height, width
    if orientation == 'rotated':
        height, width = width, height

    kind = next((label.form_factor for label in ALL_LABELS if label.identifier == label_size), None)
    is_endless = kind == FormFactor.ENDLESS

    img_width, img_height = img.size
    if is_endless:
        # For endless labels, scale to fill the fixed dimension and let length vary
        if orientation == 'rotated':
            scale = height / img_height
        else:
            scale = width / img_width
    else:
        # For die-cut labels, fit within both dimensions
        scale = min(width / img_width, height / img_height)

    new_size = (max(1, int(img_width * scale)), max(1, int(img_height * scale)))
    check_image_size(*new_size)
    return img.resize(new_size, Image.Resampling.LANCZOS)


@bp.route('/api/webhook/print', methods=['POST'])
def webhook_print():
    """
    Webhook endpoint for external services to print images directly.

    URL: POST /labeldesigner/api/webhook/print

    Accepts multiple images via multipart form-data or JSON with base64-encoded
    images. Each image is scaled to fit the target label dimensions and printed
    in batch with a cut after each label. On die-cut labels each image is
    turned to the orientation that shows it largest.

    Authentication:
        The webhook is disabled unless the WEBHOOK_PASSWORD environment variable
        (or config key) is set. Generate a secure password with:

            python3 -c "import secrets; print(secrets.token_urlsafe(32))"

        Then export it before starting the server:

            export WEBHOOK_PASSWORD="<generated-password>"

        Authenticate requests using one of:
        - Authorization header: ``Authorization: Bearer <password>``
        - Form field: ``password=<password>``
        - JSON body field: ``"password": "<password>"``

        The password is not accepted as query parameter because URLs end up
        in access logs.

    Multipart form-data:
        - images: one or more image files (field name "images")
        - label_size: label size identifier (default: from config)
        - orientation: "standard" or "rotated" (default: from config)
        - image_mode: "grayscale", "bw", "red_and_black", "colored" (default: from config)
        - bw_threshold: black/white threshold 0-255 (default: from config)
        - high_res: 0 or 1 for 600 DPI (default: 0)
        - printer: printer device specifier (default: from config)
        - model: printer model (default: from config)

    JSON payload:
        {
            "images": [
                {"data": "<base64>", "mime": "image/png"},
                ...
            ],
            "label_size": "62",
            "orientation": "standard",
            "image_mode": "grayscale",
            "bw_threshold": 70,
            "high_res": 0,
            "printer": "...",
            "model": "..."
        }
    """
    # Webhook is disabled unless WEBHOOK_PASSWORD is configured
    webhook_password = current_app.config.get('WEBHOOK_PASSWORD', '')
    if not webhook_password:
        return make_response(jsonify({'success': False, 'message': 'Webhook is not enabled'}), 403)

    # Parse JSON body once (only when content type indicates JSON, not for multipart)
    jdata = request.get_json(silent=True)
    if not isinstance(jdata, dict):
        jdata = {}

    # Authenticate: accept password via Authorization Bearer token, form field, or JSON field
    provided = (request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
                or request.form.get('password', '')
                or jdata.get('password', ''))
    if not provided or not hmac.compare_digest(str(provided).encode(), webhook_password.encode()):
        return make_response(jsonify({'success': False, 'message': 'Unauthorized'}), 401)

    try:
        # Merge parameters from query/form and JSON body (query/form takes precedence)
        def _param(key, default):
            return request.values.get(key) or jdata.get(key) or default

        label_size = _param('label_size', current_app.config['LABEL_DEFAULT_SIZE'])
        orientation = _param('orientation', current_app.config['LABEL_DEFAULT_ORIENTATION'])
        image_mode = _param('image_mode', current_app.config['IMAGE_DEFAULT_MODE'])
        bw_threshold = int(_param('bw_threshold', current_app.config['IMAGE_DEFAULT_BW_THRESHOLD']))
        high_res = int(_param('high_res', 0)) != 0
        device, model = _printer(_param('printer', None), _param('model', None))

        # Validate label_size
        kind = next((label.form_factor for label in ALL_LABELS if label.identifier == label_size), None)
        if kind is None:
            return make_response(jsonify({'success': False, 'message': f'Unknown label_size: {label_size}'}), 400)
        # The message is returned without an exception, those may contain
        # details that should not reach the client
        problem = label_size_problem(label_size, model) or resolution_problem(model, high_res)
        if problem:
            return make_response(jsonify({'success': False, 'message': problem}), 400)

        # Determine label content type from image_mode
        if image_mode == 'grayscale':
            label_content = LabelContent.IMAGE_GRAYSCALE
        elif image_mode == 'red_and_black':
            label_content = LabelContent.IMAGE_RED_BLACK
        elif image_mode == 'colored':
            label_content = LabelContent.IMAGE_COLORED
        else:
            label_content = LabelContent.IMAGE_BW

        label_orientation = LabelOrientation.ROTATED if orientation == 'rotated' else LabelOrientation.STANDARD
        if kind == FormFactor.ENDLESS:
            label_type = LabelType.ENDLESS_LABEL
        elif kind == FormFactor.DIE_CUT:
            label_type = LabelType.DIE_CUT_LABEL
        else:
            label_type = LabelType.ROUND_DIE_CUT_LABEL

        width, height = _get_label_dimensions(label_size, high_res)
        if height > width:
            width, height = height, width
        if label_orientation == LabelOrientation.ROTATED:
            height, width = width, height

    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Invalid request parameters'}), 400)

    # Collect images from request: multipart file uploads first, otherwise
    # a JSON payload with base64 images
    uploaded_files = [f for f in request.files.getlist('images') if f.filename]
    json_images = jdata.get('images') if isinstance(jdata.get('images'), list) else []
    max_count = current_app.config['PRINT_MAX_COUNT']
    if len(uploaded_files or json_images) > max_count:
        return make_response(jsonify({'success': False, 'message': f'Too many images, at most {max_count} per request'}), 400)

    def prepare(img: Image.Image) -> tuple:
        # Convert and scale right away, so only label sized images are kept.
        # Images on die-cut labels are turned like with auto-fit in the label
        # designer.
        img = _convert_image(img, image_mode, bw_threshold)
        image_width, image_height, image_orientation = width, height, label_orientation
        if label_type == LabelType.DIE_CUT_LABEL:
            image_width, image_height, image_orientation = _fitting_orientation(img, width, height, label_orientation)
        image_orientation_name = 'rotated' if image_orientation == LabelOrientation.ROTATED else 'standard'
        return _scale_image_to_label(img, label_size, image_orientation_name, high_res), image_width, image_height, image_orientation

    pil_images = []
    dpi = HIGH_RES_DPI if high_res else DEFAULT_DPI
    try:
        for f in uploaded_files:
            pil_images.append(prepare(open_image(f, f.filename, dpi)))

        if not uploaded_files:
            for entry in json_images:
                if isinstance(entry, str):
                    # Plain base64 string
                    img_bytes = base64.b64decode(entry)
                elif isinstance(entry, dict) and 'data' in entry:
                    img_bytes = base64.b64decode(entry['data'])
                else:
                    continue
                pil_images.append(prepare(open_image(BytesIO(img_bytes), '', dpi)))

    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to read images'}), 400)

    if not pil_images:
        return make_response(jsonify({'success': False, 'message': 'No images provided'}), 400)

    # Process and queue each image
    try:
        printer = PrinterQueue(model=model, device_specifier=device, label_size=label_size)
        for img, image_width, image_height, image_orientation in pil_images:
            label = SimpleLabel(
                width=image_width,
                height=image_height,
                label_content=label_content,
                label_orientation=image_orientation,
                label_type=label_type,
                image=img,
                image_fit=True,
                text=[],
            )
            printer.add_label_to_queue(label, cut=True, high_res=high_res)
        status = printer.process_queue()
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to print labels'}), 400)

    result = {
        'success': len(status) == 0,
        'count': len(pil_images)
    }
    if status:
        result['message'] = status
        return make_response(jsonify(result), 400)
    return jsonify(result)

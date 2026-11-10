import io
import os
import re
import hmac
import math
import uuid
import time
import base64
import secrets
import barcode
import threading
import contextlib
from collections import OrderedDict
from io import BytesIO
from typing import BinaryIO
from urllib.parse import urlparse
from . import bp, demos, refusals
from .refusals import IMAGE_MODE_REFUSED, UNKNOWN_MODEL
from app import get_fonts
from app.fonts import FontNotFound
from PIL import Image
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import HTTPException
from . import drivers
from .drivers import DEVICE_PATTERNS, resolution_problem
from .drivers.devices import flock_within, look_again
from .printer import PrinterQueue, configured_printers, get_ptr_status
from .drivers.labels import FormFactor
from .cable_flag import CABLE_FLAG_REFUSED, cable_diameter, wrap_dots
from .label import MAX_TEXT_LENGTH, SimpleLabel, LabelContent, LabelOrientation, LabelType, TextTooLong, check_image_position, check_image_size, check_vertical_align, datetime_codes, parse_color
from .label_names import LABELS, MODELS, check_cut, label_dpi, label_length_problem, label_name, label_palette, label_printers, label_size_problem, length_limits
from flask import Request, current_app, json, jsonify, redirect, render_template, request, make_response, url_for
from werkzeug.security import safe_join
from werkzeug.utils import secure_filename
from app.utils import (
    convert_image_to_bw, convert_image_to_grayscale, convert_image_to_red_and_black, fill_first_line_fields,
    pdffile_to_image, imgfile_to_image, image_to_png_bytes, pdf_page_count, selected_pdf_pages, PageSelectionError,
    looks_faint, stronger_contrast
)

# The label size of a request or a saved label that names none, the same for
# the label and the printer
DEFAULT_LABEL_SIZE = '62'

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


@bp.errorhandler(ValueError)
def handle_value_error(e):
    _log_refused_request('Request', e)
    # A fixed message, the details are in the log
    return jsonify({"error": refusals.message(e, refusals.INVALID_INPUT)}), 400


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


@bp.route('/classic')
def classic():
    """The label designer before the redesign is gone, its bookmarks open
    the current one."""
    return redirect(url_for('.index'))


def _asset_version() -> int:
    """Changes when the script or the style sheet of the label designer
    changes, so browsers load the new files and cache them otherwise."""
    static = os.path.join(current_app.root_path, 'static', 'designer')
    return int(max(os.path.getmtime(os.path.join(static, name)) for name in ('designer.js', 'designer.css')))


@bp.route('/')
def index():
    """The label designer: an editor, a wizard with the saved and the
    ready-made labels, and the printer panel. It uses the API below."""
    fonts = get_fonts()
    # Previews are made one CPU core short of all of them, at most 16 at a
    # time, so that printing and other users are not slowed down
    try:
        cpus = len(os.sched_getaffinity(0))
    except AttributeError:
        cpus = os.cpu_count() or 2
    label_sizes = [
        {'id': label.identifier, 'name': label_name(label), 'form': label.form_factor.name.lower(),
         'tape': list(label.tape_size), 'dots': list(label.dots_printable), 'red': label.color == 1,
         'palette': label_palette(label), 'models': label_printers(label),
         'dpi': list(drivers.for_label(label.identifier).label_dpi(label)),
         # The shortest and longest endless label as cut, None without a length to set
         'length': list(limits[:2]) if (limits := length_limits(label)) else None}
        for label in LABELS
    ]
    return render_template(
        'designer.html',
        config={
            'base': url_for('.index').rstrip('/'),
            # Instances below one host, e.g. served below different paths
            # behind a reverse proxy, keep what they store in the browser
            # apart. The one at the root keeps the names it always had.
            'storage_prefix': f'{request.script_root}/' if request.script_root else '',
            'fonts': fonts.fontlist(),
            'default_font': ','.join(fonts.get_default_font()),
            'label_sizes': label_sizes,
            'default_label_size': current_app.config['LABEL_DEFAULT_SIZE'],
            # New labels start with them
            'defaults': {
                'orientation': current_app.config['LABEL_DEFAULT_ORIENTATION'],
                'font_size': current_app.config['LABEL_DEFAULT_FONT_SIZE'],
                'line_spacing': current_app.config['LABEL_DEFAULT_LINE_SPACING'],
                'qrcode_size': current_app.config['LABEL_DEFAULT_QR_SIZE'],
                'margin_top': current_app.config['LABEL_DEFAULT_MARGIN_TOP'],
                'margin_bottom': current_app.config['LABEL_DEFAULT_MARGIN_BOTTOM'],
                'margin_left': current_app.config['LABEL_DEFAULT_MARGIN_LEFT'],
                'margin_right': current_app.config['LABEL_DEFAULT_MARGIN_RIGHT'],
                'image_mode': current_app.config['IMAGE_DEFAULT_MODE'],
                'image_bw_threshold': current_app.config['IMAGE_DEFAULT_BW_THRESHOLD'],
            },
            # What the label designer offers for each printer model
            'models': {model: drivers.for_model(model).capabilities(model) for model in MODELS},
            'datetime_codes': datetime_codes(),
            'preview_parallel': max(1, min(16, cpus - 1)),
        },
        version=_asset_version(),
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


try:
    import fcntl
except ImportError:
    # Windows: one process
    fcntl = None


# Labels are put in place one after the other, so that a new one is not
# saved under a name that another request has just taken
_SAVING = threading.Lock()
# Seconds to wait for another request putting labels in place, e.g. one held
# up on a slow network share
SAVE_LOCK_TIMEOUT = 10


# The status of a print the printer did not do, e.g. it reports an error,
# does not answer or is busy with another job: the same request may succeed
# later. A request that is refused, e.g. for an unknown label size, gets 400.
PRINTER_FAILED = 503


class LabelsBusy(Exception):
    """Labels were put in place by another request for longer than
    SAVE_LOCK_TIMEOUT."""


def _busy():
    return make_response(jsonify({'success': False, 'message': 'Another label is being saved, try again'}), 503)


# Told once where the folder of the labels cannot be locked
_told_unlockable = False


def _lock_folder(folder: str, deadline: float, shared: bool = False) -> int | None:
    """The folder of the labels opened and locked, shared with other readers
    if shared, waiting until deadline, else LabelsBusy. None where it cannot
    be locked, e.g. on some network shares."""
    global _told_unlockable
    if fcntl is None:
        return None
    fd = None
    try:
        fd = os.open(folder, os.O_RDONLY)
        locked = flock_within(fd, max(deadline - time.monotonic(), 0), shared)
    except OSError as e:
        if fd is not None:
            os.close(fd)
        if not _told_unlockable:
            _told_unlockable = True
            current_app.logger.warning(f'Cannot lock the folder of the labels, saving waits for saves of this process only: {e}')
        return None
    if not locked:
        os.close(fd)
        raise LabelsBusy()
    return fd


@contextlib.contextmanager
def _saving(folder: str):
    """One label put in place at a time: in threads by a lock, in processes
    of the server, e.g. workers of gunicorn, by locking the folder of the
    labels, where it can be locked. Raises LabelsBusy where others take
    longer than SAVE_LOCK_TIMEOUT in all."""
    deadline = time.monotonic() + SAVE_LOCK_TIMEOUT
    if not _SAVING.acquire(timeout=SAVE_LOCK_TIMEOUT):
        raise LabelsBusy()
    try:
        fd = _lock_folder(folder, deadline)
        try:
            yield
        finally:
            if fd is not None:
                os.close(fd)
    finally:
        _SAVING.release()


@contextlib.contextmanager
def _reading(folder: str):
    """A label and its image read as they were saved together: not while
    one is put in place, alongside other readers. Raises LabelsBusy where a
    save takes longer than SAVE_LOCK_TIMEOUT."""
    fd = _lock_folder(folder, time.monotonic() + SAVE_LOCK_TIMEOUT, shared=True)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)


def _write_atomically(path: str, content: bytes):
    """Write a file so that it either has the old or the new content, also
    when writing fails part way through (e.g. disk full). The caller holds
    _saving()."""
    tmp_path = _stage_file(os.path.dirname(path), content)
    try:
        os.replace(tmp_path, path)
    except BaseException:
        os.remove(tmp_path)
        raise


def _write_new(path: str, content: bytes):
    """Write a file that does not exist yet, FileExistsError if it does, also
    when another request writes it at the same time. The written content is
    given the name in one step, so the file is never seen empty or half
    written: as a hard link, which also another process cannot take at the
    same time, else, e.g. on some network shares, after looking that there
    is none while no other save of this server puts one in place: the
    caller holds _saving()."""
    tmp_path = _stage_file(os.path.dirname(path), content)
    try:
        try:
            os.link(tmp_path, path)
        except FileExistsError:
            raise
        except OSError:
            if os.path.lexists(path):
                raise FileExistsError(path) from None
            os.replace(tmp_path, path)
            return
    except BaseException:
        os.remove(tmp_path)
        raise
    # The label is saved, a hidden copy left over does not change that
    try:
        os.remove(tmp_path)
    except OSError as e:
        current_app.logger.warning(f'Staged file {tmp_path} not removed: {e}')


class _Written(str):
    """A number of a JSON body as it is written, e.g. 2.50."""


def _as_numbers(value):
    """value of a JSON body read with _Written, its numbers as numbers."""
    if isinstance(value, _Written):
        return json.loads(value)
    if isinstance(value, dict):
        return {key: _as_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_numbers(item) for item in value]
    return value


def _defaults_written(fields):
    """The settings of blanks of a JSON body, their defaults as they are
    written, all other numbers as numbers."""
    if not isinstance(fields, dict):
        return _as_numbers(fields)
    return {name: {key: value if key == 'default' else _as_numbers(value) for key, value in field.items()} if isinstance(field, dict) else _as_numbers(field)
            for name, field in fields.items()}


def _json_body() -> dict:
    """The JSON body of the request as object, else empty. Read once. The
    values of blanks and their defaults keep their numbers as they are
    written, e.g. 2.50, all other numbers are numbers."""
    body = request.environ.get('labeldesigner.json_body')
    if body is None:
        data = None
        # Files are uploaded as a form, read once by it, never as JSON
        if request.mimetype != 'multipart/form-data':
            try:
                data = json.loads(request.get_data(cache=True), parse_int=_Written, parse_float=_Written)
                if isinstance(data, dict):
                    data = {key: value if key == 'blanks' else _defaults_written(value) if key == 'fields' else _as_numbers(value)
                            for key, value in data.items()}
                request.environ['labeldesigner.json_object'] = isinstance(data, dict)
            except (ValueError, RecursionError):
                # Also a number of more digits than Python takes, or nested
                # deeper than it reads
                data = None
        body = request.environ['labeldesigner.json_body'] = data if isinstance(data, dict) else {}
    return body


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
        # The name the label was saved under, the file name lacks e.g. umlauts
        if isinstance(data.get('title'), str) and data['title'].strip():
            entry['title'] = data['title'].strip()
        # Parse label size (support both snake_case and legacy camelCase)
        label_size = stored_label_size(data.get('label_size') or data.get('labelSize'))
        entry['label_size_id'] = label_size
        entry['label_size'] = _label_size_name(label_size)
        files.append(entry)
    return {'files': files}


def stored_label_size(value):
    """A label size as the labels name it. JSON clients may send a whole
    number, e.g. 62 for "62", labels saved like that before are read the same."""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return value


def _label_size_name(label_size: str | None) -> str | None:
    """The readable name of a label size, e.g. "62 mm endless (DK-22205 / DK-2205)"."""
    if not label_size:
        return None
    return next((label_name(label) for label in LABELS if label.identifier == label_size), str(label_size))


@bp.route('/api/repository/save', methods=['POST'])
def repo_save():
    # Expect JSON payload, values of blanks and their defaults as they are
    # written, e.g. 2.50
    data = dict(_json_body())
    if not request.environ.get('labeldesigner.json_object'):
        return make_response(jsonify({'success': False, 'message': 'No JSON payload provided'}), 400)
    # The name as for printing, previewing and removing: of the address,
    # else of the body
    name = _requested_name()
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name provided'}), 400)
    # Checked as when the label is printed, which would refuse them. Blanks
    # and their settings sent as JSON text are saved as objects.
    try:
        for key, parse in (('blanks', _blank_values), ('fields', _blank_fields)):
            if key in data:
                value = parse(data[key])
                if value:
                    data[key] = value
                else:
                    del data[key]
        _image_text_gap(data.get('image_text_gap'))
        _text_fit(data)
        cut = _cutting(data.get)[0]
        check_image_position(data.get('image_position'))
        check_vertical_align(data.get('vertical_align'))
        check_image_mode(data.get('image_mode'))
        if 'label_size' in data:
            data['label_size'] = stored_label_size(data['label_size'])
            label = next((label for label in LABELS if label.identifier == data['label_size']), None)
            if label is None:
                raise ValueError('Unknown label_size')
            for model in label_printers(label):
                check_cut(cut, model)
        size = next(label for label in LABELS if label.identifier == data.get('label_size', DEFAULT_LABEL_SIZE))
        _printed_length(_label_length(data.get('label_length')), size, _cable_flag(data, size.form_factor))
    except ValueError as e:
        _log_refused_request('Saving the label', e)
        return make_response(jsonify({'success': False, 'message': refusals.message(e, refusals.INVALID_INPUT)}), 400)
    filename = secure_filename(name)
    if not filename:
        return make_response(jsonify({'success': False, 'message': 'Invalid name'}), 400)
    if not filename.lower().endswith('.json'):
        filename = filename + '.json'
    # The name kept with it is the one it is saved under
    data['name'] = filename
    repo = _get_repo_dir()
    # A label saved under the name is replaced, unless replace is false
    # Read like the other flags, e.g. true, false, 1 or 0, in the address
    # before the body
    data.pop('replace', None)
    try:
        replace = _whole_or(_requested('replace'), 1)
    except (TypeError, ValueError):
        replace = None
    if replace not in (0, 1):
        return make_response(jsonify({'success': False, 'message': 'replace must be true or false'}), 400)
    replace = replace == 1

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
        elif not isinstance(data.get('image'), str) or data['image'] not in own_images:
            data.pop('image', None)

        # Remove raw image data from JSON before saving
        if 'image_data' in data:
            del data['image_data']

        # Remove redundant information about zeroth line font settings
        # and marks of blanks, which are for previews only
        for key in ['font_size', 'font_inverted', 'font', 'font_align', 'font_checkbox', 'font_color', 'line_spacing', 'fontSettingsPerLine', 'mark_blanks']:
            if key in data:
                del data[key]

        # Finally, save the JSON file. The new image is written beforehand but
        # only put in place once the label was saved, and images stored with
        # another file type before are removed after that.
        staged_image = _stage_file(repo, image_bytes) if image_bytes is not None else None
        try:
            # The label, its image and removing images of before, while no
            # other save or removal of a label is under way
            with _saving(repo):
                write = _write_atomically if replace else _write_new
                write(_repo_path(repo, filename), json.dumps(data, ensure_ascii=False, indent=2, default=str).encode('utf-8'))
                if staged_image is not None:
                    os.replace(staged_image, _repo_path(repo, image_name))
                    staged_image = None
                # Images of before the label no longer shows go, e.g. one
                # removed in the label designer
                _remove_repo_images(repo, base, keep=data.get('image'))
        finally:
            if staged_image is not None:
                os.remove(staged_image)
    except FileExistsError:
        return make_response(jsonify({'success': False, 'message': 'A label is saved under this name already'}), 409)
    except LabelsBusy:
        return _busy()
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to save file'}), 500)
    return {'success': True, 'name': filename}


@bp.route('/api/repository/load', methods=['GET'])
def repo_load():
    name = _requested_name()
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name specified'}), 400)
    filename = secure_filename(name)
    repo = _get_repo_dir()
    path = os.path.join(repo, filename)
    if not os.path.exists(path):
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    try:
        # Not while one is put in place, its image may not be there yet
        with _reading(repo):
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            return _label_for_designer(data, repo)
    except FileNotFoundError:
        # Removed by another request since it was looked for
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    except LabelsBusy:
        return _busy()
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to load file'}), 500)


def _label_for_designer(data: dict, image_dir: str) -> dict:
    """A stored label as the label designer loads it: the text as JSON, the
    settings of the first line, and the image from image_dir as base64 in
    image_data, so that the page can put it into its image upload. An
    image that is there but cannot be read raises OSError: without it, the
    label saved again would lose it."""
    text = data.get('text', [])
    if data.get('print_type') == 'qrcode':
        text = _code_with_text(data, text)
    data['text'] = json.dumps(text)
    data = fill_first_line_fields(text, data)
    if 'label_size' in data:
        data['label_size'] = stored_label_size(data['label_size'])
    # Flags and the text size read as when the label is printed, e.g. "true"
    # as 1, a text size from shrink_text of labels of before. One that is
    # refused stays as it is, the preview tells why.
    for key, default in (('image_fit', 1), ('image_crop', 0), ('image_contrast', 0)):
        try:
            data[key] = '1' if _whole_or(data.get(key), default) > 0 else '0'
        except (TypeError, ValueError):
            pass
    if _given(data.get('text_fit'), None) is None:
        try:
            data['text_fit'] = _text_fit(data)
        except ValueError:
            pass
    image_ref = data.get('image')
    if isinstance(image_ref, str) and len(image_ref) > 0:
        image_path = os.path.join(image_dir, secure_filename(image_ref))
        if os.path.exists(image_path):
            with open(image_path, 'rb') as imgfh:
                b = imgfh.read()
            # Guess mime from extension
            ext = os.path.splitext(image_path)[1].lower()
            data['image_name'] = image_ref
            data['image_mime'] = REPO_IMAGE_MIME_TYPES.get(ext, 'application/octet-stream')
            data['image_data'] = base64.b64encode(b).decode('ascii')
    return data


def _code_with_text(data: dict, text) -> list:
    """A label of a code alone as a code with its text left empty, which
    prints the same. Its lines were not printed, a code without content of
    its own took them: a QR code all of them, another code the first."""
    data['print_type'] = 'qrcode_text'
    lines = [line for line in text if isinstance(line, dict)] if isinstance(text, list) else []
    if not str(data.get('code_text') or '').strip():
        code = '\n'.join(str(line.get('text', '')) for line in lines).replace('\r\n', '\n').replace('\r', '\n')
        data['code_text'] = code if data.get('barcode_type', 'QR') == 'QR' else code.split('\n')[0]
    return [dict(lines[0], text='')] if lines else []


@bp.route('/api/repository/delete', methods=['POST'])
def repo_delete():
    name = _requested_name()
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name specified'}), 400)
    filename = secure_filename(name)
    repo = _get_repo_dir()
    path = os.path.join(repo, filename)
    if not os.path.exists(path):
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    try:
        with _saving(repo):
            os.remove(path)
            # Also remove the image file stored alongside the JSON
            _remove_repo_images(repo, os.path.splitext(filename)[0])
        return {'success': True}
    except FileNotFoundError:
        # Removed by another request since it was looked for
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    except LabelsBusy:
        return _busy()
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to delete file'}), 500)


def _requested(key: str):
    """A parameter of the request: of the form or query, else of a JSON
    body, so that a parameter of the address can change one of a body that
    stays the same."""
    # The body is read first: parsed as a form, e.g. JSON sent as a form by
    # curl, it is gone. Read as JSON it is no form: e.g. "&name=" in one of
    # its texts must not be taken for a parameter, only the address goes
    # before it.
    body = _json_body()
    for value in (_address_or_form().get(key), body.get(key)):
        if value is not None and value != '':
            return value
    return None


def _address_or_form():
    """The parameters of the address and of a form, those of the address
    only where the body is JSON."""
    _json_body()
    return request.args if request.environ.get('labeldesigner.json_object') else request.values


def _requested_name(key: str = 'name') -> str | None:
    """The name of a stored label in the request, or its id (key). None for
    one that is no text, e.g. a list or a number."""
    name = _requested(key)
    return name if isinstance(name, str) else None


def _option(key: str, stored: dict, default):
    """An option for printing a stored label: of the request, also false or
    0, else as stored with it, else default."""
    value = _requested(key)
    return value if value is not None else stored.get(key) or default


CUTS = ('each', 'last', 'none')


def _cutting(requested, stored: dict | None = None) -> tuple[str, bool, bool]:
    """How the labels of a job are cut: cut after each label, after the last
    one or not at all, the old cut_once as after the last one. half_cut and
    chain only matter on P-touch printers: a half cut between labels that
    are not cut, and the last label left in the printer, cut off by the next
    job. requested(key) is an option of the request, None where it is left
    out. A request that names cut or cut_once is cut its own way, else as
    stored with the label, else after each label."""
    stored = stored or {}
    names_cut = lambda get: _given(get('cut'), None) is not None or _given(get('cut_once'), None) is not None
    get = requested if names_cut(requested) or not names_cut(stored.get) else stored.get
    option = lambda key, default: _given(get(key), default)
    cut = option('cut', None) or ('last' if _whole(option('cut_once', 0)) == 1 else 'each')
    if cut not in CUTS:
        raise ValueError('cut must be each, last or none')
    # A P-touch printer cuts after the last label unless chain printing, also
    # with auto cut off: not cutting is chain printing
    return cut, _whole(option('half_cut', 1)) != 0, _whole(option('chain', 0)) != 0 or cut == 'none'


def _queue_labels(printer, labels, data, files, high_res, fast):
    """Adds the labels to the queue, the printer cuts them as printer.cut says."""
    # The copies share the text measured, alike where nothing changes
    layouts = {}
    for i, image in enumerate(labels):
        printer.add_label_to_queue(create_label_from_request(data, files, i, image, layouts=layouts), high_res=high_res, fast=fast)


def _given(value, default):
    """value, else default where it is left out or empty."""
    return default if value is None or value == '' else value


def _whole_or(value, default) -> int:
    """value as a whole number like _whole(), default where it is left out
    or empty."""
    return _whole(_given(value, default))


IMAGE_MODES = ('grayscale', 'black_and_white', 'red_and_black', 'colored')


def check_image_mode(value, default: str = 'grayscale') -> str:
    """The image mode of a request, default where it is left out or empty.
    bw, as the webhook was described, is black_and_white. Another one raises
    ValueError, it was printed in black and white."""
    mode = _given(value, default)
    if mode == 'bw':
        mode = 'black_and_white'
    if mode not in IMAGE_MODES:
        raise ValueError(IMAGE_MODE_REFUSED)
    return mode


def _whole(value) -> int:
    """value as a whole number: of a JSON number also 2.0, of text as it is
    written, e.g. 2, true and false as 1 and 0, also as text."""
    if isinstance(value, (bool, int)):
        return int(value)
    if isinstance(value, str) and value.strip().lower() in ('true', 'false'):
        return int(value.strip().lower() == 'true')
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{value} is not a whole number")
        return int(value)
    return int(str(value).strip())


def _load_repo_json(name: str) -> tuple[dict, bytes | None]:
    """A saved label and the content of the image saved with it, None
    without one. Read while no label is put in place: one saved meanwhile
    may not have its image in place yet."""
    filename = secure_filename(name)
    repo = _get_repo_dir()
    path = os.path.join(repo, filename)
    with _reading(repo):
        if not os.path.exists(path):
            raise FileNotFoundError(name)
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if 'label_size' in data:
            data['label_size'] = stored_label_size(data['label_size'])
        image, image_bytes = data.get('image'), None
        if isinstance(image, str) and image and os.path.exists(os.path.join(repo, secure_filename(image))):
            with open(os.path.join(repo, secure_filename(image)), 'rb') as fh:
                image_bytes = fh.read()
    data['text'] = json.dumps(data.get('text', []))
    return data, image_bytes


@bp.route('/api/repository/preview', methods=['GET', 'POST'])
def repo_preview():
    name = _requested_name()
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name specified'}), 400)
    try:
        data, image_bytes = _load_repo_json(name)
    except FileNotFoundError:
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    except LabelsBusy:
        return _busy()
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to preview file'}), 500)

    return _preview_response(data, _get_repo_dir(), image_bytes)


def _preview_response(data: dict, image_dir: str, image_bytes: bytes | None = None):
    """The preview of a stored label whose image is in image_dir, or of
    image_bytes where it was read with the label, as PNG or base64
    (return_format)."""
    # allow override printer, in a JSON body or the form or query
    printer = _requested('printer')
    if printer:
        data['printer'] = printer
    try:
        data['blanks'] = _requested_blanks(data.get('blanks'))
        label = create_label_from_request(data, {}, image=load_label_image(data, {}, image_dir, image_bytes))
        im = label.generate()
    except Exception as e:
        _log_refused_request('Preview', e)
        return make_response(jsonify({'message': refusals.message(e, refusals.PREVIEW_FAILED)}), 413 if isinstance(e, TextTooLong) else 400)

    return _preview_of(label, im, _requested('return_format') or 'png')


@bp.route('/api/repository/print', methods=['POST'])
def repo_print():
    # Print a saved repository template by name. The server will load the JSON
    # and perform the same printing logic as the /api/print endpoint.
    name = _requested_name()
    if not name:
        return make_response(jsonify({'success': False, 'message': 'No name specified'}), 400)
    try:
        data, image_bytes = _load_repo_json(name)
    except FileNotFoundError:
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    except LabelsBusy:
        return _busy()
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to load file'}), 500)
    return _print_stored_label(data, _get_repo_dir(), image_bytes)


def _print_stored_label(data: dict, image_dir: str, image_bytes: bytes | None = None):
    """Print a stored label whose image is in image_dir, or image_bytes where
    it was read with the label, like /api/print."""
    # Allow overriding printer or other request-like parameters, in a JSON
    # body or the form or query
    requested_printer = _requested('printer')
    if requested_printer:
        data['printer'] = requested_printer
    # Prepare printer queue using requested or default device/model and label_size from data
    try:
        data['blanks'] = _requested_blanks(data.get('blanks'))
        device, model = _printer(requested_printer, _requested('model'))
        label_size = data.get('label_size', DEFAULT_LABEL_SIZE)
        printer = PrinterQueue(model=model, device_specifier=device, label_size=label_size)

        # Determine printing options (print_count, cutting, high_res)
        print_count = _check_print_count(_whole(_option('print_count', data, 1)))
        printer.cut, printer.half_cut, printer.chain = _cutting(_requested, data)
        check_cut(printer.cut, printer.model)
        high_res = _whole(_option('high_res', data, 0)) != 0
        fast = _whole(_option('fast_printing', data, 0)) != 0
        # The label and its image have to be generated with the resolution
        # it is printed with
        data['high_res'] = high_res
    except Exception as e:
        _log_refused_request('Print job', e)
        return make_response(jsonify({'success': False, 'message': refusals.message(e, refusals.PRINT_FAILED)}), 400)

    status = ""
    try:
        # Of a PDF each selected page is a label of its own
        images, _ = load_label_images(data, {}, copies=print_count, image_dir=image_dir, image_bytes=image_bytes)
        collate = _requested('collate')
        labels = _copies(images, print_count, _whole_or(collate if collate is not None else data.get('collate'), 1) != 0)
        _queue_labels(printer, labels, data, {}, high_res, fast)
        status = printer.process_queue()
    except Exception as e:
        _log_refused_request('Print job', e)
        return make_response(jsonify({'success': False, 'message': refusals.message(e, refusals.PRINT_FAILED)}), 400)

    result = {'success': len(status) == 0}
    if len(status) > 0:
        result['message'] = status
        return make_response(jsonify(result), PRINTER_FAILED)
    return result


# --- Demo labels --------------------------------------------------------------------------
@bp.route('/api/demos/list', methods=['GET'])
def demo_list():
    categories = []
    for category in demos.categories():
        labels = [{'id': label_id, 'title': data.get('title') or label_id, 'language': data.get('language'),
                   'label_size_id': data.get('label_size'), 'label_size': _label_size_name(data.get('label_size'))}
                  for label_id, data in demos.labels(category['id'])]
        categories.append({'id': category['id'], 'name': category['name'], 'labels': labels})
    return {'categories': categories}


def _demo_response(label_id: str | None, respond, text_as_json: bool = True):
    """respond(label) for the demo label with the id, or why there is none.
    Only fixed messages are returned, never those of exceptions."""
    if not label_id:
        return make_response(jsonify({'success': False, 'message': 'No demo label specified'}), 400)
    try:
        data = demos.load(label_id)
    except FileNotFoundError:
        return make_response(jsonify({'success': False, 'message': 'Not found'}), 404)
    except Exception as e:
        current_app.logger.exception(e)
        return make_response(jsonify({'success': False, 'message': 'Failed to load the demo label'}), 500)
    if text_as_json:
        data['text'] = json.dumps(data.get('text', []))
    return respond(data)


@bp.route('/api/demos/load', methods=['GET'])
def demo_load():
    def respond(data):
        try:
            return _label_for_designer(data, demos.IMAGE_DIR)
        except OSError as e:
            current_app.logger.exception(e)
            return make_response(jsonify({'success': False, 'message': 'Failed to load the demo label'}), 500)
    return _demo_response(_requested_name('id'), respond, text_as_json=False)


@bp.route('/api/demos/preview', methods=['GET'])
def demo_preview():
    return _demo_response(_requested_name('id'), lambda data: _preview_response(data, demos.IMAGE_DIR))


@bp.route('/api/demos/print', methods=['POST'])
def demo_print():
    return _demo_response(_requested_name('id'), lambda data: _print_stored_label(data, demos.IMAGE_DIR))


def _copies(images: list, count: int, collate: bool) -> list:
    """The labels of count copies of several files or pages: as sets, ABC
    ABC, collated, else grouped, AA BB CC."""
    if collate:
        return [image for _ in range(count) for image in images]
    return [image for image in images for _ in range(count)]


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
        # Only its previews show the blanks still to fill in, never a label
        # printed or saved
        label = create_label_from_request(values, files, image=images[0], mark_blanks=_whole_or(values.get('mark_blanks'), 0) != 0)
        im = label.generate()
    except HTTPException:
        # e.g. 413 for a request larger than MAX_CONTENT_LENGTH
        raise
    except Exception as e:
        _log_refused_request('Preview', e)
        error = 413 if isinstance(e, TextTooLong) else 400
        # A fixed message, the details are in the log
        return make_response(jsonify({'message': refusals.message(e, refusals.PREVIEW_FAILED)}), error)

    response = _preview_of(label, im, request.values.get('return_format', 'png'))
    # The label designer offers to choose pages of a PDF with several
    if page_count:
        response.headers.set('X-PDF-Pages', str(page_count))
    # It suggests stronger contrast for a light image on the tapes of the VC-500W
    if (images[0] is not None and _palette(values.get('label_size', DEFAULT_LABEL_SIZE)) == 'color'
            and not _image_contrast(values) and looks_faint(images[0])):
        response.headers.set('X-Image-Faint', '1')
    return response


def _preview_of(label: SimpleLabel, im, return_format: str):
    """The response with the preview im of label, as PNG or base64
    (return_format), with what it tells about the label."""
    response_data = image_to_png_bytes(im, gray=not label.in_color)
    if return_format == 'base64':
        response_data = base64.b64encode(response_data)
        content_type = 'text/plain'
    else:
        content_type = 'image/png'
    response = make_response(response_data)
    response.headers.set('Content-type', content_type)
    _label_headers(response, label)
    return response


def _label_headers(response, label: SimpleLabel):
    """What a preview tells about the label made, for /api/preview and the
    previews of saved and demo labels alike."""
    # e.g. text that does not fit on the label, shown by the label designer
    if label.warnings:
        response.headers.set('X-Label-Warning', ' '.join(label.warnings))
    # The orientation setting has no effect, the label designer says so
    if label.auto_orientation:
        response.headers.set('X-Label-Orientation', 'auto')
    # The distance between image or code and text, shown by the label
    # designer next to its setting
    if label.image_text_gap:
        response.headers.set('X-Label-Image-Text-Gap', str(label.image_text_gap))
    # Where the image or code went, shown by the label designer with its
    # setting
    if label.image_side:
        response.headers.set('X-Label-Image-Position', label.image_side)


# Description of the API (OpenAPI), kept next to the routes it describes
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'openapi.json'), encoding='utf-8') as _spec_file:
    OPENAPI_SPEC = json.load(_spec_file)


@bp.route('/api/openapi.json', methods=['GET'])
def openapi_spec():
    return jsonify(OPENAPI_SPEC)


@bp.route('/api/printer_status', methods=['GET'])
def get_printer_status():
    # Printers on Bluetooth that did not answer are looked for again only
    # when asked, see devices.note_answer()
    if request.args.get('look') == '1':
        look_again()
    status = get_ptr_status(current_app.config)
    # The VC-500W prints in color, the label designer then offers colors
    for printer in [status] + status.get('printers', []):
        model = printer.get('model')
        printer['color_support'] = drivers.for_model(model).colors(model) == 'color'
    return status


@bp.route('/api/print', methods=['POST'])
def print_label():
    """
    API to print a label
    returns: JSON
    """
    return_dict = {'success': False}
    try:
        printer = create_printer_from_request(request)
        print_count = _check_print_count(_whole_or(request.values.get('print_count'), 1))
        printer.cut, printer.half_cut, printer.chain = _cutting(request.values.get)
        check_cut(printer.cut, printer.model)
        high_res = _whole_or(request.values.get('high_res'), 0) != 0
        fast = _whole_or(request.values.get('fast_printing'), 0) != 0
    except HTTPException:
        raise
    except Exception as e:
        return_dict['message'] = refusals.message(e, refusals.PRINT_FAILED)
        _log_refused_request('Print job', e)
        return make_response(jsonify(return_dict), 400)

    status = ""
    try:
        values = request.values.to_dict(flat=True)
        # Every uploaded image, not only the first
        files = request.files
        # Decode the image once, all labels of the job share it. Of a PDF
        # each selected page is a label of its own.
        images, _ = load_label_images(values, files, copies=print_count)
        labels = _copies(images, print_count, _whole_or(values.get('collate'), 1) != 0)
        _queue_labels(printer, labels, values, files, high_res, fast)
        status = printer.process_queue()
    except Exception as e:
        return_dict['message'] = refusals.message(e, refusals.PRINT_FAILED)
        _log_refused_request('Print job', e)
        return make_response(jsonify(return_dict), 400)

    return_dict['success'] = len(status) == 0
    if len(status) > 0:
        return_dict['message'] = status
        return make_response(jsonify(return_dict), PRINTER_FAILED)
    return return_dict


def _log_refused_request(what: str, error: Exception):
    """Log why a request was refused. Invalid input like a missing font size
    or an unknown font raises ValueError or LookupError, it is logged without
    a traceback, which would look like a crash in the log. Everything else,
    also KeyError and IndexError, is logged with its traceback."""
    if refusals.is_invalid_input(error):
        current_app.logger.warning('%s refused: %s', what, error)
        current_app.logger.debug('%s refused', what, exc_info=True)
    else:
        current_app.logger.exception(error)


PRINTER_MODELS = list(MODELS)


def _printer(requested_device, requested_model) -> tuple[str, str]:
    """Return the device and model selected by a request, or the configured
    ones. With PRINTERS, the model of a listed device is the configured one,
    and a request may name the printer instead of its device."""
    printers = configured_printers(current_app.config)
    if printers:
        configured, model = printers[0][1], printers[0][0]
    else:
        configured, model = current_app.config['PRINTER_PRINTER'], drivers.canonical(current_app.config['PRINTER_MODEL'])
    device = requested_device or configured
    named = {n.casefold(): d for _, d, n in printers if n}
    device = named.get(device.casefold(), device)
    listed = [d for _, d, _ in printers]
    if device not in (configured, 'simulation', '?', *listed) and \
            not any(pattern.fullmatch(device) for pattern in DEVICE_PATTERNS):
        raise ValueError(f"Unsupported printer: {device}")
    model = drivers.canonical(requested_model) if requested_model else next((m for m, d, _ in printers if d == device), model)
    if model not in PRINTER_MODELS:
        # The driver would refuse it without a message
        raise ValueError(UNKNOWN_MODEL if model == 'Unknown' else f"Unknown printer model: {model}")
    return device, model


def create_printer_from_request(request: Request):
    label_size = request.values.get('label_size', DEFAULT_LABEL_SIZE)
    # Allow overriding the device specifier via the request (frontend selection)
    device, model = _printer(request.values.get('printer'), request.values.get('model'))
    return PrinterQueue(
        model=model,
        device_specifier=device,
        label_size=label_size
    )


# Default for create_label_from_request(): load the image from the request
_LOAD_IMAGE = object()


def _cable_flag(d: dict, kind: FormFactor) -> float | None:
    """The diameter of the cable in mm for a cable flag, None without."""
    if _whole_or(d.get('cable_flag'), 0) <= 0:
        return None
    if _label_type(kind) != LabelType.ENDLESS_LABEL:
        raise ValueError(CABLE_FLAG_REFUSED)
    return cable_diameter(d.get('cable_diameter'))


def _flag_wrap(label_size: str, diameter: float | None, high_res: bool) -> dict:
    """The wrap of a cable flag in dots along the tape and its dpi."""
    if diameter is None:
        return {}
    dpi = label_dpi(label_size, high_res)
    return {'flag_wrap': wrap_dots(diameter, dpi), 'flag_dpi': dpi}


def _label_type(kind: FormFactor) -> LabelType:
    """The label type of a form factor. P-touch tapes are endless,
    they were taken for round die-cut labels and came out 1 dot long."""
    if kind in (FormFactor.ENDLESS, FormFactor.PTOUCH_ENDLESS):
        return LabelType.ENDLESS_LABEL
    if kind == FormFactor.DIE_CUT:
        return LabelType.DIE_CUT_LABEL
    return LabelType.ROUND_DIE_CUT_LABEL


def _palette(label_size: str) -> str:
    label = next((label for label in LABELS if label.identifier == label_size), None)
    return label_palette(label) if label is not None else 'black'


def _printable_color(value: str, palette: str) -> str:
    """A color the label can show: any on the tapes of the VC-500W, red for
    reddish colors on black/red/white labels, black otherwise. The designer
    offers only these, other clients may send any."""
    red, green, blue = parse_color(value or 'black')
    if palette == 'color':
        return value or 'black'
    if palette == 'red' and red - max(green, blue) > 64:
        return 'red'
    return 'black'


def _image_mode(d: dict) -> str:
    """Images stay in color on the tapes of the VC-500W."""
    mode = check_image_mode(d.get('image_mode'))
    return 'colored' if _palette(d.get('label_size', DEFAULT_LABEL_SIZE)) == 'color' else mode


def _image_contrast(d: dict) -> bool:
    """Whether the image gets stronger contrast, only on the tapes of the
    VC-500W, where light drawings print faint."""
    return _palette(d.get('label_size', DEFAULT_LABEL_SIZE)) == 'color' and _whole_or(d.get('image_contrast'), 0) != 0


def create_label_from_request(d: dict = {}, files: dict = {}, counter: int = 0, image=_LOAD_IMAGE, mark_blanks: bool = False, layouts: dict | None = None):
    label_size = d.get('label_size', DEFAULT_LABEL_SIZE)
    size = next((label for label in LABELS if label.identifier == label_size), None)
    if size is None:
        raise LookupError("Unknown label_size")
    kind = size.form_factor
    diameter = _cable_flag(d, kind)
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
        'image_mode': _image_mode(d),
        'image_bw_threshold': int(d.get('image_bw_threshold', 70)),
        'image_fit': _whole_or(d.get('image_fit'), 1) > 0,
        'image_crop': _whole_or(d.get('image_crop'), 0) > 0,
        'image_scaling_factor': float(d.get('image_scaling_factor', 100.0)),
        'image_rotation': int(d.get('image_rotation', 0)),
        'print_color': d.get('print_color', 'black'),
        'timestamp': int(d.get('timestamp', 0)),
        # 600 dpi is for QL printers only
        'high_res': _whole_or(d.get('high_res'), 0) != 0 and _palette(label_size) != 'color',
        'code_text': d.get('code_text', '').strip(),
        'text_fit': _text_fit(d),
        # Empty: an endless label grows with its content
        'label_length': _label_length(d.get('label_length')),
        'image_text_gap': _image_text_gap(d.get('image_text_gap')),
        # Left out or empty: above the text across the tape, left of it along
        'image_position': check_image_position(d.get('image_position')),
        # Left out or empty: in the middle of a label with room to spare
        'vertical_align': check_vertical_align(d.get('vertical_align')),
        'blanks': _blank_values(d.get('blanks')),
        # The settings of the blanks, saved with a label or sent with it
        'blank_fields': _blank_fields(d.get('fields')),
        # Previews of the label designer show the blanks still to fill in
        'mark_blanks': mark_blanks,
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
    # An image with a code as well, on the other side of the text
    with_code = print_type == 'image_qrcode_text'

    label_orientation = LabelOrientation.ROTATED if context['label_orientation'] == 'rotated' else LabelOrientation.STANDARD
    label_type = _label_type(context['kind'])

    width, height = _get_label_dimensions(context['label_size'], context['high_res'])
    if height > width:
        width, height = height, width
    if label_orientation == LabelOrientation.ROTATED:
        height, width = width, height
    # A length given for an endless label is its size along the tape
    length = _printed_length(context['label_length'], size, diameter, context['high_res'])
    if length is not None:
        if label_orientation == LabelOrientation.STANDARD:
            height = length
        else:
            width = length

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
            raise TextTooLong("Text is too long")

    palette = _palette(context['label_size'])
    fore_color = parse_color(_printable_color(context['print_color'], palette))
    border_color = parse_color(_printable_color(context['border_color'], palette))
    for line in context['text']:
        line['color'] = _printable_color(line.get('color'), palette)

    if image is _LOAD_IMAGE:
        image = load_label_image(d, files)

    # In "standard" orientation die-cut labels are landscape, so a portrait
    # image (e.g. a shipping label on a 103 x 164 mm label) came out small and
    # sideways. With auto-fit, an image without text or code is placed in the
    # orientation that makes it largest. Without auto-fit the chosen
    # orientation is kept.
    image_label = label_content in (LabelContent.IMAGE_BW, LabelContent.IMAGE_GRAYSCALE, LabelContent.IMAGE_RED_BLACK, LabelContent.IMAGE_COLORED)
    label = SimpleLabel(
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
        shrink_text=context['text_fit'] == 'shrink',
        fill_text=context['text_fit'] == 'fill',
        image_text_gap=context['image_text_gap'],
        image_position=context['image_position'],
        blanks=context['blanks'],
        blank_fields=context['blank_fields'],
        mark_blanks=context['mark_blanks'],
        layouts=layouts,
        with_code=with_code,
        fixed_length=length is not None,
        vertical_align=context['vertical_align'],
        **_flag_wrap(label_size, diameter, context['high_res']),
    )
    if (label_type == LabelType.DIE_CUT_LABEL and image_label and image is not None
            and context['image_fit'] and not label.has_lines() and not with_code):
        margins = (context['margin_left'], context['margin_right'], context['margin_top'], context['margin_bottom'])
        label.turn_to_fit(*_fitting_orientation(
            image, width, height, label_orientation, margins, context['image_crop'], context['image_rotation']))
    return label


def _text_fit(d: dict) -> str:
    """How the text is sized: none as set, shrink made smaller where it does
    not fit, fill as large as fits. Without text_fit, shrink_text tells
    shrink or none."""
    value = d.get('text_fit')
    if value is None or value == '':
        try:
            return 'shrink' if _whole_or(d.get('shrink_text'), 0) != 0 else 'none'
        except (TypeError, ValueError):
            raise ValueError("shrink_text must be 0 or 1") from None
    if value not in ('none', 'shrink', 'fill'):
        raise ValueError("text_fit must be none, shrink or fill")
    return value


def _label_length(value) -> float | None:
    """The length of an endless label of a request in millimeters, as it is
    cut. None (left out, empty or null) lets the label grow with its
    content."""
    if value is None or str(value).strip() == '':
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        length = float(value)
    except (TypeError, ValueError):
        raise ValueError("label_length must be a length in millimeters or empty") from None
    if not math.isfinite(length) or length <= 0:
        raise ValueError("label_length must be a length in millimeters or empty")
    return length


def _printed_length(length: float | None, label, diameter: float | None, high_res: bool = False) -> int | None:
    """Dots along the tape of the print of an endless label of length
    millimeters as cut, the printer feeds paper before and after it. None
    where the label has no length set: die-cut labels, cable flags, which
    hold the label twice, and tapes of printers whose labels grow with their
    content."""
    limits = length_limits(label)
    if length is None or diameter is not None or limits is None:
        return None
    problem = label_length_problem(label, length)
    if problem:
        raise ValueError(problem)
    return round((length - 2 * limits[2]) / 25.4 * label_dpi(label.identifier, high_res))


def _image_text_gap(value) -> int | None:
    """The distance between image or code and text of a request in dots, also
    a JSON number like 12.0. None (left out, empty or null) lets it follow
    the text size."""
    if value is None or str(value).strip() == '':
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        gap = _whole(value)
    except ValueError:
        raise ValueError("image_text_gap must be a whole number of dots or empty") from None
    if gap < 0:
        raise ValueError("image_text_gap must not be negative")
    return gap


def _json_object(value, valid, message: str) -> dict:
    """value as an object: as given, e.g. saved with a label, or as JSON text
    in a form or query, its numbers as they are written, e.g. 2.50. Without
    a value it is empty. One that is no object, or with a value that valid()
    refuses, raises ValueError(message)."""
    if value is None or value == '':
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value, parse_int=str, parse_float=str)
        except (ValueError, RecursionError):
            # Also nested deeper than Python reads
            value = None
    if not isinstance(value, dict) or not all(valid(v) for v in value.values()):
        raise ValueError(message)
    return value


def _blank_values(value) -> dict[str, str]:
    """The values for the blanks ({{blank:<name>}}) of a label by name: an
    object of texts or numbers."""
    values = _json_object(value, lambda v: isinstance(v, (str, int, float)) and not isinstance(v, bool),
                          refusals.BLANKS_MESSAGE)
    return {name: str(v) for name, v in values.items()}


BLANK_TYPES = ('text', 'number', 'date')


def _blank_setting(field) -> bool:
    """Whether field is the settings of a blank: an object, its type one of
    BLANK_TYPES, its format a text and its default a text or number, each
    where given."""
    if not isinstance(field, dict):
        return False
    default = field.get('default')
    return (field.get('type') in (None, *BLANK_TYPES) and isinstance(field.get('format'), (str, type(None)))
            and (default is None or (isinstance(default, (str, int, float)) and not isinstance(default, bool))))


def _blank_fields(value) -> dict:
    """The settings of the blanks by name, e.g. {"Frozen on": {"type":
    "date", "format": "%d.%m.%Y"}}."""
    return _json_object(value, _blank_setting, refusals.FIELDS_MESSAGE)


def _requested_blanks(stored=None) -> dict[str, str]:
    """The values for the blanks of a stored label: those stored with it,
    over them those of the request, blanks of a JSON body or else of the form
    or query."""
    return {**_blank_values(stored), **_blank_values(_requested('blanks'))}


def _font_name(font: str) -> str:
    """A font "<family>,<style>" as the font dropdown shows it."""
    family, _, style = font.partition(',')
    return f'{family} ({style})'


def load_label_image(d: dict, files: dict, image_dir: str | None = None, image_bytes: bytes | None = None) -> Image.Image | None:
    """Return the uploaded image, or the image stored with a repository label,
    converted to the image mode of the request. Of a PDF the first selected
    page."""
    return load_label_images(d, files, first_only=True, image_dir=image_dir, image_bytes=image_bytes)[0][0]


def load_label_images(d: dict, files: dict, first_only: bool = False, copies: int = 1,
                      image_dir: str | None = None, image_bytes: bytes | None = None) -> tuple[list[Image.Image | None], int | None]:
    """The images of a label like load_label_image(), of a PDF one for each
    page pdf_pages selects, e.g. "1-3, 5" or "all", each printed as a label
    of its own. The second value is the page count of a PDF, None
    otherwise. Labels without image get [None]. A stored label has its
    image in image_dir, by default the repository, or in image_bytes where
    it was read with the label."""
    image_mode = _image_mode(d)
    bw_threshold = int(d.get('image_bw_threshold', 70))
    contrast = _image_contrast(d)
    # PDFs are rendered at the resolution the label is printed with
    dpi = label_dpi(d.get('label_size', DEFAULT_LABEL_SIZE), _whole_or(d.get('high_res'), 0) != 0)
    pdf_pages = str(d.get('pdf_pages') or '')
    # Pages only make several labels of an image label
    first_only = first_only or d.get('print_type', 'text') not in ('image', 'image_qrcode_text')

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
            images += [_convert_image(image, image_mode, bw_threshold, contrast) for image in opened]
            if i == 0:
                page_count = count
        return images, page_count

    # If no uploaded FileStorage was provided but the data references an
    # image filename (stored in repository), attempt to load it from the
    # repository directory and convert it consistent with uploaded images.
    image_ref = d.get('image')
    if isinstance(image_ref, str) and len(image_ref) > 0:
        try:
            image_path = os.path.join(image_dir or _get_repo_dir(), secure_filename(image_ref))
            if image_bytes is not None or os.path.exists(image_path):
                with (io.BytesIO(image_bytes) if image_bytes is not None else open(image_path, 'rb')) as fh:
                    images, page_count = open_images(fh, image_path, dpi, pdf_pages, first_only, copies)
                    return [_convert_image(image, image_mode, bw_threshold, contrast) for image in images], page_count
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
    dimensions = next((label.dots_printable for label in LABELS if label.identifier == label_size), None)
    if dimensions is None:
        raise LookupError("Unknown label_size")
    if high_res:
        return (2 * dimensions[0], 2 * dimensions[1])
    return dimensions


def _convert_image(img: Image.Image, image_mode: str, bw_threshold: int = 70, contrast: bool = False) -> Image.Image:
    if image_mode == 'grayscale':
        return convert_image_to_grayscale(img)
    elif image_mode == 'red_and_black':
        return convert_image_to_red_and_black(img)
    elif image_mode == 'colored':
        return stronger_contrast(img) if contrast else img
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

    kind = next((label.form_factor for label in LABELS if label.identifier == label_size), None)
    is_endless = _label_type(kind) == LabelType.ENDLESS_LABEL

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
    as one job with a cut after each label. On die-cut labels each image is
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
        - image_mode: "grayscale", "black_and_white" (also "bw"), "red_and_black", "colored" (default: from config)
        - bw_threshold: black/white threshold 0-255 (default: from config)
        - high_res: 0 or 1 for 600 DPI (default: 0)
        - fast_printing: 0 or 1, 1 prints faster and a little less sharp (default: 0)
        - image_contrast: 0 or 1, 1 prints the images with stronger contrast on the tapes of the VC-500W (default: 0)
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

    # The JSON body, read once, none for a multipart upload
    jdata = _json_body()

    # Authenticate: accept password via Authorization Bearer token, form field, or JSON field
    provided = (request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
                or request.form.get('password', '')
                or jdata.get('password', ''))
    if not provided or not hmac.compare_digest(str(provided).encode(), webhook_password.encode()):
        return make_response(jsonify({'success': False, 'message': 'Unauthorized'}), 401)

    try:
        # Merge parameters from query/form and JSON body (query/form takes precedence)
        def _param(key, default):
            return _given(_requested(key), default)

        label_size = _param('label_size', current_app.config['LABEL_DEFAULT_SIZE'])
        orientation = _param('orientation', current_app.config['LABEL_DEFAULT_ORIENTATION'])
        image_mode = _param('image_mode', current_app.config['IMAGE_DEFAULT_MODE'])
        bw_threshold = int(_param('bw_threshold', current_app.config['IMAGE_DEFAULT_BW_THRESHOLD']))
        high_res = _whole_or(_requested('high_res'), 0) != 0
        fast = _whole_or(_requested('fast_printing'), 0) != 0
        contrast = _image_contrast({'label_size': label_size, 'image_contrast': _requested('image_contrast')})
        device, model = _printer(_param('printer', None), _param('model', None))

        # Validate label_size
        kind = next((label.form_factor for label in LABELS if label.identifier == label_size), None)
        if kind is None:
            return make_response(jsonify({'success': False, 'message': f'Unknown label_size: {label_size}'}), 400)
        try:
            image_mode = check_image_mode(image_mode, current_app.config['IMAGE_DEFAULT_MODE'])
        except ValueError:
            return make_response(jsonify({'success': False, 'message': IMAGE_MODE_REFUSED}), 400)
        # Images stay in color on the tapes of the VC-500W, printed at its resolution
        color = _palette(label_size) == 'color'
        if color:
            image_mode, high_res = 'colored', False
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
        label_type = _label_type(kind)

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
        img = _convert_image(img, image_mode, bw_threshold, contrast)
        image_width, image_height, image_orientation = width, height, label_orientation
        if label_type == LabelType.DIE_CUT_LABEL:
            image_width, image_height, image_orientation = _fitting_orientation(img, width, height, label_orientation)
        image_orientation_name = 'rotated' if image_orientation == LabelOrientation.ROTATED else 'standard'
        return _scale_image_to_label(img, label_size, image_orientation_name, high_res), image_width, image_height, image_orientation

    pil_images = []
    dpi = label_dpi(label_size, high_res)
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
            printer.add_label_to_queue(label, high_res=high_res, fast=fast)
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
        return make_response(jsonify(result), PRINTER_FAILED)
    return jsonify(result)

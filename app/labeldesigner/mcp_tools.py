"""The MCP endpoint (/mcp): tools for AI assistants to see the printers and
the saved labels and to print. The tools call the API of the app, so that
they share its checks and its messages. It needs the mcp package, see
requirements-mcp.txt and docs/mcp.md."""
import asyncio
import base64
import ipaddress
import json
import logging
import os
import re
import threading
from urllib.parse import urlsplit

from werkzeug.sansio.utils import host_is_trusted
from werkzeug.wrappers import Response

from .label import BLANK_TEMPLATE, DATE_OFFSET, _iso_date
from .label_names import LABELS, label_printers
from .refusals import BARCODE_TYPES

API = '/labeldesigner/api'
# The image of a preview, for clients that show images only by their address,
# e.g. Home Assistant: the request of the preview is in its address
PREVIEW = '/mcp/preview.png'
PREVIEW_PATHS = ('/preview', '/repository/preview')
# Host names of the home network, accepted without MCP_HOSTS
LOCAL_SUFFIXES = ('.local', '.lan', '.home.arpa')


def local_host(host: str) -> bool:
    """Whether host (without port) is an IP address, a single-label name
    or a name of the home network, e.g. labels.local."""
    host = host.strip('[]').lower()
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return '.' not in host.rstrip('.') or host.rstrip('.').endswith(LOCAL_SUFFIXES)


def _hostname(host: str) -> str:
    """host without its port, an IPv6 address with its brackets."""
    if host.startswith('['):
        return host[:host.find(']') + 1]
    return host.partition(':')[0]


def host_allowed(host: str, extra: list[str] | None = None, trusted: list[str] | None = None) -> bool:
    """Whether the endpoint answers requests for host, as Host or in Origin:
    names of the home network and those of MCP_HOSTS (extra), with
    TRUSTED_HOSTS set exactly its names, like the label designer. It
    protects against DNS rebinding, which the MCP specification requires."""
    name = _hostname(host)
    if not name:
        return False
    if trusted:
        return host_is_trusted(name, trusted)
    return local_host(name) or bool(extra and host_is_trusted(name, extra))


def _origin_host(origin: str) -> str:
    return urlsplit(origin).netloc


def loaded_label_sizes(printer: dict) -> list[str]:
    """The label sizes a printer may have loaded as its status tells, as the
    label designer finds them: both 62 and 62red where a QL printer does not
    tell whether its roll is black and red, all widths of the length a ZPL
    printer measured. Empty if it tells none."""
    width, length = printer.get('media_width'), printer.get('media_length') or 0
    if not width and not length:
        return []
    told = f"{printer.get('text_color') or ''} {printer.get('media_type') or ''}"
    red = bool(re.search('red', told, re.IGNORECASE))
    unknown = not red and not printer.get('text_color')
    return [label.identifier for label in LABELS
            if label.tape_size[1] == length and (not width or label.tape_size[0] == width)
            and printer.get('model') in label_printers(label)
            and (unknown or (label.color == 1) == red)]


# The USB devices as the kernel tells them, with the maker, model and serial
# number the printer reports
USB_DEVICES = '/sys/bus/usb/devices'
USB_SPECIFIER = re.compile(r'usb://(?:0x)?([0-9a-f]{1,4}):(?:0x)?([0-9a-f]{1,4})(?:/(.+))?', re.IGNORECASE)


def _read(folder: str, name: str) -> str:
    try:
        with open(os.path.join(folder, name), encoding='utf-8', errors='replace') as fh:
            return fh.read().strip()
    except OSError:
        return ''


def usb_info(device: str) -> dict:
    """What a printer on USB reports of itself: maker, model and serial
    number, for usb://vendor:product/serial and file:///dev/usb/lpN. Empty
    for others, or where the kernel does not tell (not on Linux)."""
    folder = None
    if device.startswith(('file:///dev/usb/lp', '/dev/usb/lp')):
        folder = os.path.dirname(os.path.realpath(f'/sys/class/usbmisc/{os.path.basename(device)}/device'))
    elif match := USB_SPECIFIER.fullmatch(device):
        vendor, product, serial = match.group(1).lower().zfill(4), match.group(2).lower().zfill(4), match.group(3)
        try:
            names = os.listdir(USB_DEVICES)
        except OSError:
            names = []
        folder = next((os.path.join(USB_DEVICES, name) for name in sorted(names)
                       if _read(os.path.join(USB_DEVICES, name), 'idVendor') == vendor
                       and _read(os.path.join(USB_DEVICES, name), 'idProduct') == product
                       and (not serial or _read(os.path.join(USB_DEVICES, name), 'serial').lstrip('0') == serial.lstrip('0'))),
                      None)
    if not folder or not _read(folder, 'idVendor'):
        return {}
    info = {'maker': _read(folder, 'manufacturer'), 'model': _read(folder, 'product'), 'serial': _read(folder, 'serial')}
    return {key: value for key, value in info.items() if value}


def _describe(printer: dict) -> dict:
    """How a printer is told to the assistant, by all it may be called."""
    return {'printer': printer.get('name') or printer.get('path'), 'alias': printer.get('name') or None,
            'device': printer.get('path'), 'driver': printer.get('model'), **usb_info(printer.get('path') or '')}


def find_printer(printers: list[dict], wanted: str) -> dict | None:
    """The printer wanted names by its alias, device, driver, maker, model or
    serial number, the same or a part of it, e.g. "Datamax" or "L9Z378866".
    Refused where it names several."""
    from mcp.server.mcpserver.exceptions import ToolError
    wanted = (wanted or '').strip().casefold()
    if not wanted:
        return None
    described = [(_describe(p), p) for p in printers]

    def names(described: dict) -> list[str]:
        return [str(described.get(k) or '').casefold() for k in ('alias', 'device', 'driver', 'maker', 'model', 'serial')]

    for same in (lambda n: n == wanted or n.lstrip('0') == wanted.lstrip('0') != '', lambda n: wanted in n):
        found = [(d, p) for d, p in described if any(same(n) for n in names(d) if n)]
        if len(found) == 1:
            return found[0][1]
        if found:
            raise ToolError(f'{wanted} names several printers: {", ".join(d["printer"] for d, _ in found)}')
    return None


def _named(sizes: list[str]) -> str:
    """Label sizes with the names the label designer shows, e.g.
    62: 62 mm endless (DK-22205 / DK-2205), joined with "; " as names
    have commas."""
    from .routes import _label_size_name
    return '; '.join(f'{size}: {_label_size_name(size)}' for size in sizes)


class AskUser(Exception):
    """Nothing done yet: the user is to be asked, e.g. which label is loaded."""


def label_size_for(model: str | None, label_size: str) -> str:
    """The label size of a printer of model, of any printer without model,
    that label_size names, also without the prefix of its driver and written
    as "100 x 30 mm", e.g. zpl100x30 for 100x30 on a ZPL printer."""
    from mcp.server.mcpserver.exceptions import ToolError
    takes = [label.identifier for label in LABELS if model is None or model in label_printers(label)]
    if not takes or label_size in takes:
        return label_size
    plain = re.sub(r'\s|mm', '', str(label_size).lower()).replace('×', 'x')
    found = [size for size in takes if size == plain or (size.endswith(plain) and size[:-len(plain)].isalpha())]
    if len(found) != 1 and model is None:
        raise ToolError(f'Label size {label_size} is one of {", ".join(found)}, give the printer' if found
                        else f'No label size {label_size}, give the printer to find out its sizes')
    if len(found) != 1:
        raise ToolError(f'No label size {label_size} for this printer. Ask the user which of its sizes is loaded: {_named(takes)}')
    return found[0]


def preview_address(base_url: str, spec: dict) -> str:
    """The address of the image of the preview spec below base_url, the
    address of the app with the path of a reverse proxy."""
    encoded = base64.urlsafe_b64encode(json.dumps(spec, separators=(',', ':')).encode()).decode().rstrip('=')
    return f'{base_url.rstrip("/")}{PREVIEW}?r={encoded}'


def _decoded(encoded: str) -> dict:
    """The preview spec of an address, only as preview_label makes it: the
    path and one object as json or data."""
    spec = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
    body = [key for key in ('json', 'data') if key in spec] if isinstance(spec, dict) else []
    if (len(body) != 1 or spec.get('path') not in PREVIEW_PATHS
            or not isinstance(spec[body[0]], dict) or set(spec) != {'path', *body}):
        raise ValueError('Not a preview')
    return spec


def _blank_names(data: dict) -> list[str]:
    texts = [line.get('text', '') for line in data.get('text', []) if isinstance(line, dict)]
    texts.append(str(data.get('code_text') or ''))
    names = []
    for text in texts:
        for name in BLANK_TEMPLATE.findall(str(text)):
            if name not in names:
                names.append(name)
    return names


def _labels(count: int) -> str:
    return f'{count} label' if count == 1 else f'{count} labels'


class Tools:
    """The tools, each one or a few requests to the API of app."""

    def __init__(self, app):
        self.app = app

    def _call(self, method: str, path: str, headers: dict | None = None, **kwargs):
        """A request to the API, the response as JSON or bytes, its headers
        put into headers. One that is refused raises ToolError with the
        message of the API."""
        from mcp.server.mcpserver.exceptions import ToolError
        trusted = self.app.config.get('TRUSTED_HOSTS')
        client = self.app.test_client()
        response = client.open(API + path, method=method, base_url=f'http://{trusted[0] if trusted else "localhost"}', **kwargs)
        if response.mimetype == 'application/json':
            result = response.get_json()
            if response.status_code >= 400 or result.get('success') is False:
                raise ToolError(result.get('message') or result.get('error') or f'Refused with {response.status_code}')
            return result
        if response.status_code >= 400:
            raise ToolError(f'Refused with {response.status_code}')
        if headers is not None:
            headers.update(response.headers)
        return response.data

    def _cap(self, count: int):
        from mcp.server.mcpserver.exceptions import ToolError
        cap = self.app.config['MCP_MAX_COUNT']
        if count < 1:
            raise ToolError('The number of labels must be at least 1')
        if count > cap:
            raise ToolError(f'At most {cap} labels per call (MCP_MAX_COUNT)')

    def _expected(self, printer: str, label_size: str | None, saved: str | None = None) -> tuple[dict, str]:
        """The status of printer and its label size label_size names, refused
        if it has another one loaded, or reports an error. Without
        label_size the one loaded, else the user is to be asked."""
        from mcp.server.mcpserver.exceptions import ToolError
        found = self._printer(printer)
        if found.get('errors'):
            raise ToolError(' '.join(found['errors']))
        label_size = label_size_for(found.get('model'), label_size) if label_size else self._loaded(found, saved)
        loaded = loaded_label_sizes(found)
        if loaded and label_size not in loaded:
            on = loaded[0] if len(loaded) == 1 else 'the one loaded'
            raise ToolError(f'The printer has {" or ".join(loaded)} loaded, not {label_size}. Load {label_size} or print on {on}.')
        return found, label_size

    @staticmethod
    def _loaded(found: dict, saved: str | None = None) -> str:
        """The label size loaded in the printer found, saved where it is
        one of those it may have loaded, else the user is to be asked."""
        loaded = loaded_label_sizes(found)
        black = [size for size in loaded if not size.endswith('red')]
        # A QL printer that cannot tell a black and red roll: black
        if len(black) == 1 and all(size in (black[0], black[0] + 'red') for size in loaded):
            loaded = black if saved != black[0] + 'red' else loaded
        # The simulator tells no label size, it takes the one of a saved label
        if saved and (saved in loaded or not loaded and found.get('path') == 'simulation'):
            return saved
        if len(loaded) == 1:
            return loaded[0]
        name = found.get('name') or found.get('path')
        if saved:
            raise AskUser(f'Nothing printed yet. Ask the user whether {_named([saved])} is loaded in {name}, then call again with label_size.')
        takes = loaded or [label.identifier for label in LABELS if found.get('model') in label_printers(label)]
        told = 'may have one of these loaded' if loaded else 'does not tell which label is loaded, it takes'
        raise AskUser(f'Nothing done yet. {name} {told}: {_named(takes)}. Ask the user which one, then call again with label_size.')

    def _printers(self) -> list[dict]:
        status = self._call('GET', '/printer_status')
        printers = status.get('printers') or [status]
        # Without PRINTERS the label designer adds the simulator, offered
        # here only as PRINTER_PRINTER
        if not self.app.config.get('PRINTERS'):
            simulator = self.app.config.get('PRINTER_PRINTER') == 'simulation'
            printers = printers[:1] if simulator else [p for p in printers if p.get('path') != 'simulation']
        return printers

    def _printer(self, printer: str) -> dict:
        from mcp.server.mcpserver.exceptions import ToolError
        found = find_printer(self._printers(), printer)
        if found is None:
            raise ToolError(f'Unknown printer {printer}, list_printers names them')
        return found

    def list_printers(self) -> list[dict]:
        return [{**_describe(p), 'status': p.get('status_type'), 'errors': p.get('errors') or [],
                 'loaded_label_sizes': loaded_label_sizes(p)}
                for p in self._printers()]

    def list_labels(self) -> list[dict]:
        from .routes import _get_repo_dir
        labels = []
        with self.app.app_context():
            repo = _get_repo_dir()
        for entry in self._call('GET', '/repository/list')['files']:
            try:
                with open(os.path.join(repo, entry['name']), encoding='utf-8') as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            fields = data.get('fields') if isinstance(data.get('fields'), dict) else {}
            defaults = data.get('blanks') if isinstance(data.get('blanks'), dict) else {}
            blanks = []
            for name in _blank_names(data):
                blank = {'name': name, **{k: v for k, v in (fields.get(name) or {}).items() if k in ('type', 'format', 'default')}}
                if name in defaults:
                    blank['default'] = defaults[name]
                blanks.append(blank)
            labels.append({'name': entry['name'], 'title': entry.get('title') or entry['name'],
                           'label_size': entry.get('label_size_id'), 'tags': data.get('tags') or [], 'blanks': blanks,
                           'text': [line.get('text', '') for line in data.get('text', []) if isinstance(line, dict)],
                           'code': (data.get('code_text') or None) if 'qrcode' in str(data.get('print_type')) else None})
        return labels

    def _saved(self, name: str) -> dict:
        """The saved label name names, with or without .json."""
        from mcp.server.mcpserver.exceptions import ToolError
        saved = next((label for label in self.list_labels() if label['name'] in (name, f'{name}.json')), None)
        if saved is None:
            raise ToolError(f'No saved label {name}, list_labels names them')
        return saved

    @staticmethod
    def _filled(saved: dict, fillings: list[dict]):
        """Refused where a filling names a blank the saved label does not
        have, or gives a date blank anything but a date like 2026-11-03 or
        one moved from today like +5d, as the API reads a date."""
        from mcp.server.mcpserver.exceptions import ToolError
        names = [blank['name'] for blank in saved['blanks']]
        dates = [blank['name'] for blank in saved['blanks'] if blank.get('type') == 'date']
        for filling in fillings:
            for name, value in filling.items():
                if name not in names:
                    raise ToolError(f'{saved["name"]} has no blank {name}, its blanks are: {", ".join(names) or "none"}')
                if name in dates and not (DATE_OFFSET.fullmatch(str(value).strip()) or _iso_date(str(value))):
                    raise ToolError(f'{name} is a date: give e.g. 2026-11-03 or +2m (d days, w weeks, m months, y years from today)')

    @staticmethod
    def _same_size(saved: dict, found: dict | None, size: str | None):
        """Refused where the saved label is for another label size than size,
        the one given or loaded in the printer found."""
        from mcp.server.mcpserver.exceptions import ToolError
        if size and size != saved['label_size']:
            where = (found.get('name') or found['path']) if found else 'The printer'
            raise ToolError(f'{where} has {size} loaded, {saved["name"]} is for {saved["label_size"]}. '
                            f'Ask the user to load {_named([saved["label_size"]])}.')

    @staticmethod
    def _text_label(lines: list[str], label_size: str, font_size: int, code: str | None, code_type: str) -> dict:
        form = {'label_size': label_size, 'text_fit': 'shrink', 'text_wrap': 1,
                'text': json.dumps([{'text': line, 'size': str(font_size)} for line in lines])}
        if code:
            form.update(print_type='qrcode_text' if lines else 'qrcode', code_text=code, barcode_type=code_type)
        return form

    def preview_label(self, name: str | None, blanks: dict | None, lines: list[str] | None, label_size: str | None,
                      font_size: int, code: str | None, code_type: str, printer: str | None) -> dict:
        from mcp.server.mcpserver.exceptions import ToolError
        found = self._printer(printer) if printer else None
        device = found['path'] if found else None
        if name:
            saved = self._saved(name)
            self._filled(saved, [blanks or {}])
            if label_size:
                size = label_size_for(found.get('model') if found else None, label_size)
            else:
                size = self._loaded(found, saved['label_size']) if found else None
            self._same_size(saved, found, size)
            body = {'name': saved['name'], 'blanks': blanks or {}}
            if device:
                body['printer'] = device
            return {'path': '/repository/preview', 'json': body}
        if label_size:
            label_size = label_size_for(found.get('model') if found else None, label_size)
        elif found:
            label_size = self._loaded(found)
        if not (lines or code) or not label_size:
            raise ToolError('Give the name of a saved label, or lines or a code and a label size')
        form = self._text_label(lines or [], label_size, font_size, code, code_type)
        if device:
            form['printer'] = device
        return {'path': '/preview', 'data': form}

    def preview(self, spec: dict, headers: dict | None = None) -> bytes:
        """The image of the preview spec, the path and the body of a request
        to the API, its headers put into headers."""
        return self._call('POST', spec['path'], headers=headers, **{k: v for k, v in spec.items() if k in ('json', 'data')})

    def checked(self, spec: dict) -> dict:
        """The headers of the preview spec, what the API tells about the
        label. Refused where the label designer warns about it, e.g. text
        cut off: nothing is printed then."""
        from mcp.server.mcpserver.exceptions import ToolError
        headers = {}
        self.preview(spec, headers)
        if headers.get('X-Label-Warning'):
            raise ToolError(f'Nothing printed: {headers["X-Label-Warning"]}')
        return headers

    def _printed(self, path: str, **kwargs) -> bytes:
        """Prints, the image of the first label as the printer got it."""
        return base64.b64decode(self._call('POST', f'{path}?return_format=png', **kwargs)['image'])

    def print_label(self, name: str, printer: str, label_size: str | None, fillings: list[dict],
                    count: int) -> tuple[str, list[dict], list[bytes]]:
        from mcp.server.mcpserver.exceptions import ToolError
        fillings = fillings or [{}]
        saved = self._saved(name)
        name = saved['name']
        self._filled(saved, fillings)
        found, label_size = self._expected(printer, label_size, saved['label_size'])
        self._same_size(saved, found, label_size)
        printer, device = found.get('name') or found['path'], found['path']
        specs = [{'path': '/repository/preview', 'json': {'name': name, 'printer': device, 'blanks': blanks}}
                 for blanks in fillings]
        # All of them are checked before the first one is printed, each page
        # of a saved PDF prints as a label of its own
        told = [self.checked(spec) for spec in specs]
        labels = count * sum(int(headers.get('X-PDF-Selected-Pages') or 1) for headers in told)
        self._cap(labels)
        images = []
        for done, spec in enumerate(specs):
            try:
                images.append(self._printed('/repository/print', json=dict(spec['json'], print_count=count)))
            except ToolError as e:
                raise ToolError(f'{e} ({done * labels // len(specs)} of {_labels(labels)} printed)') from None
        return f'Printed {_labels(labels)} of {name} on {printer}', specs, images

    def print_text(self, lines: list[str], printer: str, label_size: str | None, count: int, font_size: int,
                   code: str | None, code_type: str) -> tuple[str, list[dict], list[bytes]]:
        from mcp.server.mcpserver.exceptions import ToolError
        if not (lines or code):
            raise ToolError('Give lines or a code to print')
        self._cap(count)
        found, label_size = self._expected(printer, label_size)
        printer = found.get('name') or found['path']
        form = dict(self._text_label(lines, label_size, font_size, code, code_type), printer=found['path'])
        spec = {'path': '/preview', 'data': form}
        self.checked(spec)
        image = self._printed('/print', data=dict(form, print_count=count))
        return f'Printed {_labels(count)} on {printer}', [spec], [image]


def _server(app):
    from mcp.server.mcpserver import Context, Image, MCPServer
    from mcp.types import ToolAnnotations
    tools = Tools(app)
    reading = ToolAnnotations(read_only_hint=True)
    printing = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
    # Claude Code asks before each print, also in its auto mode
    confirm = {'anthropic/requiresUserInteraction': True}
    # MCPServer() calls logging.basicConfig(), the app keeps its own logging
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    server = MCPServer('labelburg', instructions=(
        'Prints labels on the label printers of this home. list_printers tells the printers and the label '
        'size loaded in each, list_labels the saved labels and their blanks. A printer is named by its alias, '
        'device, driver, maker, model or serial number, or a part of one. Give label_size only as the user '
        'told it, never guess one: without it the tools take the one the printer has loaded, or tell to ask '
        'the user. Show a preview_label before printing something new. Where you cannot show the image of a '
        'preview, show the Markdown image of its address in the result, never an image of your own. '
        'Printing makes paper: print only what was asked for.'))
    root.handlers[:] = handlers
    root.setLevel(level)

    def shown(ctx, call):
        """The text of call, the address of each of its previews and their
        images, or what to ask the user."""
        try:
            text, specs, images = call()
        except AskUser as e:
            return [str(e)]
        request = ctx.request_context.request
        if request is None:
            return [text, *(Image(data=image, format='png') for image in images)]
        text = '\n'.join([text, 'Where you cannot show the images, put these Markdown images as they are into your answer:',
                          *(f'![Label]({preview_address(str(request.base_url), spec)})' for spec in specs)])
        # Home Assistant hands each result to its model as JSON text, image
        # data included, so it gets the addresses only
        if request.headers.get('user-agent', '').startswith('HomeAssistant/'):
            return [text]
        return [text, *(Image(data=image, format='png') for image in images)]

    def coded(tool):
        """tool, its description with the code types the API takes."""
        tool.__doc__ += f' Code types: {", ".join(BARCODE_TYPES)}.'
        return tool

    def preview(name, blanks, lines, label_size, font_size, code, code_type, printer):
        spec = tools.preview_label(name, blanks, lines, label_size, font_size, code, code_type, printer)
        headers = {}
        image = tools.preview(spec, headers)
        warning = headers.get('X-Label-Warning')
        return (f'Preview. {warning}' if warning else 'Preview'), [spec], [image]

    @server.tool(annotations=reading)
    def list_printers() -> list[dict]:
        """The printers: alias, device, driver, and on USB the maker, model
        and serial number they report, with their status, errors and the
        label sizes they may have loaded: empty if the printer does not
        tell, ask which one; both 62 and 62red where a QL printer cannot
        tell; all widths of the length a ZPL printer measured."""
        return tools.list_printers()

    @server.tool(annotations=reading)
    def list_labels() -> list[dict]:
        """The saved labels: name to print them by, title, label size, tags,
        what they say (text lines with their blanks, and code) to find the one
        asked for, and their blanks, which print_label fills in by name. A blank of type
        date takes e.g. 2026-11-03 or +5d (d days, w weeks, m months, y
        years from today) and is today without a value."""
        return tools.list_labels()

    @server.tool(annotations=reading)
    @coded
    def preview_label(name: str | None = None, blanks: dict[str, str] | None = None, lines: list[str] | None = None,
                      label_size: str | None = None, font_size: int = 60, code: str | None = None,
                      code_type: str = 'QR', printer: str | None = None, ctx: Context = None) -> list:
        """The image of a label: of a saved label by name with its blanks
        filled in, or of lines of text and/or a code (QR, or a barcode type
        like EAN13 or CODE128) on label_size, e.g. 62 or 29x90, without it on
        the one loaded in printer. Font size
        in dots at 300 dpi, the text shrinks to fit. Returns its address and
        its image, and what the label designer warns about, e.g. text cut off."""
        return shown(ctx, lambda: preview(name, blanks, lines, label_size, font_size, code, code_type, printer))

    @server.tool(annotations=printing, meta=confirm)
    def print_label(name: str, printer: str, count: int, label_size: str | None = None,
                    fillings: list[dict[str, str]] | None = None, ctx: Context = None) -> list:
        """Print a saved label on printer, count labels of each filling of
        its blanks, e.g. [{"Name": "Anna"}, {"Name": "Ben"}] prints a badge
        for each, on the label size it is saved for. label_size only as the
        user told it, refused if the printer has another one loaded. Prints
        nothing where a label is cut off or a filling is refused. Returns
        the image of each printed."""
        return shown(ctx, lambda: tools.print_label(name, printer, label_size, fillings or [], count))

    @server.tool(annotations=printing, meta=confirm)
    @coded
    def print_text(printer: str, count: int, label_size: str | None = None, lines: list[str] | None = None,
                   font_size: int = 60, code: str | None = None, code_type: str = 'QR', ctx: Context = None) -> list:
        """Print lines of text and/or a code (QR, e.g. a URL or
        WIFI:T:WPA;S:<name>;P:<password>;;, or a barcode type like EAN13 or
        CODE128) on printer, count labels. label_size, e.g. 62 or 100x30,
        only as the user told it, else the one loaded; refused if the printer
        has another one loaded. Prints nothing where the label is cut off.
        Returns the image printed."""
        return shown(ctx, lambda: tools.print_text(lines or [], printer, label_size, count, font_size, code, code_type))

    return server


class Endpoint:
    """WSGI middleware that passes requests for /mcp to the MCP server and
    all others to app. The server runs on an event loop of its own, started
    with the first request."""

    def __init__(self, app, wsgi_app):
        self.app, self.wsgi_app = app, wsgi_app
        self._mcp = None
        self._lock = threading.Lock()

    def _start(self):
        from a2wsgi import ASGIMiddleware
        from mcp.server.transport_security import TransportSecuritySettings
        # Host and Origin are checked before, also behind a reverse proxy
        asgi = _server(self.app).streamable_http_app(
            stateless_http=True, json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True, name='mcp').start()
        started = threading.Event()

        # a2wsgi does not run the lifespan of the app, which starts the
        # session manager of the server
        async def keep_lifespan():
            async with asgi.router.lifespan_context(asgi):
                started.set()
                await asyncio.Event().wait()

        asyncio.run_coroutine_threadsafe(keep_lifespan(), loop)
        started.wait(10)
        return ASGIMiddleware(asgi, loop=loop)

    def __call__(self, environ, start_response):
        if environ.get('PATH_INFO') not in ('/mcp', PREVIEW):
            return self.wsgi_app(environ, start_response)
        hosts = self.app.config.get('MCP_HOSTS'), self.app.config.get('TRUSTED_HOSTS')
        origin = environ.get('HTTP_ORIGIN')
        if not host_allowed(environ.get('HTTP_HOST', ''), *hosts) or (origin and not host_allowed(_origin_host(origin), *hosts)):
            return Response('Host or Origin not allowed', 403)(environ, start_response)
        if environ['PATH_INFO'] == PREVIEW:
            return self._preview(environ, start_response)
        with self._lock:
            if self._mcp is None:
                self._mcp = self._start()
        return self._mcp(environ, start_response)

    def _preview(self, environ, start_response):
        from urllib.parse import parse_qs
        from mcp.server.mcpserver.exceptions import ToolError
        try:
            spec = _decoded(parse_qs(environ.get('QUERY_STRING', '')).get('r', [''])[0])
            response = Response(Tools(self.app).preview(spec), mimetype='image/png')
        except (ValueError, ToolError) as e:
            response = Response(str(e), 400)
        return response(environ, start_response)


def install(app):
    """Serve /mcp if the mcp package is installed and MCP_ENABLED."""
    if not app.config.get('MCP_ENABLED', True):
        return
    try:
        import a2wsgi  # noqa: F401
        import mcp  # noqa: F401
    except ImportError:
        return
    app.wsgi_app = Endpoint(app, app.wsgi_app)

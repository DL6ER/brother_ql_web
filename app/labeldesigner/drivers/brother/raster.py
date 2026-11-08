"""Labels as Brother raster data, after the conversion of brother_ql by
Philipp Klaus."""

import logging

from PIL import Image, ImageChops, ImageOps

from ...label import LabelContent, LabelOrientation, LabelType
from ..labels import FormFactor
from .commands import Raster, RasterError
from .labels import BY_ID

logger = logging.getLogger(__name__)

# Pixels at least 30 % black print black, unless dithered
THRESHOLD = int(0.3 * 255)


def render(queue, entries, keep=None) -> Raster:
    """Rasterize the queue entries into one job. Each label is converted as
    soon as its job is complete, so that the images of a long job are not all
    kept in memory; the simulator keeps them as PNG instead."""
    raster = Raster(queue.model)
    ptouch = raster.ptouch
    job = None
    for i, entry in enumerate(entries):
        label = entry['label']
        if label.label_type == LabelType.ENDLESS_LABEL:
            rotate = 0 if label.label_orientation == LabelOrientation.STANDARD else 90
        else:
            rotate = 'auto'
        dither = label.label_content != LabelContent.IMAGE_BW
        # The labels of a PT printer are one job. As jobs of their own, a
        # PT-E550W fed, half cut and cut the tape for each, also when it
        # was to be cut after the last one only.
        settings = (rotate, dither, entry['high_res'], entry['fast'])
        if job and not (ptouch and job['settings'] == settings):
            _add_job(queue, raster, job)
            job = None
        img = label.generate()
        if keep:
            keep(img, f'{i:03d}')
        raster.page_lengths.append(max(img.size) / (180 if ptouch else 300) * 25.4)
        if job:
            job['images'].append(img)
            job['cuts'].append(entry['cut'])
        else:
            job = {'settings': settings, 'images': [img], 'cuts': [entry['cut']]}
    if job:
        _add_job(queue, raster, job)
    return raster


def _add_job(queue, raster, job):
    """Convert the images of a job, labels printed with the same settings."""
    rotate, dither, high_res, fast = job['settings']
    options = {}
    if raster.ptouch:
        # Cut after every label, or with cut_once after the last one, the
        # printer half cuts between them. ESC i A counts up to 255 labels.
        # Chain printing: the last label stays in the printer, the next job
        # cuts it off and saves the tape fed before it.
        options = {'autocut': queue.cut != 'none', 'cut_every': 1 if all(job['cuts']) else min(len(job['images']), 255),
                   'half_cut': queue.half_cut, 'chain': queue.chain}
    # Unless printing fast, the printer gives priority to print quality. A
    # QL-810W printed a 29 x 90 mm label in 3.0 s instead of 1.8 s with it, a
    # little sharper. PT printers get compressed raster lines, as from
    # Brother's apps: a PT-E550W read uncompressed ones wrongly over Wi-Fi,
    # half cut the tape and hung in the printing state.
    convert(raster, job['images'], queue.label_size, red='red' in str(queue.label_size), dither=dither,
            cut=raster.ptouch or any(job['cuts']), dpi_600=high_res, hq=not fast, rotate=rotate,
            compress=raster.ptouch, **options)


def convert(raster: Raster, images: list, label_size: str, *, red: bool = False, dither: bool = False,
            cut: bool = True, dpi_600: bool = False, hq: bool = True, rotate='auto', compress: bool = False,
            autocut: bool = True, cut_every: int = 1, half_cut: bool = True, chain: bool = False):
    """Add the images to the raster as the pages of one job. rotate is 0, 90,
    180, 270 or 'auto', which turns die-cut labels made the other way round.
    On P-touch printers autocut says whether the tape is cut at all,
    cut_every after how many labels, half_cut whether it is half cut between
    them and chain whether the last label stays in the printer."""
    label = BY_ID[label_size]
    dots_printable = label.dots_printable
    right_margin = label.offset_r + raster.spec.additional_offset_r
    width = raster.pixel_width
    if red and not raster.spec.two_color:
        raise RasterError('Printing in red is not supported with the selected model.')

    raster.add_switch_mode()
    raster.add_invalidate()
    raster.add_initialize()
    raster.add_switch_mode()
    for i, im in enumerate(images):
        if im.mode.endswith('A'):
            # In front of a white background
            background = Image.new('RGB', im.size, (255, 255, 255))
            background.paste(im, im.split()[-1])
            im = background
        elif im.mode == 'P':
            im = im.convert('RGB' if red else 'L')
        elif im.mode == 'L' and red:
            im = im.convert('RGB')

        dots_expected = [dots * 2 for dots in dots_printable] if dpi_600 else dots_printable
        if label.form_factor in (FormFactor.ENDLESS, FormFactor.PTOUCH_ENDLESS):
            if rotate not in ('auto', 0):
                im = im.rotate(rotate, expand=True)
            if dpi_600:
                im = im.resize((im.size[0] // 2, im.size[1]))
            if im.size[0] != dots_printable[0]:
                logger.warning('Resizing the image to the width of the label')
                im = im.resize((dots_printable[0], int((dots_printable[0] / im.size[0]) * im.size[1])),
                               Image.Resampling.LANCZOS)
            if im.size[0] < width:
                padded = Image.new(im.mode, (width, im.size[1]), (255,) * len(im.mode))
                padded.paste(im, (width - im.size[0] - right_margin, 0))
                im = padded
        else:
            if rotate == 'auto':
                if im.size[0] == dots_expected[1] and im.size[1] == dots_expected[0]:
                    im = im.rotate(90, expand=True)
            elif rotate != 0:
                im = im.rotate(rotate, expand=True)
            if im.size[0] != dots_expected[0] or im.size[1] != dots_expected[1]:
                raise ValueError(f'Bad image dimensions: {im.size}. Expecting: {dots_expected}.')
            if dpi_600:
                im = im.resize((im.size[0] // 2, im.size[1]))
            padded = Image.new(im.mode, (width, dots_expected[1]), (255,) * len(im.mode))
            padded.paste(im, (width - im.size[0] - right_margin, 0))
            im = padded

        if red:
            red_im = _layer(_filtered_hsv(im, lambda h: 255 if h < 40 or h > 210 else 0,
                                          lambda s: 255 if s > 100 else 0, lambda v: 255 if v > 80 else 0))
            black_im = _layer(_filtered_hsv(im, None, None, lambda v: 255 if v < 80 else 0))
            black_im = ImageChops.subtract(black_im, red_im)
        else:
            im = ImageOps.invert(im.convert('L'))
            if dither:
                im = im.convert('1', dither=Image.Dither.FLOYDSTEINBERG)
            else:
                im = im.point(lambda x: 0 if x < THRESHOLD else 255, mode='1')

        raster.add_status_information()
        tape = label.tape_size
        if label.form_factor in (FormFactor.DIE_CUT, FormFactor.ROUND_DIE_CUT):
            raster.add_media_and_quality(im.size[1], 0x0B, tape[0], tape[1], hq)
        elif label.form_factor == FormFactor.ENDLESS:
            raster.add_media_and_quality(im.size[1], 0x0A, tape[0], 0, hq)
        else:
            # A P-touch printer reads media type 0 as "no tape", a PT-E550W
            # half cut the tape and hung in the printing state
            raster.add_media_and_quality(im.size[1], None, tape[0], 0, hq)
        if cut:
            raster.add_autocut(autocut)
            if autocut:
                raster.add_cut_every(cut_every)
        raster.add_expanded_mode(cut, dpi_600, red, half_cut, chain)
        raster.add_margins(label.feed_margin)
        raster.add_compression(compress)
        if red:
            raster.add_raster_data(black_im, red_im)
        else:
            raster.add_raster_data(im)
        raster.add_print(last_page=i == len(images) - 1)


def _filtered_hsv(im: Image.Image, filter_h, filter_s, filter_v) -> Image.Image:
    """The pixels whose hue, saturation and value pass the filters, the others
    white. A filter of None passes all."""
    bands = im.convert('HSV').split()
    masks = [band.point(f) for band, f in zip(bands, (filter_h, filter_s, filter_v)) if f]
    mask = masks[0]
    for other in masks[1:]:
        mask = ImageChops.darker(mask, other)
    filtered = Image.new('RGB', im.size, (255, 255, 255))
    filtered.paste(im, None, mask)
    return filtered


def _layer(im: Image.Image) -> Image.Image:
    """The dark pixels of an image as a binary layer, white where printed."""
    return ImageOps.invert(im.convert('L')).point(lambda x: 0 if x < THRESHOLD else 255, mode='1')

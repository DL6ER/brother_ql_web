"""Labels as Brother raster data, converted by brother_ql."""

from brother_ql import BrotherQLRaster, create_label

from ...label import LabelContent, LabelOrientation, LabelType


class Raster(BrotherQLRaster):
    """brother_ql gives P-touch tapes the media type 0x00, which PT printers
    read as "no tape": a PT-E550W half cut the tape and hung in the printing
    state. Without it, the printer checks the width of the tape only."""

    # Labels after which a PT printer cuts the tape, between them it only
    # half cuts. brother_ql sends no "cut each * labels" (ESC i A) to PT
    # printers, which then cut after every label.
    cut_every = 1
    # Whether a PT printer cuts the tape at all (ESC i M). brother_ql only
    # sends it to switch cutting on.
    autocut = True

    def add_media_and_quality(self, rnumber):
        if self.model.startswith('PT-') and getattr(self, '_mtype', None) == b'\x00':
            self._mtype = None
        super().add_media_and_quality(rnumber)

    def add_autocut(self, autocut=False):
        super().add_autocut(self.autocut if self.model.startswith('PT-') else autocut)

    def add_cut_every(self, n=1):
        if self.model.startswith('PT-'):
            if self.autocut:
                self.data += b'\x1biA' + bytes([self.cut_every])
        else:
            super().add_cut_every(n)

    def add_print(self, last_page=True):
        super().add_print(last_page)
        # The print information of the following labels says that they are
        # not the first page, brother_ql leaves it at 0
        self.page_number += 1


def render(queue, entries, keep=None) -> Raster:
    """Rasterize the queue entries into one BrotherQLRaster. Each label
    is converted as soon as its job is complete, so that the images of
    a long job are not all kept in memory; the simulator keeps them as
    PNG instead."""
    qlr = Raster(queue.model)
    ptouch = queue.model.startswith('PT-')
    # The length of each page in mm, at most: how long the printer may
    # take to print it
    qlr.page_lengths = []
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
            _add_job(queue, qlr, job)
            job = None
        img = label.generate()
        if keep:
            keep(img, f'{i:03d}')
        qlr.page_lengths.append(max(img.size) / (180 if ptouch else 300) * 25.4)
        if job:
            job['images'].append(img)
            job['cuts'].append(entry['cut'])
        else:
            job = {'settings': settings, 'images': [img], 'cuts': [entry['cut']]}
    if job:
        _add_job(queue, qlr, job)
    return qlr


def _add_job(queue, qlr, job):
    """Convert the images of a job, labels printed with the same settings."""
    ptouch = queue.model.startswith('PT-')
    rotate, dither, high_res, fast = job['settings']
    if ptouch:
        # Cut after every label, or with cut_once after the last one, the
        # printer half cuts between them. ESC i A counts up to 255 labels.
        qlr.cut_every = 1 if all(job['cuts']) else min(len(job['images']), 255)
        qlr.autocut = queue.cut != 'none'
        qlr.half_cut = queue.half_cut
        # Chain printing: the last label stays in the printer, the
        # next job cuts it off and saves the tape fed before it
        qlr.no_chain_printing = not queue.chain
    # Unless printing fast, the printer gives priority to print
    # quality (ESC i z). A QL-810W printed a 29 x 90 mm label in
    # 3.0 s instead of 1.8 s with it, a little sharper.
    # PT printers get compressed raster lines, as from Brother's
    # apps: a PT-E550W read uncompressed ones wrongly over Wi-Fi,
    # half cut the tape and hung in the printing state.
    qlr.page_number = 0
    create_label(
        qlr,
        job['images'],
        queue.label_size,
        red='red' in str(queue.label_size),
        dither=dither,
        cut=ptouch or any(job['cuts']),
        dpi_600=high_res,
        hq=not fast,
        rotate=rotate,
        compress=ptouch
    )

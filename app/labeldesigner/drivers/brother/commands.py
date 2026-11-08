"""The raster commands of the Brother QL and P-touch printers, after the
raster module of brother_ql by Philipp Klaus and Brother's raster command
references, e.g. that of the PT-E550W, PT-P750W and PT-P710BT:
https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf"""

import struct

import packbits
from PIL import Image

from .models import MODELS


class RasterError(Exception):
    """Raster data that does not fit the printer."""


class Raster:
    """The commands of a print job for a model, added to data one after the
    other. page_lengths holds the length of each page in mm, at most: how
    long the printer may take to print it."""

    def __init__(self, model: str):
        self.model = model
        self.spec = MODELS[model]
        self.ptouch = model.startswith('PT-')
        self.data = b''
        self.page_number = 0
        self.page_lengths = []
        self.compression = False

    @property
    def pixel_width(self) -> int:
        return self.spec.number_bytes_per_row * 8

    def add_switch_mode(self):
        """Raster mode, on printers that have other modes (ESC i a)."""
        if self.spec.mode_setting:
            self.data += b'\x1b\x69\x61\x01'

    def add_invalidate(self):
        """Clear the command buffer."""
        self.data += b'\x00' * self.spec.num_invalidate_bytes

    def add_initialize(self):
        self.page_number = 0
        self.data += b'\x1b\x40'

    def add_status_information(self):
        """Status information request (ESC i S)."""
        self.data += b'\x1b\x69\x53'

    def add_media_and_quality(self, lines: int, media_type: int | None, width: int, length: int, quality: bool):
        """Print information (ESC i z). Without a media type, the printer
        checks the width of its media only."""
        flags = 0x80 | (media_type is not None) << 1 | 1 << 2 | 1 << 3 | quality << 6
        self.data += b'\x1b\x69\x7a' + bytes([flags, media_type or 0, width & 0xFF, length & 0xFF])
        self.data += struct.pack('<L', lines) + bytes([0 if self.page_number == 0 else 1, 0])

    def add_autocut(self, on: bool):
        """Whether the printer cuts at all (ESC i M)."""
        if self.spec.cutting:
            self.data += b'\x1b\x69\x4d' + bytes([on << 6])

    def add_cut_every(self, labels: int):
        """Cut after this many labels (ESC i A)."""
        if self.spec.cutting:
            self.data += b'\x1b\x69\x41' + bytes([labels & 0xFF])

    def add_expanded_mode(self, cut_at_end: bool, dpi_600: bool, two_color: bool, half_cut: bool, chain: bool):
        """ESC i K: on P-touch printers half cuts between labels and chain
        printing, on QL printers the cut after the last label, 600 dpi and
        printing in red."""
        if not self.spec.expanded_mode:
            return
        if self.ptouch:
            flags = half_cut << 2 | (not chain) << 3 | dpi_600 << 5
        else:
            flags = cut_at_end << 3 | dpi_600 << 6 | two_color
        self.data += b'\x1b\x69\x4b' + bytes([flags])

    def add_margins(self, dots: int):
        """Feed before and after the label (ESC i d)."""
        self.data += b'\x1b\x69\x64' + struct.pack('<H', dots)

    def add_compression(self, on: bool):
        """TIFF (PackBits) compressed raster lines (M)."""
        if self.spec.compression_support:
            self.compression = on
            self.data += b'\x4d' + bytes([on << 1])

    def add_raster_data(self, image: Image.Image, second_image: Image.Image | None = None):
        """The lines of a binary image, with second_image the red ones."""
        if image.size[0] != self.pixel_width:
            raise RasterError(f'Wrong pixel width: {image.size[0]}, expected {self.pixel_width}')
        images = [image]
        if second_image:
            if image.size != second_image.size:
                raise RasterError(f"First and second image don't have the same dimensions: {image.size} vs {second_image.size}.")
            images.append(second_image)
        frames = [bytes(im.transpose(Image.Transpose.FLIP_LEFT_RIGHT).convert('1').tobytes(encoder_name='raw'))
                  for im in images]
        row_len = image.size[0] // 8
        data = bytearray()
        for start in range(0, len(frames[0]) - row_len + 1, row_len):
            for i, frame in enumerate(frames):
                row = frame[start:start + row_len]
                if self.compression:
                    row = packbits.encode(row)
                if self.ptouch:
                    data += b'\x47' + bytes([len(row) % 256, len(row) // 256])
                else:
                    data += (b'\x77\x01' if i == 0 else b'\x77\x02') if second_image else b'\x67\x00'
                    data += bytes([len(row)])
                data += row
        self.data += bytes(data)

    def add_print(self, last_page: bool = True):
        self.data += b'\x1a' if last_page else b'\x0c'
        # The following labels are not the first page of the job
        self.page_number += 1

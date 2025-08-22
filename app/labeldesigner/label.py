from enum import Enum, auto
from qrcode import QRCode, constants
from PIL import Image, ImageDraw, ImageFont
import logging

logger = logging.getLogger(__name__)

class LabelContent(Enum):
    TEXT_ONLY = auto()
    QRCODE_ONLY = auto()
    TEXT_QRCODE = auto()
    IMAGE_BW = auto()
    IMAGE_GRAYSCALE = auto()
    IMAGE_RED_BLACK = auto()
    IMAGE_COLORED = auto()


class LabelOrientation(Enum):
    STANDARD = auto()
    ROTATED = auto()


class LabelType(Enum):
    ENDLESS_LABEL = auto()
    DIE_CUT_LABEL = auto()
    ROUND_DIE_CUT_LABEL = auto()


class TextAlign(Enum):
    LEFT = 'left'
    CENTER = 'center'
    RIGHT = 'right'


class SimpleLabel:
    def _ensure_pil_image(self, img) -> Image.Image:
        """Ensure the image is a PIL.Image.Image instance."""
        if isinstance(img, Image.Image):
            return img
        # Try to convert PyPNGImage or other types to PIL.Image
        try:
            # Try to get bytes and open as PIL
            import io
            if hasattr(img, 'tobytes') and hasattr(img, 'size') and hasattr(img, 'mode'):
                return Image.frombytes(img.mode, img.size, img.tobytes())
            elif hasattr(img, 'to_pil_image'):
                return img.to_pil_image()
            elif hasattr(img, 'as_pil_image'):
                return img.as_pil_image()
            elif hasattr(img, 'save'):
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                buf.seek(0)
                return Image.open(buf)
        except Exception:
            pass
        raise TypeError("Unsupported image type for resizing. Please provide a PIL.Image.Image or compatible object.")
    qr_correction_mapping = {
        'L': constants.ERROR_CORRECT_L,
        'M': constants.ERROR_CORRECT_M,
        'Q': constants.ERROR_CORRECT_Q,
        'H': constants.ERROR_CORRECT_H
    }

    def __init__(
            self,
            width=0,
            height=0,
            label_content=LabelContent.TEXT_ONLY,
            label_orientation=LabelOrientation.STANDARD,
            label_type=LabelType.ENDLESS_LABEL,
            label_margin=(0, 0, 0, 0),  # Left, Right, Top, Bottom
            fore_color=(0, 0, 0),  # Red, Green, Blue
            text='',
            text_align=TextAlign.CENTER,
            qr_size=10,
            qr_correction='L',
            image_fit=True,
            image=None,
            font_path='',
            font_size=70,
            line_spacing=100):
        self._width = width
        self._height = height
        self.label_content = label_content
        self.label_orientation = label_orientation
        self.label_type = label_type
        self._label_margin = label_margin
        self._fore_color = fore_color
        self.text = text
        self._text_align = text_align
        self._qr_size = qr_size
        self.qr_correction = qr_correction
        self._image = image
        self._font_path = font_path
        self._font_size = font_size
        self._line_spacing = line_spacing
        self._image_fit = image_fit

    @property
    def label_content(self):
        return self._label_content

    @label_content.setter
    def label_content(self, value):
        self._label_content = value

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value

    @property
    def qr_correction(self):
        for key, val in self.qr_correction_mapping:
            if val == self._qr_correction:
                return key

    @qr_correction.setter
    def qr_correction(self, value):
        self._qr_correction = self.qr_correction_mapping.get(
            value, constants.ERROR_CORRECT_L)

    @property
    def label_orientation(self):
        return self._label_orientation

    @label_orientation.setter
    def label_orientation(self, value):
        self._label_orientation = value

    @property
    def label_type(self):
        return self._label_type

    @label_type.setter
    def label_type(self, value):
        self._label_type = value

    def generate(self, rotate = False):
        if self._label_content in (LabelContent.QRCODE_ONLY, LabelContent.TEXT_QRCODE):
            img = self._generate_qr()
        elif self._label_content in (LabelContent.IMAGE_BW, LabelContent.IMAGE_GRAYSCALE, LabelContent.IMAGE_RED_BLACK, LabelContent.IMAGE_COLORED):
            img = self._image
        else:
            img = None

        # Initialize dimensions
        width, height = self._width, self._height
        margin_left, margin_right, margin_top, margin_bottom = self._label_margin

        # Resize image to fit if image_fit is True
        if img is not None:
            # Ensure img is a PIL image
            pil_img = self._ensure_pil_image(img)

            # Resize image to fit if image_fit is True
            if self._image_fit:
                # Calculate the maximum allowed dimensions
                max_width = max(width - margin_left - margin_right, 1)
                max_height = max(height - margin_top - margin_bottom, 1)

                # Get image dimensions
                img_width, img_height = pil_img.size

                # Print the original image size
                logger.debug(f"Maximal allowed dimensions: {max_width}x{max_height} mm")
                logger.debug(f"Original image size: {img_width}x{img_height} px")

                # Resize the image to fit within the maximum dimensions
                scale = 1.0
                if self._label_orientation == LabelOrientation.STANDARD:
                    if self._label_type in (LabelType.ENDLESS_LABEL,):
                        # Only width is considered for endless label without rotation
                        scale = min(max_width / img_width, 1.0)
                    else:
                        # Both dimensions are considered for standard label
                        scale = min(max_width / img_width, max_height / img_height, 1.0)
                else:
                    if self._label_type in (LabelType.ENDLESS_LABEL,):
                        # Only height is considered for endless label without rotation
                        scale = min(max_height / img_height, 1.0)
                    else:
                        # Both dimensions are considered for standard label
                        scale = min(max_width / img_width, max_height / img_height, 1.0)
                logger.debug(f"Scaling image by factor: {scale}")

                # Resize the image
                new_size = (int(img_width * scale), int(img_height * scale))
                logger.debug(f"Resized image size: {new_size} px")
                pil_img = pil_img.resize(new_size, Image.Resampling.LANCZOS)
                # Update image dimensions
                img_width, img_height = pil_img.size
            else:
                # No resizing requested
                img_width, img_height = pil_img.size
            img = pil_img
        else:
            img_width, img_height = (0, 0)

        if self._label_content in (LabelContent.TEXT_ONLY, LabelContent.TEXT_QRCODE):
            textsize = self._get_text_size()
        else:
            textsize = (0, 0, 0, 0)

        # Adjust label size for endless label
        if self._label_orientation == LabelOrientation.STANDARD:
            if self._label_type in (LabelType.ENDLESS_LABEL,):
                height = img_height + textsize[3] - textsize[1] + margin_top + margin_bottom
        elif self._label_orientation == LabelOrientation.ROTATED:
            if self._label_type in (LabelType.ENDLESS_LABEL,):
                width = img_width + textsize[2] + margin_left + margin_right

        if self._label_orientation == LabelOrientation.STANDARD:
            if self._label_type in (LabelType.DIE_CUT_LABEL, LabelType.ROUND_DIE_CUT_LABEL):
                vertical_offset_text = (height - img_height - textsize[3])//2
                vertical_offset_text += (margin_top - margin_bottom)//2
            else:
                vertical_offset_text = margin_top

            vertical_offset_text += img_height
            horizontal_offset_text = max((width - textsize[2])//2, 0)
            horizontal_offset_image = (width - img_width)//2
            vertical_offset_image = margin_top

        elif self._label_orientation == LabelOrientation.ROTATED:
            vertical_offset_text = (height - textsize[3])//2
            vertical_offset_text += (margin_top - margin_bottom)//2
            if self._label_type in (LabelType.DIE_CUT_LABEL, LabelType.ROUND_DIE_CUT_LABEL):
                horizontal_offset_text = max((width - img_width - textsize[2])//2, 0)
            else:
                horizontal_offset_text = margin_left
            horizontal_offset_text += img_width
            horizontal_offset_image = margin_left
            vertical_offset_image = (height - img_height)//2

        text_offset = horizontal_offset_text, vertical_offset_text - textsize[1]
        image_offset = horizontal_offset_image, vertical_offset_image

        imgResult = Image.new('RGB', (int(width), int(height)), 'white')

        if img is not None:
            imgResult.paste(img, image_offset)

        if self._label_content in (LabelContent.TEXT_ONLY, LabelContent.TEXT_QRCODE):
            draw = ImageDraw.Draw(imgResult)
            draw.multiline_text(
                text_offset,
                self._prepare_text(self._text),
                self._fore_color,
                font=self._get_font(),
                align=self._text_align,
                spacing=int(self._font_size*((self._line_spacing - 100) / 100)))

        # Check if the image needs rotation (only applied when generating
        # preview images)
        preview_needs_rotation = (
            self._label_orientation == LabelOrientation.ROTATED and self._label_type not in (LabelType.DIE_CUT_LABEL, LabelType.ROUND_DIE_CUT_LABEL) or \
            self._label_orientation == LabelOrientation.STANDARD and self._label_type in (LabelType.DIE_CUT_LABEL, LabelType.ROUND_DIE_CUT_LABEL)
        )
        if rotate and preview_needs_rotation:
            imgResult = imgResult.rotate(-90, expand=True)

        return imgResult

    def _generate_qr(self):
        qr = QRCode(
            version=1,
            error_correction=self._qr_correction,
            box_size=self._qr_size,
            border=0,
        )
        qr.add_data(self._text.encode("utf-8-sig"))
        qr.make(fit=True)
        qr_img = qr.make_image(
            fill_color='red' if (255, 0, 0) == self._fore_color else 'black',
            back_color="white")
        return qr_img

    def _get_text_size(self):
        font = self._get_font()
        img = Image.new('L', (20, 20), 'white')
        draw = ImageDraw.Draw(img)
        return draw.multiline_textbbox(
            (0, 0),
            self._prepare_text(self._text),
            font=font,
            align=self._text_align,
            spacing=int(self._font_size*((self._line_spacing - 100) / 100)))

    @staticmethod
    def _prepare_text(text):
        # workaround for a bug in multiline_textsize()
        # when there are empty lines in the text:
        lines = []
        for line in text.split('\n'):
            if line == '':
                line = ' '
            lines.append(line)
        return '\n'.join(lines)

    def _get_font(self):
        return ImageFont.truetype(self._font_path, self._font_size)

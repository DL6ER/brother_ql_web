from enum import Enum, auto
from qrcode import QRCode, constants
from PIL import Image, ImageDraw, ImageFont
import logging
import barcode
from barcode.writer import ImageWriter

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
            barcode_type="QR",
            label_margin=(0, 0, 0, 0),  # Left, Right, Top, Bottom
            fore_color=(0, 0, 0),  # Red, Green, Blue
            text={},
            qr_size=10,
            qr_correction='L',
            image_fit=True,
            image=None,
            border_thickness=1,
            border_roundness=0,
            border_distance=(0, 0),
            border_color=(0, 0, 0)):
        self._width = width
        self._height = height
        self.label_content = label_content
        self.label_orientation = label_orientation
        self.label_type = label_type
        self.barcode_type = barcode_type
        self._label_margin = label_margin
        self._fore_color = fore_color
        self.text = text
        self._qr_size = qr_size
        self.qr_correction = qr_correction
        self._image = image
        self._image_fit = image_fit
        self._border_thickness = border_thickness
        self._border_roundness = border_roundness
        self._border_distance = border_distance
        self._border_color = border_color

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
            if self.barcode_type == "QR":
                img = self._generate_qr()
            else:
                img = self._generate_barcode()
                # Remove the first line of text as the barcode already contains it
                self.text = self.text[1:]
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
            img = self._ensure_pil_image(img)

            # Resize image to fit if image_fit is True
            if self._image_fit:
                # Calculate the maximum allowed dimensions
                max_width = max(width - margin_left - margin_right, 1)
                max_height = max(height - margin_top - margin_bottom, 1)

                # Get image dimensions
                img_width, img_height = img.size

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
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                # Update image dimensions
                img_width, img_height = img.size
            else:
                # No resizing requested
                img_width, img_height = img.size
        else:
            img_width, img_height = (0, 0)

        if self._label_content in (LabelContent.TEXT_ONLY, LabelContent.TEXT_QRCODE) and len(self.text) > 0:
            bboxes = self._draw_text(None, [])
            textsize = self._compute_bbox(bboxes)
        else:
            bboxes = []
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
                if self._label_content == LabelContent.TEXT_QRCODE:
                    # Slightly increase the margin to get some distance from the
                    # QR code
                    vertical_offset_text *= 1.25

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
                if self._label_content == LabelContent.TEXT_QRCODE:
                    # Slightly increase the margin to get some distance from the
                    # QR code
                    horizontal_offset_text *= 1.25

            horizontal_offset_text += img_width
            horizontal_offset_image = margin_left
            vertical_offset_image = (height - img_height)//2

        text_offset = horizontal_offset_text, vertical_offset_text
        image_offset = horizontal_offset_image, vertical_offset_image

        imgResult = Image.new('RGB', (int(width), int(height)), 'white')

        if img is not None:
            imgResult.paste(img, image_offset)

        if self._label_content in (LabelContent.TEXT_ONLY, LabelContent.TEXT_QRCODE) and len(self.text) > 0:
            self._draw_text(imgResult, bboxes, text_offset)

        # Check if the image needs rotation (only applied when generating
        # preview images)
        preview_needs_rotation = (
            self._label_orientation == LabelOrientation.ROTATED and self._label_type not in (LabelType.DIE_CUT_LABEL, LabelType.ROUND_DIE_CUT_LABEL) or \
            self._label_orientation == LabelOrientation.STANDARD and self._label_type in (LabelType.DIE_CUT_LABEL, LabelType.ROUND_DIE_CUT_LABEL)
        )
        if rotate and preview_needs_rotation:
            imgResult = imgResult.rotate(-90, expand=True)

        # Draw border if thickness > 0
        if self._border_thickness > 0:
            draw = ImageDraw.Draw(imgResult)
            # Calculate border rectangle (inside the image, respecting thickness)
            rect = [self._border_distance[0],
                    self._border_distance[1],
                    imgResult.width - self._border_distance[0],
                    imgResult.height - self._border_distance[1]]
            # Validity checks on rect:
            # - x1 >= x0
            # - y1 >= y0
            if rect[2] < rect[0] or rect[3] < rect[1]:
                raise ValueError("Invalid border rectangle")

            # Draw (rounded) rectangle
            draw.rounded_rectangle(rect, radius=self._border_roundness, outline=self._border_color, width=self._border_thickness)
        return imgResult

    def _generate_barcode(self):
        barcode_generator = barcode.get_barcode_class(self.barcode_type)
        my_barcode = barcode_generator(self.text[0]['text'], writer=ImageWriter())
        return my_barcode.render()

    def _generate_qr(self):
        qr = QRCode(
            version=1,
            error_correction=self._qr_correction,
            box_size=self._qr_size,
            border=0,
        )
        # Combine texts
        text = ""
        for line in self.text:
            text += line['text'] + "\n"
        qr.add_data(text.encode("utf-8-sig"))
        qr.make(fit=True)
        qr_img = qr.make_image(
            fill_color='red' if (255, 0, 0) == self._fore_color else 'black',
            back_color="white")
        return qr_img

    def _draw_text(self, img = None, bboxes = [], text_offset = (0, 0)):
        """
        Returns a list of bounding boxes for each line, so each line can use a different font.
        """
        do_draw = img is not None
        if not do_draw:
            img = Image.new('L', (20, 20), 'white')
        draw = ImageDraw.Draw(img)
        y = 0
        for i, line in enumerate(self.text):
            # Workaround for empty lines to not vanish
            text = line['text']
            if len(text) == 0:
                text = ' '
            
            # Calculate spacing
            spacing = int(int(line['font_size'])*((int(line['line_spacing']) - 100) / 100)) if 'line_spacing' in line else 0

            # Get font
            font = self._get_font(line['font_path'], line['font_size'])

            # Either calculate bbox or actually draw
            if not do_draw:
                bbox = draw.textbbox((0, y), text, font=font, align=line['align'])
                line_spacing = draw.textbbox((0, 0), "§", font)[3] + spacing
                bboxes.append((bbox, y))
                y += line_spacing + (spacing if i < len(self.text)-1 else 0)
            else:
                anchor = None
                align = line['align']

                # Left aligned text
                if align == "left":
                    anchor = "lt"
                    min_bbox_x = min(bbox[0][0] for bbox in bboxes) if len(bboxes) > 0 else 0
                    x = min_bbox_x + text_offset[0]
                    y = bboxes[i][1] + text_offset[1]

                # Center aligned text
                elif align == "center":
                    anchor = "mt"
                    min_bbox_x = min(bbox[0][0] for bbox in bboxes) if len(bboxes) > 0 else 0
                    max_bbox_x = max(bbox[0][2] for bbox in bboxes) if len(bboxes) > 0 else 0
                    x = (max_bbox_x - min_bbox_x) // 2 + min_bbox_x + text_offset[0]
                    y = bboxes[i][1] + text_offset[1]

                # Right aligned text
                elif align == "right":
                    anchor = "rt"
                    max_bbox_x = max(bbox[0][2] for bbox in bboxes) if len(bboxes) > 0 else 0
                    x = max_bbox_x + text_offset[0]
                    y = bboxes[i][1] + text_offset[1]

                # else: error
                else:
                    logger.error(f"Unsupported alignment: {line['align']}")
                draw.text((x, y), text, self._fore_color, font=font, anchor=anchor, align=align, spacing=spacing)

        # Return total bbox
        # each in form (x0, y0, x1, y1)
        return bboxes

    def _compute_bbox(self, bboxes):
        # Iterate over right margins of multiple text lines and find the maximum
        # width needed to fit all lines of text
        max_width = max(bbox[0][2] for bbox in bboxes)
        return (bboxes[0][0][0], bboxes[0][0][1], max_width, bboxes[-1][0][3] * 1.1)

    def _get_font(self, font_path, font_size):
        return ImageFont.truetype(font_path, int(font_size))

from .enums import LabelContent, LabelOrientation, LabelType
import os
import uuid
from qrcode import QRCode, constants
from PIL import Image, ImageDraw, ImageFont
import logging
import barcode
from barcode.writer import ImageWriter
import datetime
import re
import random
import string
import copy
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Constants
WARNING_TEXT_LENGTH = 500
DEFAULT_RANDOM_LENGTH = 64
DEFAULT_FONT_SIZE = 12
FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


class SimpleLabel:
    """
    Represents a label with text, image, barcode, and QR code support.
    Handles rendering, template processing, and layout.
    """
    QR_CORRECTION_MAPPING = {
        'M': constants.ERROR_CORRECT_M,
        'L': constants.ERROR_CORRECT_L,
        'H': constants.ERROR_CORRECT_H,
        'Q': constants.ERROR_CORRECT_Q
    }

    def __init__(
        self,
        width: int = 0,
        height: int = 0,
        label_content: LabelContent = LabelContent.TEXT_ONLY,
        label_orientation: LabelOrientation = LabelOrientation.STANDARD,
        label_type: LabelType = LabelType.ENDLESS_LABEL,
        barcode_type: str = "QR",
        label_margin: Tuple[int, int, int, int] = (0, 0, 0, 0),
        fore_color: Tuple[int, int, int] = (0, 0, 0),
        text: Optional[List[Dict[str, Any]]] = None,
        qr_size: int = 10,
        qr_correction: str = 'L',
        image_fit: bool = False,
        image_scaling_factor: float = 100.0,
        image: Optional[Union[Image.Image, None]] = None,
        border_thickness: int = 0,
        border_roundness: int = 0,
        border_distance: Tuple[int, int] = (0, 0),
        border_color: Tuple[int, int, int] = (0, 0, 0),
        timestamp: int = 0,
        counter: int = 0,
        red_support: bool = False
    ):
        """Initialize a SimpleLabel object."""
        # Input validation
        if width < 0 or height < 0:
            raise ValueError("Width and height must be non-negative.")
        if border_thickness < 0:
            raise ValueError("Border thickness must be non-negative.")
        if qr_size < 1:
            raise ValueError("QR size must be positive.")
        if image_scaling_factor <= 0:
            raise ValueError("Image scaling factor must be > 0.")
        self._width = width
        self._height = height
        self.label_content = label_content
        self.label_orientation = label_orientation
        self.label_type = label_type
        self.barcode_type = barcode_type
        self._label_margin = label_margin
        self._fore_color = fore_color
        self.text = None
        self.input_text = text
        self._qr_size = qr_size
        self.qr_correction = qr_correction
        self._image = image
        self._image_fit = image_fit
        self._image_scaling_factor = image_scaling_factor
        self._border_thickness = border_thickness
        self._border_roundness = border_roundness
        self._border_distance = border_distance
        self._border_color = border_color
        self._counter = counter
        self._timestamp = timestamp
        self._red_support = red_support

    @property
    def label_content(self):
        return self._label_content

    @label_content.setter
    def label_content(self, value):
        self._label_content = value

    def want_text(self, img: Optional[Image.Image]) -> bool:
        """Determine if text should be drawn on the label."""

        # If we are not drawing an image, we need to draw text or the label will
        # vanish
        if img is None:
            return True

        # If we want to draw only a code, suppress any text
        if self._label_content in (LabelContent.QRCODE_ONLY,):
            return False

        # If we are drawing an image, we want to draw text as well if there is
        # at least one line of text with non-empty content
        logger.debug(f"Text content: {self.text}")
        if self.text and len(self.text[0].get('text', '')) > 0:
            return True

        # Don't draw any text
        return False

    @property
    def need_image_text_distance(self):
        return self._label_content in (LabelContent.TEXT_QRCODE,
                                       LabelContent.IMAGE_BW,
                                       LabelContent.IMAGE_GRAYSCALE,
                                       LabelContent.IMAGE_RED_BLACK,
                                       LabelContent.IMAGE_COLORED)

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value

    @property
    def qr_correction(self):
        for key, val in self.QR_CORRECTION_MAPPING.items():
            if val == self._qr_correction:
                return key
        return 'L'

    @qr_correction.setter
    def qr_correction(self, value):
        self._qr_correction = self.QR_CORRECTION_MAPPING.get(value, constants.ERROR_CORRECT_L)

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

    def process_templates(self) -> None:
        """Process and replace templates in the text lines."""
        self.text = copy.deepcopy(self.input_text)
        for line in self.text:
            text_val = line.get('text', '')
            if len(text_val) > WARNING_TEXT_LENGTH:
                logger.warning(
                    f"Text line is very long (> {WARNING_TEXT_LENGTH} characters), "
                    "this may lead to long processing times.")

            # Replace {{counter[:<start>]}} with current label counter (<start> is
            # an optional offset defaulting to 1)
            def counter_replacer(match):
                offset = int(match.group(1)) if match.group(1) else 1
                return str(self._counter + offset)
            text_val = re.sub(r"\{\{counter(?:\:(\d+))?\}\}", counter_replacer, text_val)

            # Replace {{datetime:<format>}} with current datetime formatted as <format>
            def datetime_replacer(match):
                fmt = match.group(1)
                now = datetime.datetime.fromtimestamp(self._timestamp) if self._timestamp > 0 else datetime.datetime.now()
                return now.strftime(fmt)
            text_val = re.sub(r"\{\{datetime:([^}]+)\}\}", datetime_replacer, text_val)

            # Replace {{uuid}} with a new UUID
            if "{{uuid}}" in text_val:
                ui = uuid.UUID(int=random.getrandbits(128))
                text_val = text_val.replace("{{uuid}}", str(ui))

            # Replace {{short-uuid}} with a shortened UUID
            if "{{short-uuid}}" in text_val:
                ui = uuid.UUID(int=random.getrandbits(128))
                text_val = text_val.replace("{{short-uuid}}", str(ui)[:8])

            # Replace {{env:<var>}} with the value of the environment variable var
            def env_replacer(match):
                var_name = match.group(1)
                return os.getenv(var_name, "")
            text_val = re.sub(r"\{\{env:([^}]+)\}\}", env_replacer, text_val)

            # Replace {{random[:<len>][:shift]}} with random string of optional
            # length <len> and shifting instruction. ":s" is accepted as
            # shorthand for ":shift"
            def random_replacer(match):
                length = int(match.group(1)) if match.group(1) else DEFAULT_RANDOM_LENGTH
                if match.group(2):
                    line['shift'] = True
                return ''.join(random.choices(string.ascii_letters + string.digits + string.punctuation, k=length))
            text_val = re.sub(r"\{\{random(?:\:(\d+))?(?:\:(s(hift)?))?\}\}", random_replacer, text_val)

            line['text'] = text_val

    def generate(self, rotate: bool = False):
        # Process possible templates in the text
        self.process_templates()

        # Generate codes or load images if requested
        if self._label_content in (LabelContent.QRCODE_ONLY, LabelContent.TEXT_QRCODE):
            if self.barcode_type == "QR":
                img = self._generate_qr()
            else:
                img = self._generate_barcode()
                # Remove the first line of text as the barcode already contains
                # it
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
                        scale = max_width / img_width
                    else:
                        # Both dimensions are considered for standard label
                        scale = max_width / img_width, max_height / img_height
                else:
                    if self._label_type in (LabelType.ENDLESS_LABEL,):
                        # Only height is considered for endless label without rotation
                        scale = max_height / img_height
                    else:
                        # Both dimensions are considered for standard label
                        scale = max_width / img_width, max_height / img_height
                logger.debug(f"Scaling image by factor: {scale}")
                new_size = (int(img_width * scale), int(img_height * scale))
                logger.debug(f"Resized image size: {new_size} px")
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                # Update image dimensions
                img_width, img_height = img.size
            else:
                # Use image_scaling_factor if provided
                img_width, img_height = img.size
                scale = self._image_scaling_factor / 100.0
                logger.debug(f"Manual image scaling factor: {scale}")
                new_size = (int(img_width * scale), int(img_height * scale))
                logger.debug(f"Resized image size: {new_size} px")
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                img_width, img_height = img.size
        else:
            img_width, img_height = (0, 0)

        if self.want_text(img):
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
                if self.need_image_text_distance:
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
                if self.need_image_text_distance:
                    # Slightly increase the margin to get some distance from the
                    # QR code
                    horizontal_offset_text *= 1.25

            horizontal_offset_text += img_width
            horizontal_offset_image = margin_left
            vertical_offset_image = (height - img_height)//2

        text_offset = horizontal_offset_text, vertical_offset_text
        image_offset = horizontal_offset_image, vertical_offset_image

        # Ensure height and width are at least 1 to avoid generating valishing
        # preview images for empty inputs. PIL cannot store images with 0
        # width/height dimensions
        width = max(int(width), 1)
        height = max(int(height), 1)

        logger.debug(f"Image resolution: {int(width)} x {int(height)} px")
        imgResult = Image.new('RGB', (int(width), int(height)), 'white')

        if img is not None:
            imgResult.paste(img, image_offset)

        if self.want_text(img):
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
                    imgResult.width - self._border_distance[0] - 1,
                    imgResult.height - self._border_distance[1] - 1]
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
        value = self.text[0].get('text', '') if self.text and self.text[0].get('text', '') else ''
        my_barcode = barcode_generator(value, writer=ImageWriter())
        return my_barcode.render()

    def _generate_qr(self):
        qr = QRCode(
            version=1,
            error_correction=self._qr_correction,
            box_size=self._qr_size,
            border=0,
        )
        # Combine texts
        text = "\n".join(line.get('text', '') for line in self.text)
        qr.add_data(text.encode("utf-8-sig"))
        qr.make(fit=True)
        fill_color = 'red' if self._fore_color == (255, 0, 0) else 'black'
        qr_img = qr.make_image(fill_color=fill_color, back_color="white")
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

        # Iterate over lines of text
        for i, line in enumerate(self.text):
            # Calculate spacing
            spacing = int(int(line['size'])*((int(line['line_spacing']) - 100) / 100)) if 'line_spacing' in line else 0

            # Get font
            font = self._get_font(line['path'], line['size'])

            # Determine anchors
            anchor = None
            align = line.get('align', 'center')

            # Left aligned text
            if align == "left":
                anchor = "lt"
            # Center aligned text
            elif align == "center":
                anchor = "mt"
            # Right aligned text
            elif align == "right":
                anchor = "rt"
            # else: error
            else:
                raise ValueError(f"Unsupported alignment: {align}")

            red_font = 'color' in line and line['color'] == 'red'
#            if red_font and not self._red_support:
#                raise ValueError("Red font is not supported on this label")
            color = (255, 0, 0) if red_font else (0, 0, 0)

            # Draw TODO box if needed
            todo = line.get('todo', False)

            INVERT_LINE = 'inverted' in line and line['inverted']
            if do_draw and INVERT_LINE:
                # Draw a filled rectangle
                center_x = 0
                if anchor == "lt":
                    min_bbox_x = text_offset[0] + min(bbox[0][0] for bbox in bboxes)
                    max_bbox_x = text_offset[0] + bboxes[i][0][2] # max(bbox[0][2] for bbox in bboxes)
                elif anchor == "mt":
                    min_bbox_x = min(bbox[0][0] for bbox in bboxes)
                    max_bbox_x = max(bbox[0][2] for bbox in bboxes)
                    center_x = (min_bbox_x + max_bbox_x) // 2
                    min_bbox_x = text_offset[0] + center_x - (bboxes[i][0][2] - bboxes[i][0][0]) // 2
                    max_bbox_x = text_offset[0] + center_x + (bboxes[i][0][2] - bboxes[i][0][0]) // 2
                elif anchor == "rt":
                    max_bbox_x = text_offset[0] + max(bbox[0][2] for bbox in bboxes)
                    min_bbox_x = max_bbox_x - (bboxes[i][0][2] - bboxes[i][0][0])
                shift = 0.1 * int(line['size'])
                y_min = bboxes[i][0][1] + text_offset[1] - shift
                y_max = bboxes[i][0][3] + text_offset[1] - shift
                draw.rectangle((min_bbox_x, y_min, max_bbox_x, y_max), fill=color)
                # Overwrite font color with white on colored background
                color = (255, 255, 255)

            # Either calculate bbox or actually draw
            if not do_draw:
                # Get bbox of the text
                bbox = draw.textbbox((0, y), line['text'], font=font, align=align, anchor="lt")

                # Ensure consistent line heights for each line except the last
                # one (where it is not needed). We still need this when
                # inverting text to ensure the inversion box is large enough to
                # hold the entire text
                IS_LAST_LINE = i == len(self.text) - 1
                if not IS_LAST_LINE or INVERT_LINE:
                    # Some characters may need special height
                    all_characters = ''.join(string.ascii_letters + string.digits + string.punctuation)
                    Ag = draw.textbbox((0, y), all_characters, font, anchor="lt")
                    # Get bbox with width of text and dummy height
                    bbox = (bbox[0], Ag[1], bbox[2], Ag[3])
                bboxes.append((bbox, y))
                y += bbox[3] - bbox[1] + (spacing if i < len(self.text)-1 else 0)
            else:
                bbox = bboxes[i][0]
                y = bboxes[i][1] + text_offset[1]
                # Left aligned text
                if align == "left":
                    min_bbox_x = min(bbox[0][0] for bbox in bboxes) if len(bboxes) > 0 else 0
                    x = min_bbox_x + text_offset[0]
                # Center aligned text
                elif align == "center":
                    min_bbox_x = min(bbox[0][0] for bbox in bboxes) if len(bboxes) > 0 else 0
                    max_bbox_x = max(bbox[0][2] for bbox in bboxes) if len(bboxes) > 0 else 0
                    x = (max_bbox_x - min_bbox_x) // 2 + min_bbox_x + text_offset[0]
                # Right aligned text
                elif align == "right":
                    max_bbox_x = max(bbox[0][2] for bbox in bboxes) if len(bboxes) > 0 else 0
                    x = max_bbox_x + text_offset[0]

                # Draw TODO box if needed
                if todo:
                    todo_box_dimensions = 8 * int(line['size']) // 10
                    bbox = draw.textbbox((x - 1.2 * todo_box_dimensions, y), line['text'], font=font, align=align, anchor=anchor)
                    box_dimensions = bbox[0], y, bbox[0] + todo_box_dimensions, y + todo_box_dimensions
                    draw.rounded_rectangle(box_dimensions, radius=5, outline=color, width=max(1, todo_box_dimensions//10), fill=(255,255,255))

                draw.text((x, y), line['text'], color, font=font, anchor=anchor, align=align, spacing=spacing)

                # Shift text around if requested
                if "shift" in line:
                    def get_shift_amount():
                        return 0.03 * random.randint(5, 10) * int(line['size'])
                    for x_shift in [-get_shift_amount(), get_shift_amount()]:
                        for y_shift in [-get_shift_amount(), get_shift_amount()]:
                            new_random_text = ''.join(random.choices(string.ascii_letters + string.digits + string.punctuation, k=len(line['text'])))
                            draw.text((x + x_shift, y + y_shift), new_random_text, color, font=font, anchor=anchor, align=align, spacing=spacing)

        # Return total bbox
        # each in form (x0, y0, x1, y1)
        return bboxes

    def _compute_bbox(self, bboxes):
        # Edge case: No text
        if not bboxes:
            return (0, 0, 0, 0)
        # Iterate over right margins of multiple text lines and find the maximum
        # width needed to fit all lines of text
        max_width = max(bbox[0][2] for bbox in bboxes)
        return (bboxes[0][0][0], bboxes[0][0][1], max_width, bboxes[-1][0][3])

    def _get_font(self, font_path: str, size: int) -> ImageFont.FreeTypeFont:
        """Get a font object, using cache for performance."""
        key = (font_path, size)
        if key in FONT_CACHE:
            return FONT_CACHE[key]
        try:
            font = ImageFont.truetype(font_path, int(size))
            FONT_CACHE[key] = font
            return font
        except Exception as e:
            logger.error(f"Failed to load font '{font_path}' with size {size}: {e}")
            return ImageFont.load_default()

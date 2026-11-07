"""Printing on a VC-500W: one image per label, each a job of its own."""

import logging

from .. import devices
from . import protocol, status

logger = logging.getLogger(__name__)


def print_labels(queue, job) -> str:
    """Print the labels of job, (cut after it, mode, image) each. Labels not
    cut get a half cut, unless half cuts are off, and stay together on a
    strip."""
    target, port = status.target(queue.device_specifier)
    key = devices.lock_key(queue.device_specifier)
    try:
        for i, (cut_after, mode, image) in enumerate(job):
            cut = 'full' if cut_after else 'half' if queue.half_cut else 'none'

            def progress(stage, line, i=i, lines=image.height):
                state = {'stage': stage, 'label': i + 1, 'labels': len(job)}
                if stage == protocol.stage_name('PRINTING'):
                    state['percent'] = min(99, round(100 * line / lines))
                status.jobs[key] = state
            progress(protocol.stage_name('PROCESSING'), 0)
            label = f' (label {i + 1} of {len(job)})' if len(job) > 1 else ''
            try:
                error = protocol.print_image(target, image, mode, cut, port, progress)
            except protocol.VC500WError as e:
                # Its own words name paths of the printer, e.g. "please check /dev/tmp/lutgen.out"
                logger.warning('Printing on %s failed: %s', queue.device_specifier, e)
                return f'The printer refused the label{label}, the log of the server names the reason'
            except OSError as e:
                logger.warning('Printing on %s failed: %s', queue.device_specifier, e)
                reason = devices.unanswered_reason(e)
                return (f'Could not send the label to the printer{label}: {reason}' if reason
                        else f'Could not send the label to the printer{label}, the log of the server names the reason')
            if error:
                return f'{status.error_message(error)}{label}'
    finally:
        status.jobs.pop(key, None)
    return ''

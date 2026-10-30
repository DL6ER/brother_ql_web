"""Release, commit and build time of the running version, shown in the navbar."""

import os
import re
import subprocess
from datetime import datetime, timezone

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_build_info() -> dict:
    """Release, commit and build time set by the Docker image (RELEASE,
    GIT_COMMIT, BUILD_DATE), without them the commit of the git checkout the
    app runs from. Only release images have a release, e.g. v1.0."""
    release = os.environ.get('RELEASE', '').strip()
    if not re.fullmatch(r'v[0-9]+\.[0-9]+(\.[0-9]+)?(-[0-9A-Za-z.]+)?', release):
        release = ''
    commit = os.environ.get('GIT_COMMIT', '').strip().lower() or _checkout_commit()
    if not re.fullmatch(r'[0-9a-f]{7,40}', commit):
        commit = ''
    return {'release': release, 'commit': commit, 'date': _format_date(os.environ.get('BUILD_DATE', '').strip())}


def _checkout_commit() -> str:
    try:
        # The devcontainer mounts the checkout with another owner, which git
        # refuses unless the directory is marked as safe
        result = subprocess.run(['git', '-c', f'safe.directory={APP_ROOT}', 'rev-parse', 'HEAD'],
                                cwd=APP_ROOT, capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip()


def _format_date(value: str) -> str:
    """ISO 8601 time like 2026-10-29T02:37:05Z as 2026-10-29 02:37 UTC."""
    try:
        date = datetime.fromisoformat(value)
    except ValueError:
        return ''
    if date.tzinfo is not None:
        date = date.astimezone(timezone.utc)
    return date.strftime('%Y-%m-%d %H:%M UTC')

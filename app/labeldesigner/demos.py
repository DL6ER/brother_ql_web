"""Demo labels that come with the label designer, for inspiration and to
start from. They are saved labels like those of the repository, read-only,
in categories: each category is a folder in demos/, categories.json lists
them in the order they are shown. The images they use are in demos/images,
SOURCES.md there says where they come from."""
import json
import os

from werkzeug.security import safe_join

DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'demos')
IMAGE_DIR = os.path.join(DEMO_DIR, 'images')
# Labels that were replaced, by their former id: links made to them, e.g.
# for printing, lead to the label in their place
REPLACED = {
    f'names/{old}{lang}.json': f'names/{new}{lang}.json'
    for old, new in (('001-hello-my-name-is-62x100', '027-name-badge-62x100'), ('002-hello-my-name-is-62', '029-name-badge-62'),
                     ('003-hello-my-name-is-39x90', '005-name-and-role-39x90'), ('011-hello-my-name-is-29x90', '004-name-and-role-29x90'))
    for lang in ('', '-de')
}


def categories() -> list[dict]:
    """The categories with their id (the folder) and name."""
    with open(os.path.join(DEMO_DIR, 'categories.json'), encoding='utf-8') as fh:
        return json.load(fh)


def labels(category: str) -> list[tuple[str, dict]]:
    """The labels of a category as (id, label), the id is
    "<category>/<file name>"."""
    folder = os.path.join(DEMO_DIR, category)
    result = []
    for name in sorted(os.listdir(folder)):
        if name.endswith('.json'):
            with open(os.path.join(folder, name), encoding='utf-8') as fh:
                result.append((f'{category}/{name}', json.load(fh)))
    return result


def load(label_id: str) -> dict:
    """The label with the id, FileNotFoundError if there is none."""
    if not isinstance(label_id, str):
        raise FileNotFoundError(label_id)
    label_id = REPLACED.get(label_id, label_id)
    category, _, name = label_id.partition('/')
    if category not in {c['id'] for c in categories()} or '/' in name or not name.endswith('.json'):
        raise FileNotFoundError(label_id)
    path = safe_join(DEMO_DIR, category, name)
    if path is None or not os.path.isfile(path):
        raise FileNotFoundError(label_id)
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)

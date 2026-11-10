"""Build the project website from README.md: python website/build.py <out>.

The page is the README in website/template.html, with the files it shows
copied next to it and links to other files of the repository pointing to
GitHub. A link to a file that does not exist stops the build."""

import html
import re
import shutil
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / 'website'
REPO = 'https://github.com/DL6ER/labelburg'
# Copied as they are, at their path in the repository
ASSETS = ('app/static/designer/archivo.woff2', 'app/static/images/app/icon.svg',
          'app/static/images/app/apple-touch-icon.png', 'app/static/images/app/icon-192.png')


def _local(url: str) -> bool:
    return not re.match(r'[a-z][a-z0-9+.-]*:|#|//', url)


def _rewrite(page: str, out: Path) -> str:
    """Images of the repository copied next to the page, links to its
    other files pointing to GitHub."""
    def attr(m):
        name, url = m.group(1), html.unescape(m.group(2))
        if not _local(url):
            return m.group(0)
        path, _, anchor = url.partition('#')
        path = path.removeprefix('./')
        target = ROOT / path
        if not target.exists():
            sys.exit(f'README.md links to {path}, which does not exist')
        if name == 'src':
            (out / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, out / path)
            return f'src="{html.escape(path)}"'
        url = f'{REPO}/{"tree" if target.is_dir() else "blob"}/master/{path}' + (f'#{anchor}' if anchor else '')
        return f'href="{html.escape(url)}"'
    return re.sub(r'\b(href|src)="([^"]*)"', attr, page)


def _with_dark(page: str) -> str:
    """Screenshots with a copy named <name>-dark.png next to them shown as
    that copy in dark mode."""
    def img(m):
        src = html.unescape(m.group(1)).removeprefix('./')
        dark = re.sub(r'\.png$', '-dark.png', src)
        if dark == src or not (ROOT / dark).exists():
            return m.group(0)
        tag = m.group(0)
        return (tag.replace('<img ', '<img class="light-only" ', 1)
                + tag.replace('<img ', '<img class="dark-only" loading="lazy" ', 1).replace(m.group(1), html.escape(dark)))
    return re.sub(r'<img [^>]*\bsrc="([^"]+)"[^>]*>', img, page)


def build(out: Path):
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    head, _, body = readme.partition('\n## ')
    # The title and the badges are in the template, the rest of the head
    # (what it is and the screenshot) leads the page
    intro = '\n'.join(line for line in head.splitlines() if not line.startswith(('# ', '[![')))
    # Its first sentence is the tagline under the title
    tagline, _, intro = intro.strip().partition('. ')
    md = markdown.Markdown(extensions=['tables', 'fenced_code', 'toc'])
    intro_html = md.convert(intro)
    md.reset()
    body_html = md.convert('## ' + body)
    # Wide tables scroll on their own on phones
    body_html = body_html.replace('<table>', '<div class="table"><table>').replace('</table>', '</table></div>')
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # Its first paragraph describes the project to search engines
    about = re.sub(r'<[^>]+>', '', re.search(r'<p>(.*?)</p>', intro_html, re.S).group(1))
    page = (SITE / 'template.html').read_text(encoding='utf-8')
    page = (page.replace('{{tagline}}', html.escape(tagline))
            .replace('{{about}}', html.escape(f'{tagline}. {html.unescape(about)}', quote=True))
            .replace('{{intro}}', _rewrite(_with_dark(intro_html), out))
            .replace('{{body}}', _rewrite(_with_dark(body_html), out)))
    (out / 'index.html').write_text(page, encoding='utf-8')
    shutil.copyfile(SITE / 'site.css', out / 'site.css')
    for asset in ASSETS:
        (out / asset).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / asset, out / asset)
    # No Jekyll on GitHub Pages
    (out / '.nojekyll').touch()


if __name__ == '__main__':
    build(Path(sys.argv[1] if len(sys.argv) > 1 else '_site').resolve())

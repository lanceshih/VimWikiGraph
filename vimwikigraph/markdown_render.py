import re
from pathlib import Path

import markdown

_WIKI_HEADER_RE = re.compile(r'^(=+)\s*(.+?)\s*=+\s*$', re.MULTILINE)
_WIKI_PIPE_LINK_RE = re.compile(r'\[\[([^\]|]+)\|([^\]]+)\]\]')
_WIKI_LINK_RE = re.compile(r'\[\[([^\]]+)\]\]')

MD_EXTENSIONS = ['fenced_code', 'tables', 'nl2br']


def _wiki_to_md(text):
    text = _WIKI_HEADER_RE.sub(lambda m: '#' * len(m.group(1)) + ' ' + m.group(2), text)
    text = _WIKI_PIPE_LINK_RE.sub(lambda m: f'[{m.group(2)}]({m.group(1)})', text)
    text = _WIKI_LINK_RE.sub(lambda m: f'[{m.group(1)}]({m.group(1)})', text)
    return text


def render_node_text(node, text):
    if Path(node).suffix != '.md':
        text = _wiki_to_md(text)
    return markdown.markdown(text, extensions=MD_EXTENSIONS)

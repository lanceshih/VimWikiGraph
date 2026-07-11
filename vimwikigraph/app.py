import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request
from flask_visjs import Network, VisJS4

from .markdown_render import render_node_text
from .vimwikigraph import VimwikiGraph

app = Flask(__name__)
if not os.environ.get('VIMWIKIGRAPH_CONFIG'):
    os.environ['VIMWIKIGRAPH_CONFIG'] = str(Path(__file__).parent.parent / 'vimwikigraph.cfg')
app.config.from_envvar('VIMWIKIGRAPH_CONFIG')
VisJS4().init_app(app)


class State:
    instance = None

    def __init__(self):
        self.vimwikigraphdir = os.environ.get('VIMWIKIDIR', '')
        if not self.vimwikigraphdir:
            raise ValueError('VIMWIKIDIR environment variable is not set')
        self.vimwikigraph = VimwikiGraph(
            self.vimwikigraphdir,
            app.config.get('FILE_EXTENSIONS', ['wiki']),
            exclude_pattern=app.config.get('EXCLUDE_PATTERN', ''),
            tag_pattern=app.config.get('TAG_PATTERN', r'^:((\w+:)+)'),
        )
        self.reset_form()
        self.n_tags = app.config.get('N_TAGS', 30)
        self.SEP = app.config.get('SEPARATOR', ';')

    def reset_form(self):
        self.filter = app.config.get('DEFAULT_FILTER', [])
        self.invert_filter = app.config.get('DEFAULT_INVERT_FILTER', False)
        self.highlight = app.config.get('DEFAULT_HIGHLIGHT', [])
        self.filename_filter = app.config.get('DEFAULT_FILE_FILTER', [])
        self.invert_filename_filter = app.config.get('DEFAULT_INVERT_FILE_FILTER', False)
        self.remove_leaves_depth = app.config.get('DEFAULT_REMOVE_LEAVES_DEPTH', 0)
        self.add_leaves_depth = app.config.get('DEFAULT_ADD_LEAVES_DEPTH', 0)
        date_attribute = app.config.get('DATE_ATTRIBUTE', 'mtime')
        min_ts, max_ts = self.vimwikigraph.get_date_range(date_attribute)
        self.date_min = datetime.fromtimestamp(min_ts).strftime('%Y-%m-%d') if min_ts else ''
        self.date_max = datetime.fromtimestamp(max_ts).strftime('%Y-%m-%d') if max_ts else ''

    @staticmethod
    def get_instance():
        if State.instance is None:
            State.instance = State()
        return State.instance

    def set_form(self, filter, invert_filter, filename_filter, invert_file_filter, highlight, remove_leaves_depth=0, add_leaves_depth=0, date_min='', date_max=''):
        self.filter = filter.split(self.SEP)
        self.invert_filter = invert_filter
        self.filename_filter = filename_filter.split(self.SEP)
        self.invert_filename_filter = invert_file_filter
        self.highlight = highlight.split(self.SEP)
        self.remove_leaves_depth = int(remove_leaves_depth)
        self.add_leaves_depth = int(add_leaves_depth)
        self.date_min = date_min
        self.date_max = date_max

    def get_graph(self):
        return self.vimwikigraph

    def __str__(self):
        msg = "Filter"
        if self.invert_filter:
            msg += "[X]"
        msg += f": {self.filter}\nFilename filter"
        if self.invert_filename_filter:
            msg += "[X]"
        msg += f": {self.filename_filter}\nHighlight: {self.highlight}"
        return msg


class EmbedState:
    _retriever = None

    @classmethod
    def get_retriever(cls):
        if cls._retriever is None:
            from .embed.retriever import Retriever
            cfg = {
                "paths":     {"db_path": str(Path(app.config['EMBED_DB_PATH']).expanduser())},
                "embedding": {"model":  app.config['EMBED_MODEL'],
                              "device": app.config['EMBED_DEVICE']},
            }
            cls._retriever = Retriever(cfg)
        return cls._retriever


@app.route('/', methods=['GET', 'POST'])
def route_index():
    if request.method == 'GET':
        state = State.get_instance()
        rendered = render_template(
            'index.html',
            filter_value=state.SEP.join(state.filter),
            invert_filter_value=state.invert_filter,
            filename_value=state.SEP.join(state.filename_filter),
            invert_filename_value=state.invert_filename_filter,
            highlight_value=state.SEP.join(state.highlight),
            remove_leaves_value=state.remove_leaves_depth,
            add_leaves_value=state.add_leaves_depth,
            date_min_value=state.date_min,
            date_max_value=state.date_max,
            sep=state.SEP,
            embed_mode_default=app.config.get('EMBED_DEFAULT_MODE', False),
        )
        return rendered
    if request.method == 'POST':
        state = State.get_instance()
        state.set_form(
            request.form['inptFilter'],
            'inptInvertFilter' in request.form,
            request.form['inptFileFilter'],
            'inptInvertFileFilter' in request.form,
            request.form['inptHighlight'],
            request.form.get('inptRemoveLeaves', 0),
            request.form.get('inptAddLeaves', 0),
            request.form.get('inptDateMin', ''),
            request.form.get('inptDateMax', ''),
        )
        rendered = render_template(
            'index.html',
            filter_value=state.SEP.join(state.filter),
            invert_filter_value=state.invert_filter,
            filename_value=state.SEP.join(state.filename_filter),
            invert_filename_value=state.invert_filename_filter,
            highlight_value=state.SEP.join(state.highlight),
            remove_leaves_value=state.remove_leaves_depth,
            add_leaves_value=state.add_leaves_depth,
            date_min_value=state.date_min,
            date_max_value=state.date_max,
            sep=state.SEP,
        )
        return rendered


@app.route('/network')
def network_json():
    state = State.get_instance()
    graph = state.get_graph().reset_graph()
    if state.filename_filter != ['']:
        graph = graph.filter_filenames(state.filename_filter, invert=state.invert_filename_filter)
    if state.filter != ['']:
        graph = graph.filter_nodes(state.filter, invert=state.invert_filter)
    if state.date_min or state.date_max:
        graph = graph.filter_by_date(state.date_min, state.date_max, attribute=app.config.get('DATE_ATTRIBUTE', 'mtime'))
    if state.add_leaves_depth > 0:
        graph = graph.add_leaves(state.add_leaves_depth)
    if state.remove_leaves_depth > 0:
        graph = graph.remove_leaves(state.remove_leaves_depth)
    if state.highlight != ['']:
        attributes = ['color', 'style']
        values = ['red', 'filled']
        graph = graph.add_attribute_by_regex(state.highlight, attributes, values)
    network = Network(
        neighborhood_highlight=True,
        filter_menu=True,
        cdn_resources='remote',
    )
    network.from_nx(graph.graph)
    return network.to_json(max_depth=3)


@app.route('/node', methods=['POST'])
def node_json():
    state = State.get_instance()
    if request.json and 'node' in request.json:
        node = request.json['node']
        lines = ''.join(state.vimwikigraph.lines[node])
        embed_query = request.json.get('embed_query')
        if embed_query:
            top_k = app.config.get('EMBED_TOP_K', 50)
            results = EmbedState.get_retriever().query(embed_query, top_k=top_k)
            headings = {
                r['metadata']['heading_path'].split(' > ')[-1]
                for r in results
                if r['metadata'].get('source_file') == node
                and r['metadata'].get('heading_path')
            }
            for heading in headings:
                lines = re.sub(
                    r'^(={1,6}\s+' + re.escape(heading) + r'\s+=+[^\n]*)',
                    r'<span style="background:orange">\g<0></span>',
                    lines,
                    flags=re.MULTILINE | re.IGNORECASE,
                )
        else:
            highlight_regex = request.json.get('highlight')
            highlights = highlight_regex.split(state.SEP) if highlight_regex is not None else state.highlight
            for highlight in highlights:
                lines = re.sub(highlight, r'<span style="background:red">\g<0></span>', lines, flags=re.IGNORECASE)
        lines = render_node_text(node, lines)
    else:
        lines = []
    return json.dumps({'text': lines})


@app.route('/reload', methods=['POST'])
def reload():
    state = State.get_instance()
    state.vimwikigraph.reload_graph()
    state.set_form(
        request.form['inptFilter'],
        'inptInvertFilter' in request.form,
        request.form['inptFileFilter'],
        'inptInvertFileFilter' in request.form,
        request.form['inptHighlight'],
        request.form.get('inptRemoveLeaves', 0),
        request.form.get('inptAddLeaves', 0),
    )
    rendered = render_template(
        'index.html',
        filter_value=state.SEP.join(state.filter),
        invert_filter_value=state.invert_filter,
        filename_value=state.SEP.join(state.filename_filter),
        invert_filename_value=state.invert_filename_filter,
        highlight_value=state.SEP.join(state.highlight),
        remove_leaves_value=state.remove_leaves_depth,
        add_leaves_value=state.add_leaves_depth,
        sep=state.SEP,
    )
    return rendered


@app.route('/reset', methods=['GET'])
def reset():
    state = State.get_instance()
    state.reset_form()
    return json.dumps({
        'filter_value': state.SEP.join(state.filter),
        'invert_filter_value': state.invert_filter,
        'filename_value': state.SEP.join(state.filename_filter),
        'invert_filename_value': state.invert_filename_filter,
        'highlight_value': state.highlight,
        'remove_leaves_value': state.remove_leaves_depth,
        'add_leaves_value': state.add_leaves_depth,
        'date_min_value': state.date_min,
        'date_max_value': state.date_max,
    })


@app.route('/meta')
def meta():
    state = State.get_instance()
    date_attribute = app.config.get('DATE_ATTRIBUTE', 'mtime')
    min_ts, max_ts = state.vimwikigraph.get_date_range(date_attribute)
    return json.dumps({
        'date_min': datetime.fromtimestamp(min_ts).strftime('%Y-%m-%d') if min_ts else '',
        'date_max': datetime.fromtimestamp(max_ts).strftime('%Y-%m-%d') if max_ts else '',
    })


@app.route('/highlight', methods=['POST'])
def highlight():
    state = State.get_instance()
    regex = request.json.get('regex', '') if request.json else ''
    nodes = []
    try:
        for node, lines in state.vimwikigraph.lines.items():
            if regex and re.search(regex, ''.join(lines), re.IGNORECASE):
                nodes.append({'id': node, 'color': 'red'})
            else:
                nodes.append({'id': node, 'color': None})
    except re.error:
        for node in state.vimwikigraph.lines:
            nodes.append({'id': node, 'color': None})
    return json.dumps({'nodes': nodes})


def _embed_cfg(db_path: Path) -> dict:
    return {
        'paths': {
            'db_path': str(db_path),
            'notes_path': State.get_instance().vimwikigraphdir,
        },
        'embedding': {
            'model':  app.config['EMBED_MODEL'],
            'device': app.config['EMBED_DEVICE'],
        },
        'chunker': {
            'max_tokens':           app.config.get('EMBED_CHUNKER_MAX_TOKENS', 512),
            'overlap_tokens':       app.config.get('EMBED_CHUNKER_OVERLAP_TOKENS', 64),
            'parent_context_tokens': app.config.get('EMBED_CHUNKER_PARENT_CONTEXT_TOKENS', 128),
            'min_chunk_tokens':     app.config.get('EMBED_CHUNKER_MIN_CHUNK_TOKENS', 32),
        },
    }


@app.route('/embed_index_status', methods=['GET'])
def embed_index_status():
    db_path = Path(app.config['EMBED_DB_PATH']).expanduser()
    timestamp_file = db_path / '.embed-last-built'
    if not db_path.exists():
        return json.dumps({'exists': False, 'chunk_count': 0, 'last_built': None})
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(db_path))
        collection = client.get_collection(name='notes')
        count = collection.count()
        last_built = timestamp_file.read_text().strip() if timestamp_file.exists() else None
        return json.dumps({'exists': True, 'chunk_count': count, 'last_built': last_built})
    except Exception as e:
        return json.dumps({'exists': False, 'chunk_count': 0, 'last_built': None, 'error': str(e)})


@app.route('/embed_rebuild', methods=['POST'])
def embed_rebuild():
    from datetime import datetime, timezone

    from .embed.ingest import build_index
    db_path = Path(app.config['EMBED_DB_PATH']).expanduser()
    build_index(_embed_cfg(db_path))
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    (db_path / '.embed-last-built').write_text(timestamp)
    EmbedState._retriever = None  # reconnect to rebuilt collection
    return json.dumps({'last_built': timestamp})


@app.route('/embed_highlight', methods=['POST'])
def embed_highlight():
    data = request.json or {}
    query = data.get('query', '')
    threshold = float(data.get('threshold', 0.5))
    top_k = app.config.get('EMBED_TOP_K', 20)

    state = State.get_instance()
    retriever = EmbedState.get_retriever()
    results = retriever.query(query, top_k=top_k)
    hits = {r['metadata']['source_file'] for r in results if r['score'] >= threshold}

    nodes = [
        {'id': node, 'color': 'red' if node in hits else None}
        for node in state.vimwikigraph.lines
    ]
    return json.dumps({'nodes': nodes})


@app.route('/tags', methods=['GET'])
def tags():
    state = State.get_instance()
    count_dict = state.vimwikigraph.get_tags()
    return json.dumps({'tags': list(count_dict.keys())[:state.n_tags]})


def create_app():
    if app.config.get('EMBED_DEFAULT_MODE', False):
        threading.Thread(target=EmbedState.get_retriever, daemon=True).start()
    return app

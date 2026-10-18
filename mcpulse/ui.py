"""Python-rendered local dashboard. No JavaScript or frontend dependencies."""
from datetime import datetime
import html
import json
from http.cookies import SimpleCookie, CookieError
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from .core import RequestError
from .replies import valid_pin


def esc(value):
    return html.escape(str(value), quote=True)


CSS = """
:root{color-scheme:light;--bg:#f5f6f2;--ink:#20342f;--muted:#667970;--line:#dde4dc;--green:#146c4c;--soft:#e8f1e8;--white:#fff;--orange:#9a5915}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}a{color:inherit;text-decoration:none}button,input,select,textarea{font:inherit}button,a,summary,input,textarea,select{outline-offset:5px}a:focus-visible,button:focus-visible{outline:3px solid #438c63}.shell{display:grid;grid-template-columns:226px minmax(0,1fr);min-height:100vh}.sidebar{position:sticky;top:0;align-self:start;height:100vh;height:100dvh;overflow-y:auto;background:#eef1e9;border-right:1px solid var(--line);padding:32px 22px;display:flex;flex-direction:column;gap:38px}.sidebar>*{flex-shrink:0}.brand{font-size:25px;font-weight:750;letter-spacing:-1px}.brand em{color:var(--green);font-style:normal}.brand small{font-size:10px;background:#dce7d7;color:#426b46;letter-spacing:1.5px;padding:4px 6px;border-radius:4px;margin-left:8px;vertical-align:middle}.nav-label,.eyebrow{font-size:11px;font-weight:700;letter-spacing:1.8px;text-transform:uppercase;color:var(--muted)}nav{display:grid;gap:7px;margin-top:14px}nav a{padding:11px 13px;border-radius:7px;display:flex;align-items:center;gap:12px;color:#52665a}nav a.active{background:#dce8d9;color:#224e37;font-weight:650}nav a:hover{background:#e2eadf}.nav-icon{font-size:16px;width:20px;text-align:center}.sidebar-note{margin-top:auto;padding-top:26px;border-top:1px solid #d2dcce;font-size:12px;color:var(--muted)}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#41865a;margin-right:6px}.workspace{min-width:0}.topbar{height:83px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding:0 44px;background:#fafbf7;gap:16px}.crumb{color:var(--muted);font-size:13px}.crumb strong{color:var(--ink);font-weight:500}.top-actions,.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.content{max-width:1400px;margin:auto;padding:36px 44px}.hero{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:28px}h1{font-size:34px;letter-spacing:-1.25px;line-height:1.2;font-weight:600;margin:8px 0 10px}h2{font-size:18px;letter-spacing:-.35px;margin:0;font-weight:650}h3{font-size:16px;margin:0;font-weight:650}p{margin:8px 0}.muted{color:var(--muted)}.small{font-size:12px}.button,button{display:inline-block;background:var(--green);border:1px solid var(--green);color:white;padding:10px 16px;border-radius:7px;font-weight:600;font-size:13px;cursor:pointer;text-align:center}.secondary{background:white;border-color:#d4ded2;color:var(--ink)}.danger{background:#fff5f1;color:#a44b36;border-color:#e9cfc6}.subtle{background:transparent;color:var(--muted);border-color:var(--line);padding:6px 10px;font-weight:500}button:hover,.button:hover{filter:brightness(.95)}button:disabled{opacity:.45;cursor:not-allowed}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-bottom:28px}.metric{background:white;border:1px solid var(--line);border-radius:10px;padding:19px 22px}.metric .value{font-size:32px;line-height:1.3;letter-spacing:-1px;margin:8px 0}.metric small{color:var(--muted)}.split{display:grid;grid-template-columns:minmax(0,1fr) 286px;gap:24px}.panel{background:white;border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-bottom:22px}.panel-head{padding:20px 22px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px}.panel-body{padding:20px 22px}.count{background:var(--soft);border-radius:5px;padding:2px 7px;font-size:12px;margin-left:7px;color:#366a43}.request{padding:22px;border-bottom:1px solid var(--line)}.request:last-child{border:0}.request-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}.request-meta{font-size:12px;color:var(--muted);margin-top:4px;overflow-wrap:anywhere}.tag{display:inline-block;font-size:10px;text-transform:uppercase;letter-spacing:.6px;font-weight:700;border-radius:4px;padding:4px 7px;white-space:nowrap;background:#edf2e9;color:#48704a}.tag.high{background:#fff1df;color:#9a5915}.tag.question{background:#eef0fa;color:#5a6195}.tag.finished{background:#e7f2ef;color:#337560}.request-preview{background:#f7f8f4;border:1px solid #e9ede5;border-radius:6px;padding:13px 15px;margin:12px 0 16px;font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}.actions{margin-top:18px}.request-foot{display:flex;align-items:center;justify-content:space-between;gap:10px}.session,.channel{padding:16px 22px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.session:last-child,.channel:last-child{border:0}.channel{display:flex;align-items:center;justify-content:space-between;gap:12px}.presence{display:grid;gap:12px}.presence select{width:100%}.presence button{width:100%}.notice{background:#eef4e8;border:1px solid #dce6d4;border-radius:8px;padding:13px 16px;color:#47633b;font-size:13px;margin-bottom:22px}.notice.warning{background:#fff6e9;border-color:#eedfc8;color:#855b24}.empty{padding:40px 22px;text-align:center;color:var(--muted)}.empty strong{display:block;color:var(--ink);font-size:18px;margin-bottom:8px}input,select,textarea{border:1px solid #cbd7c9;border-radius:6px;padding:10px 12px;background:white;color:var(--ink);max-width:100%}textarea{width:100%;resize:vertical}label{display:block;font-size:13px;font-weight:600;margin:16px 0 8px}input[type=password]{width:100%}input[type=checkbox],input[type=radio]{width:auto;margin-right:7px}fieldset{border:0;padding:0;margin:22px 0}legend{font-size:16px;font-weight:650}.choice{font-weight:400;padding:11px;background:#f5f7f1;border:1px solid var(--line);border-radius:6px;margin:8px 0}table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}th{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);background:#fafbf8;font-weight:600}td,th{padding:13px 22px;border-bottom:1px solid var(--line)}tr:last-child td{border-bottom:0}.table-wrap{overflow:auto}code{font:12px ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}.toolbar input{flex:1;min-width:140px}.detail{max-width:850px;margin:auto}.detail .request-preview{font-size:14px}.detail dl{display:grid;grid-template-columns:110px minmax(0,1fr);gap:8px;font-size:13px}.detail dt{color:var(--muted)}.detail dd{margin:0;overflow-wrap:anywhere}details{margin-top:18px}summary{cursor:pointer;color:var(--muted);font-size:13px}.footer{color:#829080;font-size:11px;padding-top:8px;display:flex;justify-content:space-between;gap:12px}.standalone{max-width:850px;margin:50px auto;padding:24px}.standalone .brand{display:block;margin-bottom:28px}.option{padding:8px 12px;background:#f6f7f2;border-radius:5px;margin-top:7px;font-size:13px}.skip{position:absolute;left:-10000px}.skip:focus{left:20px;top:10px;background:white;padding:10px;z-index:2}
@media(min-width:1600px){.content{padding-top:48px}}@media(max-width:1100px){.split{grid-template-columns:minmax(0,1fr)}.aside{display:grid;grid-template-columns:1fr 1fr;gap:20px}.content{padding:28px}.topbar{padding:0 28px}.shell{grid-template-columns:190px minmax(0,1fr)}.sidebar{padding:28px 14px}}@media(max-width:700px){.shell{display:block}.sidebar{position:static;height:auto;overflow:visible;padding:16px;border-right:0;border-bottom:1px solid var(--line);gap:10px}.sidebar-note,.nav-label{display:none}.sidebar nav{display:flex;overflow:auto;margin:0;gap:3px}.sidebar nav a{padding:8px 10px;white-space:nowrap;font-size:12px}.nav-icon{display:none}.brand{font-size:22px}.topbar{height:auto;padding:14px 18px}.content{padding:22px 16px}.hero{align-items:flex-start}h1{font-size:28px}.hero>.button{white-space:nowrap;padding:8px 11px}.metrics{gap:8px}.metric{padding:12px}.metric .value{font-size:26px}.metric small{font-size:10px}.metric .muted{font-size:11px}.aside{display:block}.request,.panel-body{padding:16px}.panel-head{padding:16px}.request-head{gap:8px}.actions{margin-top:18px}.request-foot{align-items:flex-end}.footer{display:block}.standalone{margin:20px auto;padding:16px}td,th{padding:12px}.crumb{font-size:11px}.top-actions .small{display:none}}
"""


def page(title, body):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + esc(title) + ' · MCPulse</title><style>' + CSS + '</style></head><body>' + body + '</body></html>')


def standalone(title, body):
    return page(title, '<main class="standalone"><div class="brand">MC<em>Pulse</em></div>'
                '<h1>' + esc(title) + '</h1>' + body + '</main>')


def age(stamp):
    seconds = max(0, int(time.time() - stamp))
    return 'just now' if seconds < 60 else f'{seconds // 60}m ago' if seconds < 3600 else f'{seconds // 3600}h ago'


def project_name(path):
    return str(path).rstrip('/\\').replace('\\', '/').rsplit('/', 1)[-1] or 'Project'


def request_title(card):
    return {'permission': card['tool'] + ' needs approval', 'question': card.get('agent', 'Agent') + ' has a question',
            'finished': 'Ready for your next step', 'notification': 'Session update'}.get(card['kind'], 'Review request')


def input_text(card):
    import json
    try:
        value = json.loads(card['input'])
        if isinstance(value, dict):
            for key in ('command', 'message'):
                if key in value:
                    return str(value[key])
    except (ValueError, TypeError):
        pass
    return card['input']


def card_details(card, *, show_questions=True):
    body = '<dl><dt>Project</dt><dd>' + esc(card['project']) + '</dd><dt>Agent</dt><dd>' + esc(card.get('agent', 'Unknown agent')) + '</dd><dt>Session ID</dt><dd>' + esc(card['session']) + '</dd></dl>'
    raw = '<pre class="request-preview">' + esc(card['input']) + '</pre>'
    body += '<details><summary>Full request</summary>' + raw + '</details>' if card.get('questions') else raw
    for question in card.get('questions', []) if show_questions else []:
        body += '<h3>' + esc(question['question']) + '</h3>'
        for index, option in enumerate(question.get('options', []), 1):
            body += '<div class="option">' + str(index) + '. ' + esc(option['label']) + '</div>'
        if question.get('multiSelect'):
            body += '<p class="muted small">Multiple choices allowed; separate their numbers with commas.</p>'
    if card.get('context'):
        import json
        body += '<details><summary>More context</summary><pre class="request-preview">' + esc(json.dumps(card['context'], indent=2, ensure_ascii=False)) + '</pre></details>'
    if not card['can_allow']:
        body += '<div class="notice warning">This preview was masked or shortened. Return to the terminal to approve or answer safely.</div>'
    return body


class Dashboard:
    def __init__(self, server):
        self.server = server
        self.lock = threading.RLock()
        self.tickets = {}
        self.sessions = {}
        self.failures = {}

    @property
    def origin(self):
        return 'http://127.0.0.1:' + str(self.server.server_port)

    @property
    def cookie_name(self):
        return 'mcpulse_ui_' + str(self.server.server_port)

    def login_url(self):
        with self.lock:
            self.tickets = {k: v for k, v in self.tickets.items() if v > time.time()}
            if len(self.tickets) >= 16:
                self.tickets.pop(next(iter(self.tickets)))
            ticket = secrets.token_urlsafe(32)
            self.tickets[ticket] = time.time() + 60
        return self.origin + '/ui/login?ticket=' + ticket

    def send(self, handler, status, body='', *, location=None, cookie=None):
        data = body.encode()
        handler.send_response(status)
        for key, value in {'Content-Type':'text/html; charset=utf-8', 'Content-Length':str(len(data)),
                           'Cache-Control':'no-store', 'Referrer-Policy':'same-origin', 'X-Content-Type-Options':'nosniff',
                           'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}.items():
            handler.send_header(key, value)
        if location:
            handler.send_header('Location', location)
        if cookie is not None:
            handler.send_header('Set-Cookie', self.cookie_name + '=' + cookie + '; Path=/ui; HttpOnly; SameSite=Strict' + ('; Max-Age=28800' if cookie else '; Max-Age=0'))
        handler.end_headers()
        try:
            handler.wfile.write(data)
        except OSError:
            pass

    def handle(self, handler):
        url = urlsplit(handler.path)
        if url.path not in {'/', '/ui'} and not url.path.startswith('/ui/'):
            return False
        # Host validation prevents a hostile DNS name from reaching the loopback UI.
        if handler.headers.get('Host') != self.origin.removeprefix('http://'):
            self.send(handler, 403, standalone('Access denied', '<p>Use the local dashboard URL from the CLI.</p>'))
            return True
        try:
            if handler.command == 'GET' and url.path == '/ui/login':
                ticket = parse_qs(url.query, max_num_fields=2).get('ticket', [''])[0]
                with self.lock:
                    if self.tickets.pop(ticket, 0) <= time.time():
                        raise RequestError('Login link expired or already used. Run python -m mcpulse ui again.')
                    self.sessions = {k: v for k, v in self.sessions.items() if v[0] > time.time()}
                    if len(self.sessions) >= 32:
                        self.sessions.pop(next(iter(self.sessions)))
                    token = secrets.token_urlsafe(32)
                    self.sessions[token] = (time.time() + 28800, secrets.token_urlsafe(32))
                self.send(handler, 303, location='/ui', cookie=token)
                return True
            cookies = SimpleCookie()
            cookies.load(handler.headers.get('Cookie', ''))
            token = cookies[self.cookie_name].value if self.cookie_name in cookies else ''
            with self.lock:
                session = self.sessions.get(token)
            if not session or session[0] <= time.time():
                self.send(handler, 401, standalone('Open your dashboard', '<p class="muted">Start MCPulse, then open a private dashboard session from your terminal.</p><pre class="request-preview">python -m mcpulse ui</pre>'))
                return True
            broker = self.server.broker
            broker.tick()
            if handler.command == 'POST':
                # Some embedded browsers omit Origin on same-site forms. The
                # session-bound CSRF token remains required on every mutation.
                if handler.headers.get('Origin') not in {None, self.origin}:
                    self.send(handler, 403, standalone('Request rejected', '<p>Open the dashboard in its original browser tab.</p>'))
                    return True
                length = int(handler.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536 or handler.headers.get('Transfer-Encoding'):
                    raise RequestError('Invalid form size')
                if handler.headers.get_content_type() != 'application/x-www-form-urlencoded':
                    raise RequestError('Expected a form')
                values = parse_qs(handler.rfile.read(length).decode(), max_num_fields=50)
                data = {k: v[0] for k, v in values.items() if len(v) == 1}
                data['_choices'] = {k:v for k,v in values.items() if k.startswith('choice_')}
                if not secrets.compare_digest(data.get('csrf', ''), session[1]):
                    self.send(handler, 403, standalone('Request rejected', '<p>Refresh the dashboard before trying again.</p>'))
                    return True
                if url.path == '/ui/logout':
                    with self.lock:
                        self.sessions.pop(token, None)
                    self.send(handler, 303, location='/ui', cookie='')
                    return True
                self.act(url.path, data)
                self.send(handler, 303, location='/ui?notice=saved')
                return True
            if url.path in {'/', '/ui'}:
                query = parse_qs(url.query, max_num_fields=6)
                content = self.render(session[1], {k:v[0] for k,v in query.items()})
            elif url.path.startswith('/ui/request/'):
                rid = url.path.rsplit('/', 1)[-1]
                with broker.lock:
                    request = broker.pending.get(rid)
                    if not request:
                        raise RequestError('This request is already answered or expired.')
                    card = dict(request.card)
                content = self.detail(card, session[1])
            else:
                self.send(handler, 404, standalone('Page not found', '<a class="button" href="/ui">Back to dashboard</a>'))
                return True
            self.send(handler, 200, content)
        except (ValueError, UnicodeError, CookieError) as error:
            self.send(handler, 400, standalone('Cannot complete request', '<p>' + esc(error) + '</p><a class="button secondary" href="/ui">Back to dashboard</a>'))
        return True

    def act(self, path, data):
        broker = self.server.broker
        if path == '/ui/presence':
            broker.set_presence(data.get('mode'))
        elif path == '/ui/mute':
            seconds = int(data.get('seconds', '0'))
            if seconds not in {0, 3600}:
                raise RequestError('Invalid mute duration')
            with broker.lock:
                broker.muted_until = time.time() + seconds
        elif path == '/ui/answer':
            action, rid = data.get('action'), data.get('id')
            if action not in {'allow', 'deny', 'handback', 'text', 'done'}:
                raise RequestError('Unsupported action')
            confirmed = False
            with broker.lock:
                request = broker.pending.get(rid)
                if not request:
                    raise RequestError('This request is already answered or expired.')
                if action == 'text' and data.get('question_form') == '1':
                    answers = {}
                    for index, question in enumerate(request.payload.get('tool_input', {}).get('questions', [])):
                        custom = data.get('custom_' + str(index), '').strip()
                        selected = data.get('_choices', {}).get('choice_' + str(index), [])
                        options = question.get('options', [])
                        if len(selected) > 1 and not question.get('multiSelect'):
                            raise RequestError('Choose one option for this question.')
                        if any(not v.isascii() or not v.isdigit() or int(v) >= len(options) for v in selected):
                            raise RequestError('Invalid choice; refresh the request.')
                        answer = custom or ', '.join(options[int(v)]['label'] for v in selected)
                        if not answer:
                            raise RequestError('Answer every question before sending.')
                        answers[question['question']] = answer
                    data['reply'] = json.dumps(answers, ensure_ascii=False) if answers else ''
                if action == 'text' and not data.get('reply', '').strip():
                    raise RequestError('Write an answer or follow-up before sending.')
                if action == 'allow' and request.card['risk'] == 'high':
                    with self.lock:
                        self.failures = {k:v for k,v in self.failures.items() if k in broker.pending}
                        attempts = [stamp for stamp in self.failures.get(rid, []) if time.time() - stamp < 300]
                        if len(attempts) >= 5 or not valid_pin(data.get('pin', ''), broker.config):
                            self.failures[rid] = [*attempts, time.time()][:5]
                            raise RequestError('A valid approval PIN is required. After five failed attempts, wait five minutes.')
                    confirmed = data.get('confirm') == 'yes'
                    if not confirmed:
                        raise RequestError('Confirm that you reviewed this high-risk action.')
                broker.answer(rid, action, 'dashboard', confirmed=confirmed, reason=data.get('reply', ''))
        else:
            raise RequestError('Unknown action')

    def hidden(self, csrf):
        return '<input type="hidden" name="csrf" value="' + esc(csrf) + '">'

    def layout(self, title, body, csrf, view='overview'):
        nav = [('overview', '◫', 'Overview'), ('requests', '☷', 'Requests'), ('sessions', '▣', 'Sessions'), ('activity', '↗', 'Activity'), ('channels', '◉', 'Channels')]
        links = ''.join('<a href="/ui?view=' + key + '"' + (' class="active" aria-current="page"' if key == view else '') + '><span class="nav-icon" aria-hidden="true">' + icon + '</span>' + name + '</a>' for key, icon, name in nav)
        demo = getattr(self.server, 'demo', False)
        banner = '<div class="notice">Preview mode · Sample requests only. Answers stay in this demo; no commands run and no messages are sent.</div>' if demo else ''
        return page(title, '<a class="skip" href="#main">Skip to content</a><div class="shell"><aside class="sidebar"><a class="brand" href="/ui">MC<em>Pulse</em><small>LOCAL</small></a><div><div class="nav-label">Workspace</div><nav aria-label="Main navigation">' + links + '</nav></div><div class="sidebar-note"><span class="dot"></span>Running on this computer<p>Built in Python.<br>Your decisions, within reach.</p></div></aside><div class="workspace"><header class="topbar"><div class="crumb">Workspace &nbsp; / &nbsp; <strong>' + esc(title) + '</strong></div><div class="top-actions"><span class="small muted"><span class="dot"></span>' + ('Demo connected' if demo else 'Daemon connected') + '</span><form method="post" action="/ui/logout">' + self.hidden(csrf) + '<button class="subtle">Lock dashboard</button></form></div></header><main id="main" class="content">' + banner + body + '<footer class="footer"><span>MCPulse · Human judgment, uninterrupted.</span><span>Updated ' + datetime.now().strftime('%H:%M:%S') + ' · Refresh to fetch latest</span></footer></main></div></div>')

    def request_list(self, cards):
        if not cards:
            return '<div class="empty"><strong>You’re all caught up.</strong>New requests appear here when a connected session needs you.</div>'
        result = ''
        for card in cards:
            tag = 'high' if card['risk'] == 'high' and card['kind'] == 'permission' else card['kind']
            label = card['risk'] + ' risk' if card['kind'] == 'permission' else card['kind']
            preview = card.get('questions', [{}])[0].get('question', '') if card.get('questions') else input_text(card)
            if len(preview) > 240:
                preview = preview[:240] + '…'
            result += '<article class="request"><div class="request-head"><div><h3>' + esc(request_title(card)) + '</h3><div class="request-meta">' + esc(project_name(card['project'])) + ' &nbsp;·&nbsp; ' + esc(card.get('agent', 'Unknown agent')) + ' &nbsp;·&nbsp; ' + esc(card['tool']) + '</div></div><span class="tag ' + esc(tag) + '">' + esc(label) + '</span></div><pre class="request-preview">' + esc(preview) + '</pre><div class="request-foot"><span class="muted small">Waiting · ' + age(card['created']) + '</span><a class="button secondary" href="/ui/request/' + esc(card['id']) + '">Review request ↗</a></div></article>'
        return result

    def session_list(self, sessions):
        if not sessions:
            return '<div class="empty"><strong>No sessions yet</strong>Sessions appear after Claude sends a session hook.</div>'
        return ''.join('<div class="session"><h3>' + esc(project_name(s['project'])) + '</h3><p class="small muted">' + esc(s.get('task', 'Session started')) + '</p><p class="small muted">Agent: ' + esc(s.get('agent', 'Unknown agent')) + '</p><span class="small muted">Session ID: </span><code>' + esc(s['id']) + '</code><p class="small muted">Last seen ' + age(s['updated']) + '</p></div>' for s in sorted(sessions, key=lambda s:s['updated'], reverse=True))

    def activity(self, events):
        if not events:
            return '<div class="empty"><strong>No activity yet</strong>Decisions will appear here as requests are reviewed.</div>'
        rows = ''.join('<tr><td><span class="tag">' + esc(e['state'].replace('_', ' ').lower()) + '</span></td><td><code>' + esc(e['id']) + '</code></td><td>' + esc(e['source']) + '</td><td>' + esc(datetime.fromtimestamp(e['at']).strftime('%d %b, %H:%M:%S')) + '</td></tr>' for e in events)
        return '<div class="table-wrap"><table><thead><tr><th>Event</th><th>Request</th><th>Source</th><th>Local time</th></tr></thead><tbody>' + rows + '</tbody></table></div>'

    def channels(self, health):
        return ''.join('<div class="channel"><div><h3>' + esc(name.title()) + '</h3><span class="small muted">' + esc(health.get(name, 'Not connected')) + '</span></div><span class="tag">' + ('connected' if health.get(name) == 'connected' else 'offline') + '</span></div>' for name in ('telegram', 'slack', 'ntfy', 'email', 'whatsapp'))

    def render(self, csrf, query):
        broker = self.server.broker
        state, events = broker.status(), broker.audit()
        view = query.get('view', 'overview')
        if view not in {'overview', 'requests', 'sessions', 'activity', 'channels'}:
            raise RequestError('Unknown dashboard view')
        title = {'overview':'Your workspace', 'requests':'Pending requests', 'sessions':'Sessions', 'activity':'Activity', 'channels':'Channels'}[view]
        subtitles = {'overview':'Step away. Stay in the loop.', 'requests':'A little human judgment goes a long way.', 'sessions':'The latest updates from your connected agent sessions.', 'activity':'A record of requests and the decisions that moved them forward.', 'channels':'Your paths from a waiting session to an answer.'}
        body = '<div class="hero"><div><div class="eyebrow">' + ('Command center' if view == 'overview' else 'Workspace / ' + esc(view)) + '</div><h1>' + title + '</h1><p class="muted">' + subtitles[view] + '</p></div><a class="button secondary" href="/ui?' + esc(urlencode(query)) + '">↻ &nbsp; Refresh</a></div>'
        if query.get('notice') == 'saved':
            body += '<div class="notice" role="status">Saved. Your workspace is up to date.</div>'
        cards = state['pending']
        if view == 'overview':
            body += '<div class="metrics">' + ''.join('<div class="metric"><div class="muted">' + name + '</div><div class="value">' + str(number) + '</div><small>' + detail + '</small></div>' for name,number,detail in [('Needs your attention',len(cards),'Pending requests'),('Tracked sessions',len(state['sessions']),'Reported by session hooks'),('Connected channels',sum(v == 'connected' for k,v in state['channels'].items() if k in {'telegram','slack','ntfy','email','whatsapp'}),'Ready to deliver')]) + '</div>'
            options = ''.join('<option value="' + mode + '"' + (' selected' if state['presence_mode'] == mode else '') + '>' + label + '</option>' for mode,label in [('away','Away · send requests to me'),('present','Present · use the terminal'),('auto','Auto · follow my activity')])
            presence = '<section class="panel"><div class="panel-head"><h2>Your presence</h2><span class="tag">' + esc(state['presence']) + '</span></div><div class="panel-body"><form class="presence" method="post" action="/ui/presence">' + self.hidden(csrf) + '<label for="presence">Where should requests go?</label><select id="presence" name="mode">' + options + '</select><button>Update presence</button></form><p class="small muted">Switching to Present returns pending Claude hook requests to your terminal. Mode changes last until restart.</p></div></section>'
            muted = state['muted_until'] > time.time()
            mute = '<section class="panel"><div class="panel-body"><h3>Notification pause</h3><p class="small muted">' + ('Notifications paused until ' + datetime.fromtimestamp(state['muted_until']).strftime('%H:%M') if muted else 'Pause informational notifications for an hour. Approval requests still reach your channels.') + '</p><form method="post" action="/ui/mute">' + self.hidden(csrf) + '<input type="hidden" name="seconds" value="' + ('0' if muted else '3600') + '"><button class="secondary">' + ('Resume notifications' if muted else 'Pause for 1 hour') + '</button></form></div></section>'
            body += '<div class="split"><div><section class="panel"><div class="panel-head"><h2>Waiting for you <span class="count">' + str(len(cards)) + '</span></h2><a class="small muted" href="/ui?view=requests">View all →</a></div>' + self.request_list(cards[:5]) + '</section><section class="panel"><div class="panel-head"><h2>Recent activity</h2><a class="small muted" href="/ui?view=activity">View all →</a></div>' + self.activity(events[:5]) + '</section></div><aside class="aside">' + presence + mute + '<section class="panel"><div class="panel-head"><h2>Sessions</h2><a class="small muted" href="/ui?view=sessions">All →</a></div>' + self.session_list(state['sessions'][:3]) + '</section></aside></div>'
        elif view == 'requests':
            search, kind = query.get('q', '')[:200], query.get('kind', '')
            body += '<form class="toolbar" method="get" action="/ui"><input type="hidden" name="view" value="requests"><input aria-label="Search requests" name="q" placeholder="Search agent, session, project, or request…" value="' + esc(search) + '"><select aria-label="Request type" name="kind">' + ''.join('<option value="' + value + '"' + (' selected' if value == kind else '') + '>' + label + '</option>' for value,label in [('', 'All requests'),('permission','Permissions'),('question','Questions'),('finished','Completed'),('notification','Notifications')]) + '</select><button class="secondary">Filter</button></form>'
            cards = [c for c in cards if (not kind or c['kind'] == kind) and search.lower() in ' '.join((c['project'], c['tool'], c['input'], c.get('agent', ''), c['session'])).lower()]
            body += '<section class="panel">' + self.request_list(cards) + '</section>'
        elif view == 'sessions':
            body += '<section class="panel">' + self.session_list(state['sessions']) + '</section>'
        elif view == 'activity':
            body += '<p class="small muted">Most recent 1,000 events. Request contents are excluded from this audit view.</p><section class="panel">' + self.activity(events) + '</section>'
        else:
            body += '<section class="panel">' + self.channels(state['channels']) + '</section><div class="notice">Connect channels from the CLI using <code>python -m mcpulse channel</code>. Credentials are kept out of this dashboard.</div>'
        return self.layout(title, body, csrf, view)

    def detail(self, card, csrf):
        kind = card['kind']
        body = '<div class="detail"><a class="muted small" href="/ui?view=requests">← Back to requests</a><div class="hero"><div><div class="eyebrow">Request review</div><h1>' + esc(request_title(card)) + '</h1><p class="muted">' + esc(project_name(card['project'])) + ' · ' + age(card['created']) + '</p></div></div><section class="panel"><div class="panel-body">' + card_details(card, show_questions=False)
        body += '<form method="post" action="/ui/answer">' + self.hidden(csrf) + '<input type="hidden" name="id" value="' + esc(card['id']) + '">'
        if kind == 'permission':
            if card['risk'] == 'high':
                body += '<div class="notice warning">High-risk action. Review the full command before approving.</div><label for="pin">Approval PIN (required to allow)</label><input id="pin" type="password" name="pin" autocomplete="off" inputmode="numeric"><label><input type="checkbox" name="confirm" value="yes">I have reviewed this high-risk action.</label><p class="small muted">Set your PIN with <code>python -m mcpulse pin set</code>, then restart the daemon.</p>'
            body += '<label for="reply">Reason for denying (optional)</label><textarea id="reply" name="reply" rows="2" maxlength="4000" placeholder="Explain what Claude should do instead…"></textarea><div class="actions"><button name="action" value="allow"' + ('' if card['can_allow'] else ' disabled') + '>Allow once</button><button class="danger" name="action" value="deny">Deny request</button>'
        elif kind in {'question', 'finished'}:
            if card.get('questions'):
                body += '<input type="hidden" name="question_form" value="1">'
                for index, question in enumerate(card['questions']):
                    body += '<fieldset><legend>' + esc(question['question']) + '</legend>'
                    if question.get('multiSelect'):
                        body += '<p class="muted small">Choose all that apply.</p>'
                    for number, option in enumerate(question.get('options', [])):
                        body += '<label class="choice"><input type="' + ('checkbox' if question.get('multiSelect') else 'radio') + '" name="choice_' + str(index) + '" value="' + str(number) + '">' + esc(option['label']) + '</label>'
                    body += '<label for="custom_' + str(index) + '">' + ('Or write your own answer' if question.get('options') else 'Your answer') + '</label><textarea id="custom_' + str(index) + '" name="custom_' + str(index) + '" rows="2" maxlength="1000"></textarea></fieldset>'
                body += '<p class="small muted">A written answer replaces the selected options for that question.</p>'
            else:
                body += '<label for="reply">' + ('Your answer' if kind == 'question' else 'Follow-up instructions') + '</label><textarea id="reply" name="reply" rows="4" maxlength="4000" placeholder="Write your reply…"></textarea>'
            body += '<div class="actions"><button name="action" value="text"' + ('' if card['can_allow'] else ' disabled') + '>Send reply</button>'
            if kind == 'finished':
                body += '<button class="secondary" name="action" value="done">Done</button>'
        else:
            body += '<div class="actions"><button name="action" value="done">Mark as read</button>'
        body += '<button class="secondary" name="action" value="handback">Return to terminal</button></div></form></div></section></div>'
        return self.layout('Review request', body, csrf, 'requests')


def preview_server(port=7720, *, open_browser=True):
    """Disposable in-memory demo, with no adapters, hooks, or command execution."""
    from .core import Broker
    from .server import Server
    if not 0 <= port <= 65535:
        raise ValueError('Port must be between 0 and 65535')
    broker = Broker(':memory:', 'away', timeout=86400, config={'context': False, 'stop_timeout': 86400})
    base = {'agent':'claude-cli', 'session_id':'demo-mcpulse', 'cwd':'/demo/MCPulse'}
    broker.session_event({**base, 'hook_event_name':'UserPromptSubmit', 'prompt':'Build the Python dashboard and verify the approval flow.'})
    broker.session_event({'session_id':'demo-docs', 'cwd':'/demo/documentation', 'hook_event_name':'UserPromptSubmit', 'prompt':'Update the setup guide for the next release.'})
    completed = broker.open({**base, 'hook_event_name':'PermissionRequest', 'tool_name':'Bash', 'tool_input':{'command':'git diff --stat'}})
    broker.answer(completed.id, 'allow', 'demo', confirmed=True)
    broker.open({**base, 'hook_event_name':'PermissionRequest', 'tool_name':'Bash', 'tool_input':{'command':'git status'}})
    broker.open({**base, 'hook_event_name':'PreToolUse', 'tool_name':'AskUserQuestion', 'tool_input':{'questions':[{'question':'Which part should I work on next?', 'options':[{'label':'Improve the approval experience'}, {'label':'Write the setup guide'}]}]}})
    broker.open({'session_id':'demo-docs', 'cwd':'/demo/documentation', 'hook_event_name':'Stop', 'last_assistant_message':'The setup guide is ready for review. Would you like any changes?'})
    server = None
    try:
        server = Server(broker, secrets.token_hex(32), port)
        server.demo = True
        url = server.dashboard.login_url()
        print('MCPulse preview · sample data only · Ctrl-C to stop', flush=True)
        print(url, flush=True)
        if open_browser:
            import webbrowser
            webbrowser.open(url)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        broker.close()

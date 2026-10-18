"""Exercise the dashboard through real HTTP, including browser trust boundaries."""
from http.cookiejar import CookieJar
import re
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler, HTTPCookieProcessor

from mcpulse.core import Broker
from mcpulse.server import Server
from mcpulse.replies import pin_hash
from tests.test_core import payload


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(':memory:', 'away', config={'context':False})
        self.server = Server(self.broker, 'api-test-key', 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':0.01})
        self.thread.start()
        self.base = self.server.dashboard.origin
        self.cookies = CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies))

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.broker.close()

    def get(self, path='/ui', data=None, headers=None):
        request = Request(path if path.startswith('http://') else self.base + path,
                          data=None if data is None else urlencode(data).encode(),
                          headers=headers or ({'Origin':self.base} if data is not None else {}))
        try:
            response = self.opener.open(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read().decode(), response.headers

    def login(self):
        url = self.server.dashboard.login_url()
        status, body, headers = self.get(url)
        self.assertEqual(status, 200)
        self.assertIn('Your workspace', body)
        return re.search(r'name="csrf" value="([^"]+)"', body)[1]

    def test_requires_login_one_time_link_expiry_and_logout(self):
        self.assertEqual(self.get()[0], 401)
        url = self.server.dashboard.login_url()
        self.assertEqual(self.get(url)[0], 200)
        self.assertEqual(self.get(url)[0], 400)
        cookie = next(iter(self.cookies))
        self.assertIn('HttpOnly', cookie._rest)
        self.assertEqual(cookie._rest['SameSite'], 'Strict')
        token = self.login()
        self.assertEqual(self.get('/ui/logout', {'csrf':token})[0], 401)
        url = self.server.dashboard.login_url()
        self.server.dashboard.tickets[url.split('ticket=')[1]] = time.time() - 1
        self.assertEqual(self.get(url)[0], 400)
        self.assertEqual(self.get()[0], 401)

    def test_host_origin_and_csrf_cannot_mutate(self):
        csrf = self.login()
        request = self.broker.open(payload())
        body = {'csrf':csrf, 'id':request.id, 'action':'allow'}
        self.assertEqual(self.get('/ui', headers={'Host':'evil.example'})[0], 403)
        self.assertEqual(self.get('/ui/answer', body, {'Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.get('/ui/answer', body, {'Origin':'null'})[0], 403)
        self.assertEqual(self.get('/ui/answer', {**body,'csrf':'wrong'})[0], 403)
        self.assertEqual(self.get('/ui/answer', {**body,'csrf':'wrong'}, {'X-Test':'no-origin'})[0], 403)
        self.assertEqual(request.state, 'OPEN')
        self.assertEqual(self.get('/ui/answer?' + urlencode(body))[0], 404)
        self.assertEqual(request.state, 'OPEN')
        self.assertEqual(self.get('/ui/answer', body, {'X-Test':'no-origin'})[0], 200)
        self.assertEqual(request.state, 'ALLOWED')
        self.assertEqual(self.get('/ui/answer', body)[0], 200)
        self.assertEqual(len([e for e in self.broker.audit() if e['state']=='ALLOWED']), 1)

    def test_views_filter_escape_and_empty_state(self):
        self.login()
        request = self.broker.open(payload("echo '<script>unsafe</script>'"))
        for view in ('overview','requests','sessions','activity','channels'):
            status, body, headers = self.get('/ui?view=' + view)
            self.assertEqual(status, 200)
            self.assertNotIn('<script>', body)
            self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        detail = self.get('/ui/request/' + request.id)[1]
        self.assertIn('&lt;script&gt;', detail)
        self.assertIn('You’re all caught up', self.get('/ui?view=requests&kind=question')[1])
        self.assertIn('You’re all caught up', self.get('/ui?view=requests&q=nomatch')[1])
        self.assertEqual(request.state, 'OPEN')

    def test_agent_and_session_metadata_are_visible_searchable_and_escaped(self):
        self.login()
        request = self.broker.open({**payload(session="session-unique"), "agent":"opencode"})
        detail = self.get('/ui/request/' + request.id)[1]
        self.assertIn('<dt>Agent</dt><dd>OpenCode</dd>', detail)
        self.assertIn('<dt>Session ID</dt><dd>session-unique</dd>', detail)
        for search in ('OpenCode', 'session-unique'):
            self.assertIn(request.id, self.get('/ui?view=requests&q=' + search)[1])
        unsafe = self.broker.open({**payload(), "agent":"<script>unsafe</script>"})
        detail = self.get('/ui/request/' + unsafe.id)[1]
        self.assertNotIn('<script>', detail)
        self.assertIn('&lt;script&gt;', detail)

    def test_high_risk_pin_and_confirmation(self):
        self.broker.config.update(pin_salt='ab'*16, pin_hash=pin_hash('123456','ab'*16))
        csrf = self.login()
        request = self.broker.open(payload('git push'))
        body = {'csrf':csrf, 'id':request.id, 'action':'allow'}
        self.assertEqual(self.get('/ui/answer', body)[0], 400)
        self.assertEqual(self.get('/ui/answer', {**body,'pin':'123456'})[0], 400)
        self.assertEqual(request.state, 'OPEN')
        self.assertEqual(self.get('/ui/answer', {**body,'pin':'123456','confirm':'yes'})[0], 200)
        self.assertEqual(request.state, 'ALLOWED')
        limited = self.broker.open(payload('git push'))
        body.update(id=limited.id, confirm='yes', pin='000000')
        for _ in range(5):
            self.assertEqual(self.get('/ui/answer', body)[0], 400)
        self.assertEqual(self.get('/ui/answer', {**body,'pin':'123456'})[0], 400)
        self.assertEqual(limited.state, 'OPEN')

    def test_masked_request_cannot_be_approved(self):
        csrf = self.login()
        request = self.broker.open(payload('echo password=secret'))
        self.assertFalse(request.card['can_allow'])
        self.assertIn('disabled', self.get('/ui/request/' + request.id)[1])
        self.assertEqual(self.get('/ui/answer', {'csrf':csrf, 'id':request.id, 'action':'allow'})[0], 400)
        self.assertEqual(request.state, 'OPEN')

    def test_question_reply_finished_and_presence(self):
        csrf = self.login()
        question = self.broker.open({**payload(), 'hook_event_name':'PreToolUse', 'tool_name':'AskUserQuestion', 'tool_input':{'questions':[{'question':'Pick a runner', 'options':[{'label':'unittest'}, {'label':'pytest'}]}]}})
        self.assertIn('unittest', self.get('/ui/request/' + question.id)[1])
        self.assertEqual(self.get('/ui/answer', {'csrf':csrf,'id':question.id,'action':'text','reply':'1'})[0],200)
        self.assertEqual(question.state,'ANSWERED')
        self.assertIn('unittest', str(question.response))
        finished = self.broker.open({**payload(), 'hook_event_name':'Stop', 'last_assistant_message':'Ready for review'})
        self.assertEqual(self.get('/ui/answer', {'csrf':csrf,'id':finished.id,'action':'text','reply':' '})[0],400)
        self.assertEqual(finished.state,'OPEN')
        self.assertEqual(self.get('/ui/answer', {'csrf':csrf,'id':finished.id,'action':'done'})[0],200)
        self.assertEqual(finished.state,'DONE')
        pending = self.broker.open(payload())
        self.assertEqual(self.get('/ui/presence', {'csrf':csrf,'mode':'present'})[0],200)
        self.assertEqual(pending.state,'HANDED_BACK')
        self.assertEqual(self.get('/ui/mute', {'csrf':csrf,'seconds':'3600'})[0],200)
        self.assertGreater(self.broker.muted_until,time.time()+3500)
        self.assertEqual(self.get('/ui/mute', {'csrf':csrf,'seconds':'0'})[0],200)

    def test_api_login_requires_key_and_dashboard_cookie_does_not_unlock_api(self):
        self.login()
        self.assertEqual(self.get('/api/status')[0],401)
        self.assertEqual(self.get('/api/ui-login', {})[0],401)

    def test_native_multi_question_choices_and_literal_numeric_labels(self):
        csrf = self.login()
        questions = [{'question':'Pick a version', 'options':[{'label':'2'}, {'label':'3'}]},
                     {'question':'Choose features', 'multiSelect':True, 'options':[{'label':'Approvals'}, {'label':'Questions'}]},
                     {'question':'Any notes?', 'options':[]}]
        request = self.broker.open({**payload(), 'hook_event_name':'PreToolUse', 'tool_name':'AskUserQuestion', 'tool_input':{'questions':questions}})
        body = self.get('/ui/request/' + request.id)[1]
        self.assertIn('type="radio"', body)
        self.assertIn('type="checkbox"', body)
        form = {'csrf':csrf, 'id':request.id, 'action':'text', 'question_form':'1', 'choice_0':'0', 'choice_1':'9', 'custom_2':'Ship it'}
        self.assertEqual(self.get('/ui/answer', form)[0],400)
        self.assertEqual(request.state,'OPEN')
        pairs = [(k,v) for k,v in form.items() if k != 'choice_1'] + [('choice_1','0'),('choice_1','1')]
        self.assertEqual(self.get('/ui/answer', pairs)[0],200)
        answers = request.response['hookSpecificOutput']['updatedInput']['answers']
        self.assertEqual(answers, {'Pick a version':'2','Choose features':'Approvals, Questions','Any notes?':'Ship it'})

    def test_closed_receipts_cover_outcomes_and_expire(self):
        csrf = self.login()
        for action, title in (('allow', 'Approval saved'), ('deny', 'Request denied'), ('handback', 'Returned to terminal')):
            request = self.broker.open(payload())
            status, body, _ = self.get('/ui/answer', {'csrf':csrf, 'id':request.id, 'action':action})
            self.assertEqual(status, 200)
            self.assertIn(title, body)
            self.assertIn('Claude CLI', body)
            self.assertIn('test-session', body)
            self.assertNotIn('name="action"', body)
            self.assertNotIn('git status', body)
            # A conflicting retry cannot replace the winning response.
            repeated = self.get('/ui/answer', {'csrf':csrf, 'id':request.id, 'action':'allow'})
            self.assertIn(title, repeated[1])
            self.assertEqual(len([e for e in self.broker.audit() if e['id']==request.id and e['state']!='OPEN']), 1)
        expired = self.broker.open(payload())
        expired.deadline = time.monotonic() - 1
        self.assertIn('Request expired', self.get('/ui/request/' + expired.id)[1])
        cancelled = self.broker.open(payload())
        self.broker.cancel_session(cancelled.session)
        self.assertIn('Request cancelled', self.get('/ui/request/' + cancelled.id)[1])
        with self.broker.lock, self.broker.db:
            self.broker.db.execute("UPDATE requests SET expires=0 WHERE id=?", (expired.id,))
        self.assertEqual(self.get('/ui/request/' + expired.id)[0], 404)
        self.assertIn('receipt has expired', self.get('/ui/request/' + expired.id)[1])

    def test_validation_preserves_escaped_answers_and_choices(self):
        csrf = self.login()
        questions = [{'question':'Choose', 'options':[{'label':'One'}]}, {'question':'Notes?', 'options':[]}]
        request = self.broker.open({**payload(), 'hook_event_name':'PreToolUse', 'tool_name':'AskUserQuestion', 'tool_input':{'questions':questions}})
        form = {'csrf':csrf, 'id':request.id, 'action':'text', 'question_form':'1', 'choice_0':'0', 'custom_0':'</textarea><script>bad</script>'}
        status, body, _ = self.get('/ui/answer', form)
        self.assertEqual(status, 400)
        self.assertIn('role="alert"', body)
        self.assertIn('Answer every question', body)
        self.assertIn('value="0" checked', body)
        self.assertIn('&lt;/textarea&gt;&lt;script&gt;bad', body)
        self.assertNotIn('<script>', body)
        self.assertEqual(request.state, 'OPEN')
        status, body, _ = self.get('/ui/answer', {**form, 'custom_1':'Ready'})
        self.assertEqual(status, 200)
        self.assertIn('Reply saved', body)
        self.assertIn('agent delivery is not confirmed', body)

    def test_uncertain_failure_checks_status_before_retry(self):
        csrf = self.login()
        for saved in (False, True):
            request = self.broker.open(payload())
            answer = self.broker.answer
            def fail(*args, **kwargs):
                if saved:
                    answer(*args, **kwargs)
                raise ConnectionError('private diagnostic must not leak')
            with patch.object(self.broker, 'answer', side_effect=fail):
                status, body, _ = self.get('/ui/answer', {'csrf':csrf, 'id':request.id, 'action':'allow'})
            self.assertEqual(status, 503)
            self.assertIn('Check status / retry', body)
            self.assertIn('/ui/request/' + request.id, body)
            self.assertNotIn('private diagnostic', body)
            status, body, _ = self.get('/ui/request/' + request.id)
            self.assertEqual(status, 200)
            self.assertIn('Approval saved' if saved else 'Allow once', body)

    def test_channels_distinguish_unverified_from_connected(self):
        self.login()
        self.broker.channel_health.update(ntfy='ready; delivery not yet verified', slack='1 delivery/update failed; retrying', telegram='connected')
        body = self.get('/ui?view=channels')[1]
        for label in ('unverified', 'needs attention', 'not configured', 'connected'):
            self.assertIn('class="tag">' + label, body)
        self.assertIn('Provider acceptance does not confirm', body)

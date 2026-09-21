"""Browser smoke/regression tests with mocked auth and inference (no real users)."""
import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'web-test-results'
MOCK_SDK = """
window.supabase = { createClient(url) {
  let session = null, listener = () => {};
  return { supabaseUrl: url, auth: {
    onAuthStateChange(callback) { listener = callback; },
    async getSession() { return {data: {session}}; },
    async signInWithPassword({email}) {
      session = {access_token:'fake-session-token',user:{id:'test-user',email}};
      listener('SIGNED_IN', session); return {data:{session}};
    },
    async signUp() { return {data:{session:null}}; },
    async signOut() { session = null; listener('SIGNED_OUT',null); return {}; }
  }};
}};
"""


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    OUT.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            edge = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
            browser = p.chromium.launch(headless=True, **({'executable_path': str(edge)} if edge.exists() else {}))
            context = browser.new_context(viewport={'width':1440,'height':1000})
            context.route('https://cdn.jsdelivr.net/**', lambda route: route.fulfill(content_type='application/javascript', body=MOCK_SDK))
            context.route('https://fonts.googleapis.com/**', lambda route: route.fulfill(content_type='text/css', body=''))
            context.route('**/config.js', lambda route: route.fulfill(content_type='application/javascript', body='window.NUTRITION_CONFIG={supabaseUrl:"https://test.supabase.co",supabaseKey:"sb_publishable_mock"};'))
            calls = []

            def chat(route):
                assert route.request.headers['authorization'] == 'Bearer fake-session-token'
                question = route.request.post_data_json['question']
                calls.append(question)
                if question == 'Quota test':
                    route.fulfill(status=429, headers={'Retry-After':'60'}, json={'error':{'code':'DAILY_LIMIT_REACHED','message':'Daily limit reached.'}})
                elif question == 'No sources test':
                    route.fulfill(json={'status':'no_sources','answer':'No relevant textbook passages were found.','sources':[], 'usage':{'limit':10,'remaining':8}})
                elif question == 'Session test':
                    route.fulfill(status=401, json={'error':{'code':'UNAUTHENTICATED','message':'Sign in again.'}})
                elif question == 'Failure test':
                    route.fulfill(status=503, json={'error':{'code':'PROVIDER_CREDITS_REQUIRED','message':'Provider credits are unavailable.'},'request_id':'test-reference'})
                else:
                    route.fulfill(json={'status':'answered','answer':'**Dietary fiber** supports digestion. [S1]\n- Read the source for details.\n<img src=x onerror=alert(1)>',
                        'sources':[{'label':'S1','pdf_pages':[120,121],'passage':'Fiber supports normal bowel function. <script>alert(1)</script>'}],
                        'review_notes':['Review the cited passage.'], 'usage':{'limit':10,'remaining':9}})

            context.route('https://test.supabase.co/functions/v1/nutrition-chat', chat)
            page = context.new_page()
            page.add_init_script('''
              window.soundStarts = 0;
              const originalStart = OscillatorNode.prototype.start;
              OscillatorNode.prototype.start = function(...args) {
                window.soundStarts++;
                return originalStart.apply(this, args);
              };
            ''')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base)
            expect(page.locator('#chat-error')).to_be_hidden()
            expect(page).to_have_title('RAG Nutrition Assistant')
            expect(page.locator('.byline')).to_have_text('Presented by Ankit Kondilkar')
            expect(page.locator('#send')).to_be_disabled()
            page.screenshot(path=str(OUT / 'desktop.png'), full_page=True)
            for button in page.locator('.suggestion').all():
                label = button.locator('.question-label').inner_text()
                button.click()
                expect(page.locator('#question')).to_have_value(label)
            page.get_by_role('button', name='What do macronutrients actually do?').click()
            page.get_by_role('button', name='Send question').click()
            expect(page.get_by_role('dialog')).to_be_visible()
            assert not calls
            page.get_by_label('Email address').fill('reader@example.test')
            page.get_by_label('Password', exact=True).fill('mock-password')
            page.locator('#auth-submit').click()
            expect(page.get_by_role('dialog')).not_to_be_visible()
            expect(page.locator('#account-button')).to_have_text('Sign out')
            page.get_by_label('Your nutrition question').fill('Why is fiber important?')
            page.get_by_role('button', name='Send question').click()
            expect(page.locator('#quota')).to_contain_text('9 / 10')
            expect(page.locator('.message.assistant')).to_have_count(1)
            assert page.evaluate('window.soundStarts') == 3, 'Completion must play three short tones'
            expect(page.locator('#sound-toggle')).to_have_count(0)
            expect(page.locator('.message-body img')).to_have_count(0)
            page.get_by_role('button', name='Read source S1').click()
            expect(page.locator('.source-card')).to_have_attribute('open', '')
            expect(page.locator('.passage')).to_contain_text('Fiber supports')
            expect(page.locator('.passage script')).to_have_count(0)
            page.screenshot(path=str(OUT / 'answer.png'), full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.get_by_role('button', name='Read source S1').click()
            expect(page.locator('#source-column')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(OUT / 'mobile-answer.png'), full_page=True)
            page.get_by_label('Your nutrition question').fill('Quota test')
            page.get_by_role('button', name='Send question').click()
            expect(page.locator('#chat-error')).to_contain_text('Daily limit reached')
            expect(page.locator('#quota')).to_contain_text('0 / 10')
            assert calls == ['Why is fiber important?', 'Quota test'], 'Client must not automatically retry'
            page.get_by_label('Your nutrition question').fill('Failure test')
            page.get_by_role('button', name='Send question').click()
            expect(page.locator('#chat-error')).to_contain_text('test-reference')
            expect(page.locator('#quota')).to_contain_text('Allowance updates')
            assert page.evaluate('window.soundStarts') == 3, 'Failures must not play completion audio'
            page.get_by_label('Your nutrition question').fill('No sources test')
            page.get_by_role('button', name='Send question').click()
            expect(page.locator('#source-empty')).to_be_visible()
            expect(page.locator('#sources details')).to_have_count(0)
            page.get_by_label('Your nutrition question').fill('Another response test')
            page.get_by_role('button', name='Send question').click()
            expect(page.locator('#quota')).to_contain_text('9 / 10')
            assert page.evaluate('window.soundStarts') == 6, 'Each successful answer plays its completion sound'
            page.get_by_label('Your nutrition question').fill('Session test')
            page.get_by_role('button', name='Send question').click()
            expect(page.get_by_role('dialog')).to_be_visible()
            page.get_by_role('button', name='Close sign-in').click()
            page.get_by_role('button', name='Sign out', exact=True).click()
            expect(page.locator('.message')).to_have_count(0)
            expect(page.locator('#account-name')).to_have_text('Your learning space')
            page.screenshot(path=str(OUT / 'mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.reload()
            expect(page.locator('#sound-toggle')).to_have_count(0)
            page.get_by_role('button', name='Sign in', exact=True).click()
            page.get_by_role('button', name='New here? Create an account').click()
            page.get_by_label('Email address').fill('reader@example.test')
            page.get_by_label('Password', exact=True).fill('mock-password')
            page.locator('#auth-submit').click()
            expect(page.locator('#auth-message')).to_contain_text('Check your email')
            assert not errors, errors
            browser.close()
            print('PASS: auth gating, login, signup confirmation, answers, safe text rendering, citations, mobile layout, quota, provider failure, no sources, expired session, sign-out privacy; no real API calls.')
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()

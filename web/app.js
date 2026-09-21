/* Browser client: model credentials and retrieval stay in the Edge Function. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  let client, session = null, sending = false, signup = false, controller;
  let generation = 0;
  let audioContext, soundGain;
  // Unlock audio inside the submit gesture, before the network request.
  function unlockSound() {
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) return;
      if (!audioContext) {
        audioContext = new Audio();
        soundGain = audioContext.createGain();
        soundGain.gain.value = 0.065;
        soundGain.connect(audioContext.destination);
      }
      if (audioContext.state === 'suspended') audioContext.resume().catch(() => {});
    } catch { /* Audio is optional; never interrupt chat. */ }
  }
  function responseSound() {
    if (audioContext?.state !== 'running') return;
    try {
      const now = audioContext.currentTime;
      [660, 880, 1320].forEach((frequency, index) => {
        const oscillator = audioContext.createOscillator();
        const envelope = audioContext.createGain();
        const start = now + index * 0.105;
        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(frequency, start);
        oscillator.frequency.exponentialRampToValueAtTime(frequency * 1.15, start + 0.16);
        envelope.gain.setValueAtTime(0, start);
        envelope.gain.linearRampToValueAtTime(0.6, start + 0.012);
        envelope.gain.exponentialRampToValueAtTime(0.001, start + 0.22);
        oscillator.connect(envelope);
        envelope.connect(soundGain);
        oscillator.start(start);
        oscillator.stop(start + 0.24);
        oscillator.onended = () => { oscillator.disconnect(); envelope.disconnect(); };
      });
    } catch { /* Browsers may suspend audio in background tabs. */ }
  }
  const notice = (text = '') => {
    $('chat-error').textContent = text;
    $('chat-error').classList.toggle('hidden', !text);
  };
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function controls() {
    $('send').disabled = sending || !$('question').value.trim() || !client;
    $('question').disabled = sending;
    $('busy').classList.toggle('hidden', !sending);
    $('character-count').textContent = `${$('question').value.length} / 1000`;
    $('messages').setAttribute('aria-busy', String(sending));
  }
  function clearChat() {
    generation++;
    controller?.abort();
    sending = false;
    $('messages').replaceChildren();
    $('welcome').classList.remove('hidden');
    $('question').value = '';
    showSources([]);
    $('source-column').classList.add('hidden');
    $('library-nav').setAttribute('aria-expanded', 'false');
    notice();
    controls();
  }
  function updateSession(next) {
    if (session?.user?.id !== next?.user?.id) {
      const draft = !session ? $('question').value : '';
      clearChat();
      $('question').value = draft;
      $('quota').textContent = next ? '10 attempts per day · resets at 00:00 UTC' : '10 questions per day after sign-in';
    }
    session = next;
    $('account-name').textContent = session?.user?.email || 'Your learning space';
    $('account-caption').textContent = session ? 'Signed in' : 'Sign in to start exploring';
    $('account-button').textContent = session ? 'Sign out' : 'Sign in';
    document.body.classList.toggle('signed-in', !!session);
    controls();
  }
  function openAuth() {
    if (!client) { notice('The app connection is not configured yet. Please contact the app owner.'); return; }
    $('auth-message').textContent = '';
    $('auth-dialog').showModal();
  }
  function showSources(sources, notes = [], selected) {
    $('sources').replaceChildren();
    $('source-count').textContent = sources.length || '—';
    $('source-empty').classList.toggle('hidden', sources.length > 0);
    $('review-notes').textContent = notes.join('\n');
    $('review-notes').classList.toggle('hidden', !notes.length);
    for (const source of sources) {
      const card = element('details', undefined, 'source-card');
      const summary = element('summary', `${source.label} · Human Nutrition: 2020 Edition by University of Hawai‘i at Mānoa Food Science and Human Nutrition Program`);
      summary.append(element('span', `PDF pages: ${(source.pdf_pages || []).join(', ') || 'Unavailable'}`));
      card.append(summary, element('div', source.passage, 'passage'));
      card.open = source.label === selected;
      card.classList.toggle('selected', card.open);
      $('sources').append(card);
    }
    if (selected) {
      $('source-column').classList.remove('hidden');
      $('library-nav').setAttribute('aria-expanded', 'true');
      $('source-column').scrollIntoView({ block: 'nearest' });
      const summary = $('sources').querySelector('.selected summary');
      summary?.focus();
    }
  }
  // Only text nodes and explicit formatting elements: no model-generated HTML.
  function inline(parent, text, sources, notes) {
    for (const part of text.split(/(\[S\d+\]|\*\*[^*]+\*\*)/g)) {
      const source = sources.find((s) => `[${s.label}]` === part);
      if (source) {
        const button = element('button', part, 'citation');
        button.type = 'button';
        button.setAttribute('aria-label', `Read source ${source.label}`);
        button.onclick = () => showSources(sources, notes, source.label);
        parent.append(button);
      } else if (part.startsWith('**') && part.endsWith('**')) {
        parent.append(element('strong', part.slice(2, -2)));
      } else parent.append(document.createTextNode(part));
    }
  }
  function message(role, text, sources = [], notes = []) {
    const article = element('article', undefined, `message ${role}`);
    const body = element('div', undefined, 'message-body');
    article.append(element('div', role === 'user' ? 'YOU' : 'RAG NUTRITION ASSISTANT', 'message-role'), body);
    if (role === 'user') body.textContent = text;
    else {
      let list;
      for (const line of text.split('\n')) {
        if (!line.trim()) { list = null; continue; }
        const bullet = line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)/);
        if (bullet) {
          if (!list) { list = element('ul'); body.append(list); }
          const item = element('li');
          inline(item, bullet[1], sources, notes);
          list.append(item);
        } else {
          list = null;
          const p = element('p');
          inline(p, line.replace(/^#{1,6}\s+/, ''), sources, notes);
          body.append(p);
        }
      }
      const actions = element('div', undefined, 'answer-actions');
      if (sources.length) {
        const button = element('button', `View ${sources.length} sources`, 'text-button');
        button.onclick = () => showSources(sources, notes, sources[0].label);
        actions.append(button);
      }
      body.append(actions);
      if (notes.length) body.append(element('p', notes.join('\n'), 'notice'));
    }
    $('messages').append(article);
    return article;
  }
  $('question').addEventListener('input', controls);
  $('question').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!$('send').disabled) $('chat-form').requestSubmit();
    }
  });
  document.querySelectorAll('.suggestion').forEach((button) => {
    button.onclick = () => {
      if (sending) return;
      $('question').value = button.querySelector('.question-label').textContent.trim();
      controls();
      $('question').focus();
    };
  });
  $('new-chat').onclick = () => { clearChat(); $('question').focus(); };
  $('library-nav').onclick = () => {
    const hidden = $('source-column').classList.toggle('hidden');
    $('library-nav').setAttribute('aria-expanded', String(!hidden));
    if (!hidden) $('source-column').scrollIntoView({ block: 'nearest' });
  };
  $('close-auth').onclick = () => $('auth-dialog').close();
  $('auth-toggle').onclick = () => {
    signup = !signup;
    $('auth-title').textContent = signup ? 'Start exploring.' : 'Welcome back.';
    $('auth-submit').textContent = signup ? 'Create account' : 'Sign in';
    $('auth-description').textContent = signup ? 'Create an account to ask up to 10 questions per day.' : 'Sign in to explore your nutrition textbook.';
    $('auth-toggle').textContent = signup ? 'Already registered? Sign in' : 'New here? Create an account';
    $('password').autocomplete = signup ? 'new-password' : 'current-password';
    $('password').minLength = signup ? 8 : 1;
    $('auth-message').textContent = '';
  };
  $('account-button').onclick = async () => {
    if (!session) return openAuth();
    $('account-button').disabled = true;
    try {
      const { error } = await client.auth.signOut();
      if (error) throw error;
      updateSession(null);
    } catch { notice('Unable to sign out. Check your connection and try again.'); }
    finally { $('account-button').disabled = false; }
  };
  $('auth-form').onsubmit = async (event) => {
    event.preventDefault();
    $('auth-submit').disabled = $('auth-toggle').disabled = true;
    $('auth-message').textContent = signup ? 'Creating your account…' : 'Signing in…';
    try {
      const credentials = { email: $('email').value.trim(), password: $('password').value };
      const { data, error } = signup
        ? await client.auth.signUp({ ...credentials, options: { emailRedirectTo: location.origin + '/' } })
        : await client.auth.signInWithPassword(credentials);
      if (error) throw error;
      $('password').value = '';
      if (data.session) {
        updateSession(data.session);
        $('auth-dialog').close();
        $('question').focus();
      } else $('auth-message').textContent = 'Check your email for a confirmation link, then return here to sign in.';
    } catch (error) { $('auth-message').textContent = error.message || 'Unable to sign in. Try again.'; }
    finally { $('auth-submit').disabled = $('auth-toggle').disabled = false; }
  };
  $('chat-form').onsubmit = async (event) => {
    event.preventDefault();
    const question = $('question').value.trim();
    if (!question || question.length > 1000 || sending || !client) return;
    if (!session) return openAuth();
    unlockSound();
    const ticket = generation;
    sending = true;
    controls();
    notice();
    const abort = new AbortController();
    controller = abort;
    const timeout = setTimeout(() => abort.abort(), 115000);
    let requested = false;
    try {
      const { data, error } = await client.auth.getSession();
      if (ticket !== generation) return;
      if (error || !data.session) { updateSession(null); openAuth(); return; }
      $('welcome').classList.add('hidden');
      message('user', question);
      showSources([]);
      requested = true;
      const response = await fetch(`${client.supabaseUrl}/functions/v1/nutrition-chat`, {
        method: 'POST', signal: abort.signal,
        headers: { 'Content-Type': 'application/json', apikey: window.NUTRITION_CONFIG.supabaseKey, Authorization: `Bearer ${data.session.access_token}` },
        body: JSON.stringify({ question }),
      });
      const result = await response.json().catch(() => null);
      if (ticket !== generation) return;
      if (!response.ok) {
        const failure = new Error(result?.error?.message || `Unable to answer (HTTP ${response.status}).`);
        failure.code = result?.error?.code;
        failure.status = response.status;
        const wait = Number(response.headers.get('Retry-After'));
        if (wait > 0) failure.message += ` Try again in about ${Math.ceil(wait / 60)} minutes.`;
        if (result?.request_id) failure.message += ` Reference: ${result.request_id}`;
        throw failure;
      }
      if (typeof result?.answer !== 'string' || !Array.isArray(result.sources)) throw new Error('The server returned an unexpected response.');
      const notes = Array.isArray(result.review_notes) ? result.review_notes : [];
      message('assistant', result.answer, result.sources, notes);
      showSources(result.sources, notes);
      if (result.status === 'answered') responseSound();
      if (result.usage) $('quota').textContent = `${result.usage.remaining} / ${result.usage.limit} attempts left · resets at 00:00 UTC`;
      $('question').value = '';
    } catch (error) {
      if (ticket !== generation) return;
      notice(error.name === 'AbortError' ? 'The request timed out. It may have counted toward your allowance. You can try again manually.' : (error.message || 'Unable to connect. Please try again.'));
      if (requested) $('quota').textContent = error.code === 'DAILY_LIMIT_REACHED' ? '0 / 10 attempts left · resets at 00:00 UTC' : 'Allowance updates after the next answer';
      if (error.status === 401) openAuth();
    } finally {
      clearTimeout(timeout);
      if (ticket === generation) { sending = false; controls(); $('question').focus(); }
    }
  };
  async function start() {
    try {
      const config = window.NUTRITION_CONFIG;
      if (!config?.supabaseUrl || !config?.supabaseKey) throw new Error('Setup pending: add the public Supabase connection settings to web/config.js.');
      if (!window.supabase?.createClient) throw new Error('Unable to load sign-in services. Check your connection and reload.');
      client = window.supabase.createClient(config.supabaseUrl, config.supabaseKey);
      client.auth.onAuthStateChange((_event, next) => updateSession(next));
      const { data, error } = await client.auth.getSession();
      if (error) throw error;
      updateSession(data.session);
    } catch (error) { notice(error.message || 'Unable to initialize the app.'); }
    controls();
  }
  start();
})();

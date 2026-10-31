'use strict';
// Spec: docs/specs/agy-integration.md
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../static/index.html'), 'utf8').split('<script>')[1].split('</script>')[0];

// Spec: docs/specs/agy-integration.md
function boot(storage = new Map(), search = '') {
  const nodes = new Map();
  const element = () => ({style: {}, dataset: {}, value: '', content: '', children: [],
    classList: {toggle() {}, add() {}, remove() {}}, addEventListener() {}, setAttribute() {},
    appendChild(child) {this.children.push(child);}, querySelector() {return null;}, focus() {},
    innerHTML: '', textContent: '', scrollHeight: 0, scrollTop: 0, clientHeight: 0});
  const sandbox = {URL, URLSearchParams, AbortController, TextDecoder, console, setTimeout,
    window: {}, location: {search, href: 'http://test/' + search}, history: {replaceState() {}},
    requestAnimationFrame: fn => fn(),
    localStorage: {getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k)},
    document: {querySelector: s => {if (!nodes.has(s)) nodes.set(s, element()); return nodes.get(s);},
      createElement: element, addEventListener() {}},
    fetch: async () => ({ok: true, json: async () => ({models: [{id: 'unchanged', name: 'Unchanged'}]})})};
  const context = vm.createContext(sandbox);
  vm.runInContext(source, context);
  return {storage, nodes, run: code => vm.runInContext(code, context), context};
}

// Spec: docs/specs/agy-integration.md
test('completed transcript restores synchronously with metadata and matching session', async () => {
  const first = boot();
  // Exercise the real send/finally hook with a terminal event stream, not saveHistory directly.
  const events = [{event: 'init', conversation_id: 'cid-1'}, {event: 'tool', tool: 'example'},
    {event: 'result', response: 'Hello 🌍', usage: {total_tokens: 9}}, {event: 'done'}];
  const chunks = [{value: Buffer.from(events.map(e => 'data: ' + JSON.stringify(e) + '\n\n').join('')), done: false}, {done: true}];
  first.context.fetch = async () => ({ok: true, body: {getReader: () => ({read: async () => chunks.shift()})}});
  first.run("modelSel.value = 'unchanged'");
  await first.run("input.value = 'Hello'; send()");
  assert.equal(first.run('modelSel.value'), 'unchanged');
  const again = boot(first.storage);
  assert.deepEqual(JSON.parse(again.run('JSON.stringify(messages)')), [
    {role: 'user', content: 'Hello'}, {role: 'assistant', content: 'Hello 🌍', status: 'complete', tool: 'example', metrics: '9 tokens (0 in / 0 out)'}]);
  assert.equal(again.nodes.get('#chat').children[0].children.length, 2);
  assert.equal(again.run('currentConversationId'), 'cid-1');
});

// Spec: docs/specs/agy-integration.md
test('Stop and error text survive reload; New chat removes snapshot and session', async () => {
  const first = boot();
  first.context.fetch = async () => {const error = new Error('Cancelled'); error.name = 'AbortError'; throw error;};
  await first.run("input.value = 'Stop me'; send()");
  let again = boot(first.storage);
  assert.match(again.run('messages[1].content'), /Response cancelled/);
  assert.equal(again.run('messages[1].status'), 'stopped');
  again.context.fetch = async () => {throw new Error('test failure');};
  await again.run("input.value = 'Fail'; send()");
  again = boot(first.storage);
  assert.match(again.run('messages[3].content'), /test failure/);
  assert.equal(again.run('messages[3].status'), 'error');
  again.run('clearChat()');
  assert.equal(first.storage.has('agy_chat_history'), false);
  assert.equal(boot(first.storage).run('messages.length'), 0);
});

// Spec: docs/specs/agy-integration.md
test('New chat during a pending response cannot resurrect history in finally', async () => {
  const first = boot();
  first.context.fetch = (_, options) => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => {
    const error = new Error('Cancelled'); error.name = 'AbortError'; reject(error);
  }));
  const pending = first.run("input.value = 'Cancel then clear'; send()");
  first.run('clearChat()');
  await pending;
  assert.equal(first.storage.has('agy_chat_history'), false);
  assert.equal(boot(first.storage).run('messages.length'), 0);
});

// Spec: docs/specs/agy-integration.md
for (const field of ['tool', 'metrics']) {
  test(`invalid restored ${field} cannot poison state or block subsequent sends`, async () => {
    const storage = new Map([
      ['agy_user_id', 'browser-user'], ['agy_conversation_id', 'cid-1'],
      ['agy_chat_history', JSON.stringify({userId: 'browser-user', conversationId: 'cid-1',
        messages: [{role: 'user', content: 'Prior'},
          {role: 'assistant', content: 'Invalid badge', [field]: {toString: null}}]})]
    ]);
    const page = boot(storage);
    assert.equal(page.run('messages.length'), 0);
    assert.equal(page.run('currentConversationId'), 'cid-1');
    let requests = 0;
    page.context.fetch = async (url, options) => {
      assert.equal(url, '/api/chat');
      assert.equal(JSON.parse(options.body).conversation_id, 'cid-1');
      requests++;
      const chunks = [{value: Buffer.from('data: {"event":"result","response":"Usable"}\n\ndata: {"event":"done"}\n\n'), done: false}, {done: true}];
      return {ok: true, body: {getReader: () => ({read: async () => chunks.shift()})}};
    };
    for (const prompt of ['First retry', 'Second retry']) {
      await page.run(`input.value = ${JSON.stringify(prompt)}; send()`);
      assert.equal(page.run('busy'), false);
      assert.equal(page.run('messages[messages.length - 1].content'), 'Usable');
    }
    assert.equal(requests, 2);
    assert.equal(boot(storage).run('messages.length'), 4);
  });
}

// Spec: docs/specs/agy-integration.md
test('wrong user, URL conversation, malformed JSON and invalid messages do not restore', () => {
  const first = boot();
  first.run("messages = [{role:'assistant', content:'Private'}]; currentConversationId = 'cid-1'; localStorage.setItem('agy_conversation_id','cid-1'); saveHistory()");
  assert.equal(boot(new Map(first.storage), '?cid=cid-other').run('messages.length'), 0);
  const wrongUser = new Map(first.storage); wrongUser.set('agy_user_id', 'other-user');
  assert.equal(boot(wrongUser).run('messages.length'), 0);
  for (const value of ['{', JSON.stringify({userId: first.storage.get('agy_user_id'), conversationId: 'cid-1', messages: [null]})]) {
    const bad = new Map(first.storage); bad.set('agy_chat_history', value);
    assert.equal(boot(bad).run('messages.length'), 0);
  }
  first.run("localStorage.setItem = () => {throw new Error('quota')}; saveHistory()");
});

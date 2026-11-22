// Offline integration checks; no live ILLiad, AWS or model requests are made.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.argv[2];
if (!root) throw Error('Pass the NonShib directory to test.');
const source = fs.readFileSync(path.join(root, 'js/custom.js'), 'utf8');
const browserId = '12345678-1234-4123-8123-123456789abc';
const storageKey = 'accessibleHtmlJob:952125';
const tick = () => new Promise(resolve => setImmediate(resolve));
let checks = 0;

function setup({saved, cacheStatus = 200, modalPlugin = true, analytics = true} = {}) {
    const storage = new Map(saved ? [[storageKey, JSON.stringify(saved)]] : []);
    const calls = [], opened = [], children = [];
    let clickHandler, focused, lastConfig, fallbackAnswer = false;
    class Element {
        constructor(action) {
            this.action = action; this.attrs = {}; this.dataset = {}; this.events = {};
            this.classList = {contains: () => false, toggle: () => {}};
        }
        setAttribute(name, value) {this.attrs[name] = value;}
        getAttribute(name) {return name === 'data-accessible-html-consent' ? this.action : this.attrs[name];}
        removeAttribute(name) {delete this.attrs[name];}
        hasAttribute(name) {return name in this.attrs;}
        addEventListener(name, fn) {this.events[name] = fn;}
        closest(selector) {return selector === '.menuAccessibleHtml' ? (this === button ? this : null) : (this.action ? this : null);}
        querySelector() {return new Element('no');}
        focus() {focused = this;}
        remove() {const index = children.indexOf(this); if (index >= 0) children.splice(index, 1);}
        click() {clickHandler({target: this, preventDefault() {}});}
    }
    const button = new Element(), status = new Element();
    button.dataset = {transactionNumber: '952125', title: 'Test article', pdfUrl: 'https://illiad.example/pdf'};
    button.attrs['aria-describedby'] = 'status'; button.innerHTML = 'Accessible HTML';
    const document = {
        body: {appendChild: node => children.push(node)},
        createElement: () => new Element(),
        getElementById: id => id === 'status' ? status : children.find(node => node.id === id),
        contains: node => node === button || children.includes(node),
        addEventListener: (name, fn) => {if (name === 'click') clickHandler = fn;}
    };
    function emit(node, name) {const fn = node.events[name]; delete node.events[name]; if (fn) fn();}
    function $(node) {
        return {ready: fn => fn(), width: () => 1200,
            one: (name, fn) => {node.events[name] = fn;},
            modal: options => {
                if (options === 'hide') emit(node, 'hidden.bs.modal');
                else if (options !== 'dispose') {lastConfig = options; emit(node, 'shown.bs.modal');}
            }};
    }
    $.fn = modalPlugin ? {modal() {}} : {};
    class FormData {
        constructor() {this.fields = new Map();}
        append(key, value) {this.fields.set(key, value);}
    }
    const window = {
        localStorage: {getItem: name => storage.get(name), setItem: (name, value) => storage.set(name, value), removeItem: name => storage.delete(name)},
        ILLIAD_ACCESSIBLE_HTML_API_BASE: 'https://api.example',
        confirm: () => fallbackAnswer, open: (...args) => opened.push(args), setTimeout
    };
    if (analytics) window.ILLIAD_ACCESSIBLE_HTML_ANALYTICS = {browserId: () => browserId};
    const env = {window, document, $, FormData, Promise,
        fetch: async (url, options) => {
            calls.push({url, options});
            if (url === button.dataset.pdfUrl) return {ok: true, headers: {get: () => 'application/pdf'}, blob: async () => ({pdf: true})};
            if (options.method === 'POST') return {ok: true, json: async () => ({job_id:'bbbbbbbbbbbb', status:'completed', viewer_url:'https://api.example/reader.html'})};
            return {ok: cacheStatus === 200, status: cacheStatus, json: async () => ({status: 'completed', error:'Unavailable'})};
        }};
    vm.runInNewContext(source, env);
    return {button, status, calls, opened, storage, window, env,
        modal: () => children.find(node => node.id === 'accessibleHtmlConsentModal'),
        focus: () => focused, config: () => lastConfig,
        close: () => emit(children[0], 'hidden.bs.modal'),
        choose: answer => {const node = children[0]; node.events.click({target: new Element(answer)});},
        fallback: answer => {fallbackAnswer = answer;}};
}

async function test(name, run) {await run(); checks++;}
(async () => {
    await test('No dismisses with focus restoration and no downloads', async () => {
        const app = setup(); app.button.click();
        const modal = app.modal();
        assert.equal(modal.attrs.role, 'dialog');
        assert.equal(modal.attrs['aria-modal'], 'true');
        assert.equal(modal.attrs['aria-labelledby'], 'accessibleHtmlConsentTitle');
        assert.equal(modal.attrs['aria-describedby'], 'accessibleHtmlConsentBody');
        assert.equal(app.focus().action, 'no');
        assert.equal(app.config().keyboard, true); assert.equal(app.config().focus, true);
        assert.equal(app.calls.length, 0);
        app.choose('no'); await tick();
        assert.equal(app.calls.length, 0); assert.equal(app.modal(), undefined);
        assert.equal(app.focus(), app.button);
        assert.match(app.status.textContent, /cancelled/);
        assert.equal(app.button.dataset.accessibleHtmlConfirming, undefined);
    });
    await test('Escape/close is cancellation', async () => {
        const app = setup(); app.button.click(); app.close(); await tick();
        assert.equal(app.calls.length, 0); assert.equal(app.focus(), app.button);
        assert.match(app.status.textContent, /cancelled/);
    });
    await test('Yes submits once, with analytics; repeat clicks cannot duplicate consent', async () => {
        const app = setup(); app.button.click(); app.button.click();
        assert.equal(app.calls.length, 0); app.choose('yes'); await tick();
        const posts = app.calls.filter(call => call.options.method === 'POST');
        assert.equal(posts.length, 1);
        assert.equal(posts[0].options.body.fields.get('reader_client_id'), browserId);
        assert.equal(posts[0].options.body.fields.get('transaction_number'), '952125');
        assert.equal(app.opened.length, 1); assert.equal(app.modal(), undefined);
        assert.equal(app.button.hasAttribute('disabled'), false);
    });
    await test('Missing analytics cannot block generation', async () => {
        const app = setup({analytics:false}); app.button.click(); app.choose('yes'); await tick();
        const post = app.calls.find(call => call.options.method === 'POST');
        assert.ok(post); assert.equal(post.options.body.fields.has('reader_client_id'), false);
    });
    await test('Fallback confirmation remains async-compatible when modal plugin is unavailable', async () => {
        const app = setup({modalPlugin:false}); app.button.click(); await tick();
        assert.equal(app.calls.length, 0);
        app.fallback(true); app.button.click(); await tick();
        assert.equal(app.calls.filter(call => call.options.method === 'POST').length, 1);
    });
    await test('Temporary cache failure preserves job and never starts a paid submission', async () => {
        const app = setup({saved:{status:'completed', job_id:'aaaaaaaaaaaa'}, cacheStatus:503});
        app.button.click(); await tick();
        assert.equal(app.calls.length, 1); assert.ok(app.storage.has(storageKey));
        assert.equal(app.modal(), undefined); assert.match(app.status.textContent, /try again later/);
    });
    await test('Expired cache is cleared without automatic regeneration', async () => {
        const app = setup({saved:{status:'completed', job_id:'aaaaaaaaaaaa'}, cacheStatus:410});
        app.button.click(); await tick();
        assert.equal(app.calls.length, 1); assert.equal(app.storage.has(storageKey), false);
        assert.equal(app.modal(), undefined); assert.match(app.status.textContent, /expired/);
    });
    await test('A genuinely missing old job offers consent again before downloading', async () => {
        const app = setup({saved:{status:'completed', job_id:'aaaaaaaaaaaa'}, cacheStatus:404});
        app.button.click(); await tick();
        assert.ok(app.modal()); assert.equal(app.calls.length, 1);
        app.choose('no'); await tick(); assert.equal(app.calls.length, 1);
    });
    await test('Helper load order, no unused iframe, and separate retention notices', async () => {
        const head = fs.readFileSync(path.join(root, 'include_head.html'), 'utf8');
        const viewer = fs.readFileSync(path.join(root, 'AccessibleHtmlViewer.html'), 'utf8');
        const menu = fs.readFileSync(path.join(root, 'ILLiadMainMenu.html'), 'utf8');
        assert.ok(head.indexOf('js/accessibleHtmlAnalytics.js') < head.indexOf('js/custom.js'));
        assert.ok(viewer.indexOf('js/accessibleHtmlAnalytics.js') < viewer.indexOf('js/accessibleHtmlViewer.js'));
        assert.doesNotMatch(viewer, /<iframe\b/i);
        assert.match(menu, /PDF articles are available for 30 days from delivery date/);
        assert.match(menu, /Generated HTML pages are available for 30 days from first conversion/);
    });
    await test('Viewer preserves local fragment links and resolves remote assets over HTTPS', async () => {
        const base = {removed:false, remove() {this.removed=true;}};
        const asset = attrs => ({attrs, getAttribute:name=>attrs[name], setAttribute:(name,value)=>{attrs[name]=value;}});
        const image = asset({src:'images/figure.png'});
        const section = asset({href:'#reader-section-2'});
        const script = asset({src:'reader.js'});
        let load, written = '';
        const page = {querySelectorAll:selector=>selector==='base'?[base]:[image,section,script],
            documentElement:{get outerHTML() {return `<html><head>${base.removed?'':'<base href="https://api.example/">'}</head><body><a href="${section.attrs.href}">Section</a><img src="${image.attrs.src}"></body></html>`;}}};
        const env = {
            URL, URLSearchParams,
            window:{location:{href:'https://illiad.example/nonshib/AccessibleHtmlViewer.html?job_id=aaaaaaaaaaaa',
                protocol:'https:', search:'?job_id=aaaaaaaaaaaa'}, ILLIAD_ACCESSIBLE_HTML_API_BASE:'https://api.example'},
            document:{addEventListener:(_name,fn)=>{load=fn;}, getElementById:()=>null, open() {},close() {},write:html=>{written=html;}},
            DOMParser:class {parseFromString() {return page;}},
            fetch:async url=>url.endsWith('/result')?{ok:true,json:async()=>({viewer_url:'http://api.example/runs/aaaaaaaaaaaa/output/reader.html'})}:{ok:true,text:async()=>'<html></html>'}
        };
        vm.runInNewContext(fs.readFileSync(path.join(root, 'js/accessibleHtmlViewer.js'), 'utf8'), env);
        load(); await tick();
        assert.equal(section.attrs.href, '#reader-section-2');
        assert.equal(image.attrs.src, 'https://api.example/runs/aaaaaaaaaaaa/output/images/figure.png');
        assert.equal(script.attrs.src, 'https://api.example/runs/aaaaaaaaaaaa/output/reader.js');
        assert.doesNotMatch(written, /<base\b/);
        assert.match(written, /<!doctype html>/i);
    });
    console.log(`${checks} ILLiad frontend checks passed`);
})().catch(error => {console.error(error); process.exitCode = 1;});

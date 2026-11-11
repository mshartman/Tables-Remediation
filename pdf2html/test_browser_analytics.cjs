// Offline checks for the ILLiad first-party analytics helper.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(process.argv[2], 'utf8');
const key = 'illiad_accessible_html_browser_v1';
const first = '12345678-1234-4123-8123-123456789abc';
const second = '12345678-1234-4123-9123-123456789abc';
let checks = 0;
function load({storage = new Map(), navigator = {}, window = {}} = {}) {
    let calls = 0;
    const env = {navigator, window: {
        crypto: {randomUUID: () => { calls++; return second; }},
        localStorage: {getItem: name => storage.get(name), setItem: (name, value) => storage.set(name, value)},
        ...window
    }};
    vm.runInNewContext(source, env);
    return {api: env.window.ILLIAD_ACCESSIBLE_HTML_ANALYTICS, storage, calls: () => calls};
}
function test(name, run) {run(); checks++;}
test('lazy, stable across reloads, no usernames', () => {
    const session = load();
    assert.equal(session.calls(), 0);
    assert.equal(session.api.browserId(), second);
    assert.equal(session.api.browserId(), second);
    assert.equal(session.calls(), 1);
    assert.equal(load({storage: session.storage}).api.browserId(), second);
    assert.equal(session.api.headers()['X-Accessible-Reader-Id'], second);
    assert.equal(session.storage.size, 1);
});
test('expires after 90 days', () => {
    const storage = new Map([[key, JSON.stringify({id:first, created:Date.now()-91*86400000})]]);
    assert.equal(load({storage}).api.browserId(), second);
});
test('valid record reused, corrupt and future records replaced', () => {
    for (const raw of ('invalid|{}|' + JSON.stringify({id:first,created:Date.now()+86400000})).split('|')) {
        assert.equal(load({storage:new Map([[key,raw]])}).api.browserId(), second);
    }
    assert.equal(load({storage:new Map([[key,JSON.stringify({id:first,created:Date.now()})]])}).api.browserId(), first);
});
test('DNT, GPC, site opt-out, missing crypto, blocked storage fail open', () => {
    for (const options of [
        {navigator:{doNotTrack:'1'}}, {navigator:{globalPrivacyControl:true}},
        {window:{doNotTrack:'1'}}, {window:{ILLIAD_ACCESSIBLE_HTML_ANALYTICS_ENABLED:false}},
        {window:{crypto:null}}, {window:{localStorage:{getItem:()=>{throw Error('blocked');}}}},
        {window:{localStorage:{getItem:()=>null,setItem:()=>{throw Error('quota');}}}}
    ]) {
        const session = load(options);
        assert.equal(session.api.browserId(), '');
        assert.equal(Object.keys(session.api.headers()).length, 0);
    }
});
async function viewerCheck(viewerUrl, failHeader) {
    const viewerSource = fs.readFileSync(path.join(path.dirname(process.argv[2]), 'accessibleHtmlViewer.js'), 'utf8');
    const calls = [];
    let handler;
    const env = {
        URL, URLSearchParams,
        window: {location:{href:'https://illiad.example/AccessibleHtmlViewer.html?job_id=aaaaaaaaaaaa',
                            search:'?job_id=aaaaaaaaaaaa',protocol:'https:'},
                 ILLIAD_ACCESSIBLE_HTML_API_BASE:'https://api.example',
                 ILLIAD_ACCESSIBLE_HTML_ANALYTICS:{headers:()=>({'X-Accessible-Reader-Id':first})}},
        document:{addEventListener:(_name, fn)=>{handler=fn;},getElementById:()=>null,
                  open:()=>{},write:()=>{},close:()=>{}},
        DOMParser:class {parseFromString() {return {querySelectorAll:()=>[],documentElement:{outerHTML:'<html></html>'}};}},
        fetch:async (url, options)=>{
            calls.push({url,options});
            if (calls.length===1) return {ok:true,json:async()=>({viewer_url:viewerUrl})};
            if (failHeader && options.headers && Object.keys(options.headers).length) throw TypeError('CORS');
            return {ok:true,text:async()=>'<html></html>'};
        }
    };
    vm.runInNewContext(viewerSource, env);
    handler();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls[0].options.headers, undefined, 'Status lookup must not carry identity');
    return calls;
}
(async () => {
    let calls = await viewerCheck('https://api.example/runs/aaaaaaaaaaaa/output/remediated.html', false);
    assert.equal(calls.length, 2);
    assert.equal(calls[1].options.headers['X-Accessible-Reader-Id'], first);
    checks++;
    calls = await viewerCheck('https://api.example/runs/aaaaaaaaaaaa/output/remediated.html', true);
    assert.equal(calls.length, 3);
    assert.equal(calls[2].options.headers, undefined, 'Optional tracking must fail open');
    checks++;
    calls = await viewerCheck('https://untrusted.example/file.html', false);
    assert.equal(Object.keys(calls[1].options.headers).length, 0, 'No browser ID to an unrelated origin');
    checks++;
    console.log(`${checks} browser analytics checks passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});

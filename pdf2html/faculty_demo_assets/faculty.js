'use strict';
(function () {
  const byId = id => document.getElementById(id);
  const form = byId('upload-form');
  const fileInput = byId('pdf-file');
  const titleInput = byId('document-title');
  const consent = byId('consent');
  const drop = byId('drop-zone');
  const panel = byId('processing-panel');
  const stageOrder = ['extracting', 'structuring', 'checking'];
  const messages = {queued: 'Your document is waiting for the local processor.',
    extracting: 'Reading the text and elements in your PDF.',
    structuring: 'Organizing a flexible HTML reading view.',
    checking: 'Checking the structure and preparing your review notes.'};
  const jobs = new Map();
  let selectedFile = null;
  let busy = false;
  let timer = null;

  function error(message, control) {
    const node = byId('form-error');
    node.hidden = false;
    node.textContent = message;
    if (control) {control.setAttribute('aria-invalid', 'true'); control.focus();}
  }
  function clearError() {
    byId('form-error').hidden = true;
    byId('form-error').textContent = '';
    [fileInput, consent].forEach(node => node.removeAttribute('aria-invalid'));
  }
  function size(bytes) {return bytes >= 1024 * 1024 ? (bytes / (1024 * 1024)).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';}
  function choose(file) {
    if (busy || !file) return;
    clearError();
    selectedFile = null;
    byId('selected-file').hidden = true;
    byId('file-selection').textContent = 'No file selected.';
    if (!/\.pdf$/i.test(file.name)) {error('Please choose a PDF. Other document formats are not supported in this demo.', fileInput); return;}
    if (!file.size || file.size > 20 * 1024 * 1024) {error('Choose a PDF smaller than 20 MB.', fileInput); return;}
    selectedFile = file;
    byId('selected-name').textContent = file.name;
    byId('selected-size').textContent = size(file.size) + ' \u00b7 Ready to process locally';
    byId('selected-file').hidden = false;
    byId('file-selection').textContent = file.name + ' selected. ' + size(file.size) + '.';
  }
  fileInput.addEventListener('change', () => choose(fileInput.files[0]));
  byId('remove-file').addEventListener('click', () => {
    selectedFile = null; fileInput.value = ''; byId('selected-file').hidden = true;
    byId('file-selection').textContent = 'File removed. Choose another PDF.';
    fileInput.focus();
  });
  ['dragenter', 'dragover'].forEach(name => drop.addEventListener(name, event => {
    event.preventDefault(); if (!busy) drop.classList.add('drag-over');
  }));
  ['dragleave', 'drop'].forEach(name => drop.addEventListener(name, event => {
    event.preventDefault(); drop.classList.remove('drag-over');
  }));
  drop.addEventListener('drop', event => {
    if (event.dataTransfer.files.length > 1) {error('Choose one PDF at a time for this demo.', fileInput); return;}
    choose(event.dataTransfer.files[0]);
  });

  async function responseJson(response) {
    let value;
    try {value = await response.json();} catch (_) {throw new Error('The local demo could not respond. Make sure the demo server is still running.');}
    if (!response.ok) throw new Error(value.error || 'The request could not be completed. Try again.');
    return value;
  }
  byId('sample-button').addEventListener('click', async () => {
    const button = byId('sample-button');
    button.disabled = true;
    clearError();
    try {
      const response = await fetch('/sample.pdf', {credentials: 'same-origin'});
      if (!response.ok) throw new Error('The sample document could not be loaded. Try again.');
      choose(new File([await response.blob()], 'inclusive-course-materials.pdf', {type: 'application/pdf'}));
      if (!titleInput.value.trim()) titleInput.value = 'Designing inclusive course materials';
    } catch (issue) {error(issue.message || 'Could not load the sample document.');}
    finally {button.disabled = false;}
  });

  function itemNode(job) {
    const item = document.createElement('li'); item.className = 'document-item';
    const info = document.createElement('div'); info.className = 'document-info';
    const title = document.createElement('strong'); title.textContent = job.title;
    const meta = document.createElement('p');
    meta.textContent = job.pages + (job.pages === 1 ? ' page' : ' pages') + ' \u00b7 ' + size(job.bytes) + ' \u00b7 ' + job.filename;
    info.append(title, meta);
    const actions = document.createElement('div'); actions.className = 'document-actions';
    const status = document.createElement('span'); status.className = 'status-pill';
    if (job.status === 'completed') {
      status.textContent = 'Ready for review';
      const link = document.createElement('a'); link.href = job.view_url;
      link.textContent = 'Open reading view'; link.target = '_blank'; link.rel = 'noopener';
      link.setAttribute('aria-label', 'Open ' + job.title + ' reading view in a new tab');
      actions.append(status, link);
    } else {
      status.classList.add(job.status === 'failed' ? 'failed' : 'pending');
      status.textContent = job.status === 'failed' ? 'Could not process' : 'Processing locally';
      actions.append(status);
    }
    item.append(info, actions);
    return item;
  }
  function renderJobs() {
    const list = byId('document-items'); list.replaceChildren();
    Array.from(jobs.values()).reverse().forEach(job => list.append(itemNode(job)));
    byId('documents-empty').hidden = jobs.size > 0;
    byId('nav-count').textContent = String(jobs.size);
  }
  function showProgress(job, focus) {
    jobs.set(job.id, job); renderJobs();
    form.hidden = true; panel.hidden = false;
    byId('processing-filename').textContent = job.title;
    const complete = job.status === 'completed';
    panel.classList.toggle('is-ready', complete);
    byId('processing-heading').textContent = complete ? 'Your reading view is ready.' : job.status === 'failed' ? 'This document needs another look.' : 'Creating your reading view';
    let current = stageOrder.indexOf(job.phase);
    if (current < 0) current = 0;
    document.querySelectorAll('#progress-steps > li').forEach((step, index) => {
      const done = complete || index < current;
      step.classList.toggle('done', done);
      step.classList.toggle('current', !complete && job.status !== 'failed' && index === current);
      step.querySelector('.step-dot').textContent = done ? '\u2713' : String(index + 1);
      if (step.classList.contains('current')) step.setAttribute('aria-current', 'step');
      else step.removeAttribute('aria-current');
    });
    const message = complete ? 'HTML created. Human accessibility review is still required.' : job.error || messages[job.phase] || messages.queued;
    if (byId('processing-status').textContent !== message) byId('processing-status').textContent = message;
    ['result-summary', 'result-actions', 'review-reminder'].forEach(id => byId(id).hidden = !complete);
    byId('another-button').hidden = !complete && job.status !== 'failed';
    if (complete) {
      const summary = byId('result-summary'); summary.replaceChildren();
      [job.pages + (job.pages === 1 ? ' page' : ' pages'), job.headings + ' section headings', job.tables + ' tables'].forEach(text => {
        const badge = document.createElement('span'); badge.textContent = text; summary.append(badge);
      });
      if (job.pages_without_text) byId('processing-status').textContent = 'HTML created, but ' + job.pages_without_text + ' page(s) have no selectable text. This demo does not perform OCR. Review those pages before using the result.';
      byId('open-result').href = job.view_url;
      byId('download-result').href = job.download_url;
      byId('download-report').href = job.report_url;
    }
    if (focus) byId('processing-heading').focus({preventScroll: true});
    if (complete || job.status === 'failed') busy = false;
  }
  async function poll(id, failures = 0) {
    try {
      const response = await fetch('/api/demo/jobs/' + encodeURIComponent(id), {credentials: 'same-origin', cache: 'no-store'});
      const job = await responseJson(response);
      const wasReady = jobs.get(id)?.status === 'completed';
      showProgress(job, job.status === 'completed' && !wasReady);
      if (job.status === 'queued' || job.status === 'processing') timer = window.setTimeout(() => poll(id), 1200);
    } catch (_) {
      if (failures < 3) {timer = window.setTimeout(() => poll(id, failures + 1), 2500); return;}
      byId('processing-status').textContent = 'We lost contact with the local processor. Your document may still be processing. Reload this page to check its status before uploading again.';
      busy = false;
    }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    clearError();
    if (!selectedFile) {error('Choose a PDF or try the sample document first.', fileInput); return;}
    if (!consent.checked) {error('Please confirm your permission to process the document.', consent); return;}
    busy = true;
    const button = byId('submit-button'); button.disabled = true;
    form.setAttribute('aria-busy', 'true');
    const body = new FormData(); body.append('pdf_file', selectedFile, selectedFile.name);
    body.append('title', titleInput.value.trim()); body.append('consent', 'yes');
    try {
      const response = await fetch('/api/demo/jobs', {method: 'POST', credentials: 'same-origin',
        headers: {'X-Demo-CSRF': document.querySelector('meta[name="demo-csrf"]').content}, body});
      const job = await responseJson(response);
      showProgress(job, true);
      if (job.status === 'queued' || job.status === 'processing') poll(job.id);
    } catch (issue) {busy = false; error(issue.message || 'Could not submit the document. Check the local server.');}
    finally {button.disabled = false; form.removeAttribute('aria-busy');}
  });
  byId('another-button').addEventListener('click', () => {
    window.clearTimeout(timer); busy = false; panel.hidden = true; form.hidden = false;
    selectedFile = null; fileInput.value = ''; titleInput.value = ''; consent.checked = false;
    byId('selected-file').hidden = true;
    byId('file-selection').textContent = 'Choose a PDF for your next document.';
    clearError(); fileInput.focus();
  });
  consent.addEventListener('change', () => consent.removeAttribute('aria-invalid'));
  async function restore() {
    try {
      const response = await fetch('/api/demo/jobs', {credentials: 'same-origin', cache: 'no-store'});
      const saved = await responseJson(response);
      saved.jobs.slice().reverse().forEach(job => jobs.set(job.id, job));
      renderJobs();
      const pending = saved.jobs.find(job => ['queued', 'processing'].includes(job.status));
      if (pending) {busy = true; showProgress(pending, false); poll(pending.id);}
    } catch (_) {error('The local demo server is not available. Start faculty_demo.py and open http://127.0.0.1:8002.');}
  }
  restore();
})();

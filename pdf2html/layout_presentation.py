"""Document chrome and navigation for the page-faithful reader."""
from collections import Counter
from difflib import SequenceMatcher
import re
import unicodedata

from bs4 import BeautifulSoup


def reading_label(node):
    semantic = node.select_one('.txt')
    if semantic:
        text = semantic.get_text()
    else:
        copy = BeautifulSoup(str(node), 'html.parser')
        for hidden in copy.select('[aria-hidden="true"]'):
            hidden.decompose()
        text = copy.get_text(' ', strip=True)
    return ' '.join(unicodedata.normalize('NFKC', text).split())


def _page_position(node, pages):
    page = node.find_parent(class_='page')
    if page is None:
        return None, None, None
    index = next((i for i, item in enumerate(pages) if item is page), None)
    line = node.select_one('.ln')
    y = re.search(r'top:([\d.]+)pt', line.get('style', '')) if line else None
    height = re.search(r'height:([\d.]+)pt', page.get('style', ''))
    return index, float(y[1]) if y else None, float(height[1]) if height else None


def _body_start(pages):
    """Find an early explicit body label; abstain if substantive text precedes it."""
    preceding = 0
    tags = ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
    for pi, page in enumerate(pages[:3]):
        blocks = [n for n in page.find_all(tags, recursive=False)
                  if 'artifact' not in n.get('class', []) and n.get('aria-hidden') != 'true']
        prefix = 0
        for index, node in enumerate(blocks):
            label = reading_label(node)
            compact = re.sub(r'\s+', '', re.sub(r'^(?:1(?:\.0)?|i)(?:\.\s*|\s+)', '', label, flags=re.I)).rstrip(':.').casefold()
            standalone = compact in {'abstract', 'summary', 'introduction'}
            inline = re.match(r'^(?:abstract|summary)\s*[:\u2013\u2014-]\s*\S', label, re.I) and len(label) >= 140
            following = any(len(reading_label(other)) >= 120 for other in blocks[index+1:index+4])
            if (standalone and following or inline) and (inline or not node.find('a', href=True)):
                if preceding <= 600 and prefix <= 600:
                    return pi, node
                return None
            if len(label) >= 200:
                prefix += len(label)
        preceding += prefix
    return None


def navigation_headings(soup, title=None, review=None):
    headings = soup.select('main h1, main h2, main h3, main h4, main h5, main h6')
    pages = soup.select('main .page')
    anchor = _body_start(pages)
    positions = {id(n): i for i, n in enumerate(soup.select('main *'))}
    margin_labels = []
    def key(text):
        return ' '.join(re.findall(r'[^\W\d_]+', text.casefold()))
    for node in soup.select('main .page > p, main .page > h1, main .page > h2, main .page > h3, main .page > h4, main .page > h5, main .page > h6'):
        pi, y, height = _page_position(node, pages)
        label = reading_label(node)
        if pi is not None and y is not None and height and (y < height*.12 or y > height*.92) and len(label) >= 15:
            margin_labels.append((pi, key(label), node.name == 'p'))
    result = []
    excluded = []
    for heading in headings:
        label = reading_label(heading)
        pi, y, height = _page_position(heading, pages)
        margin = y is not None and height and (y < height*.12 or y > height*.92)
        normalized = key(label)
        folio = re.fullmatch(r'\d+\s+.+\b\d+\s*\(\d+\)', label)
        running = margin and len(label) >= 15 and bool(normalized) and any(other != pi and (paragraph or folio) and (
            normalized == text or (len(normalized.split()) >= 8 and len(text.split()) >= 8
                                   and SequenceMatcher(None, normalized, text).ratio() >= .8)) for other, text, paragraph in margin_labels)
        reason = None
        demote = False
        if heading.find_parent(class_='fallback-text'):
            reason = 'generated_page_fallback'
        elif heading.find_parent(['figure', 'table']):
            reason = 'embedded_object_heading'
        elif running:
            reason = 'repeated_margin_header'
            demote = True
        elif anchor and pi is not None and (pi < anchor[0] or (
                pi == anchor[0] and positions[id(heading)] < positions[id(anchor[1])])):
            reason = 'front_matter_before_body'
            demote = True
        elif (not anchor and pi == 0 and title and len(label.split()) >= 4
              and re.sub(r'\W+', '', label.casefold()) == re.sub(r'\W+', '', unicodedata.normalize('NFKC', title).casefold())):
            reason = 'document_title'
            demote = True
        if reason:
            heading['data-layout-nav-exclude'] = reason
            if demote:
                # Keep the source text, geometry and IDs; remove false heading semantics.
                heading.name = 'p'
            if reason == 'repeated_margin_header':
                heading['data-layout-running-header'] = ''
            excluded.append({'label': label, 'page': pi+1 if pi is not None else None, 'reason': reason})
            continue
        if not label:
            heading.name = 'p'
            continue
        result.append((heading, label))
    if review is not None:
        review.update(section_count=len(result), excluded=excluded,
                      body_start={'page':anchor[0]+1, 'label':reading_label(anchor[1])[:160]} if anchor else None,
                      manual_review_required=True)
    return result


CSS = '''
:root{--desk:#f3f5f7;--paper:#fff;--ink:#18202a;--link:#124e76;--rule:#cbd5df}
html{background:#f3f5f7;scroll-padding-top:1.5rem}
body{padding:24px 16px 48px;background:#f3f5f7;color:#18202a}
.layout-intro,main{box-sizing:border-box;width:100%;max-width:var(--reader-width,var(--page-view-width,var(--document-width)));margin:0 auto}
.layout-intro{padding:0 0 20px}.layout-intro h1{margin:12px 0 18px;font-size:clamp(1.4rem,3vw,2rem);line-height:1.25}
.layout-eyebrow{font:600 .75rem/1.4 Verdana,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#40556a;margin:14px 0 0}
.layout-intro a{color:#124e76}.layout-controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.layout-controls button{font:inherit;background:#183d57;color:white;border:1px solid #183d57;border-radius:6px;padding:9px 16px;cursor:pointer}
.layout-controls #viewToggle{position:relative;display:inline-flex;align-items:center;gap:10px;min-height:44px;padding:10px 14px;background:white;color:#183d57;border:1px solid #718294;border-radius:999px}
.layout-controls #viewToggle::before{content:"";display:block;width:2.6rem;height:1.4rem;background:#596b7d;border:1px solid currentColor;border-radius:999px;flex:none}
.layout-controls #viewToggle::after{content:"";position:absolute;left:calc(14px + .2rem);top:50%;width:1rem;height:1rem;background:white;border:1px solid #183d57;border-radius:50%;transform:translateY(-50%)}
.layout-controls #viewToggle[aria-checked="true"]::before{background:#183d57}
.layout-controls #viewToggle[aria-checked="true"]::after{transform:translate(1.2rem,-50%)}
.layout-controls #viewToggle:disabled{cursor:default;border-style:dashed}
.view-switch-state{font-size:.85em;font-weight:bold}
@media(forced-colors:active){.layout-controls #viewToggle::before{background:Canvas}.layout-controls #viewToggle::after{background:CanvasText;border-color:CanvasText}.layout-controls #viewToggle[aria-checked="true"]::before{background:Highlight}}
.layout-controls p{font-size:.9rem;margin:0;color:#40556a;flex:1 1 220px}
.layout-intro nav{margin:18px 0 0;border-block:1px solid #cbd5df;display:grid;grid-template-columns:1fr 1fr;gap:0 20px}
.layout-intro details{padding:10px 0}.layout-intro summary{cursor:pointer;font-weight:bold;padding:4px 0}
.layout-intro ul,.layout-intro ol{padding-left:24px;margin:8px 0}.layout-intro li{padding:3px 0}
.layout-intro li ul{margin:4px 0}.layout-intro .layout-nav-note{grid-column:1/-1;margin:12px 0 0;color:#40556a;font-size:.9rem}
.layout-intro .layout-details{font-size:.88rem;color:#40556a}
.layout-intro a:focus-visible,button:focus-visible,summary:focus-visible,main :focus-visible{outline:3px solid #12639a;outline-offset:3px}
main [data-reader-image-notice]{box-sizing:border-box;padding:12px 16px;margin:0 0 20px;background:#fff8e8;border-left:3px solid #8a5c00;font-size:.9rem}
main [data-reader-image-notice] p{margin:0}
main [data-layout-scan-warning]{box-sizing:border-box;padding:12px 16px;margin:0 0 20px;background:#fff8e8;border-left:3px solid #8a5c00;font-size:.9rem}
.page[data-layout-image-text="hidden-source-text"] .vis{display:none}
.page[data-layout-image-text="hidden-source-text"] .txt:focus-within{opacity:1;background:white;overflow:visible;box-shadow:0 0 0 3px #12639a}
.reader-source-scan{display:none}
body[data-layout-reading="true"] .reader-source-scan{display:block;margin:1rem 0;padding:12px;border:1px solid #cbd5df}
.reader-source-scan img{max-width:100%;height:auto}
.reader-margin-notes{display:none}
body[data-layout-reading="true"] .reader-margin-notes{display:block;padding:12px;margin:1rem 0;border:1px solid #cbd5df;overflow-wrap:anywhere}
body[data-layout-reading="true"] .source-margin-visual{display:none}
.page{max-width:none;border:1px solid #d9e0e7;box-sizing:content-box;box-shadow:0 3px 14px #18202a0c}
.fallback-page{height:auto!important}.fallback-image{margin:0}.fallback-image img{width:100%;height:auto}
.fallback-text{padding:1rem}.fallback-text h2{font-size:1.2rem!important}
body.reflow,body[data-layout-reading="true"]{--reader-width:70rem;background:#f3f5f7}
body.reflow .page,body[data-layout-reading="true"] .page{box-sizing:border-box;padding:24px;background:white;border:1px solid #d9e0e7;max-width:none}
body.reflow .page .txt,body.auto-reflow .page .txt{left:auto;top:auto}
body[data-layout-reading="true"] .fallback-image{display:none}
.sem-table th,.sem-table td{overflow-wrap:anywhere}
@media(max-width:900px){.fallback-image{display:none}}
@media(max-width:600px){.layout-intro nav{grid-template-columns:1fr;gap:0}body{padding:16px 12px 32px}}
'''


SCRIPT = r'''
(function(){
  function sourceBox(target){
    if(target.matches && target.matches('.txt')) return target;
    return target.querySelector && target.querySelector('.txt') || target;
  }
  function reveal(target, force){
    var box=sourceBox(target), rect=box.getBoundingClientRect();
    if(force || rect.top<20 || rect.bottom>window.innerHeight-20){
      window.scrollTo({top:Math.max(0,window.scrollY+rect.top-24),left:window.scrollX,behavior:'instant'});
    }
  }
  function followHash(){
    var id;try{id=decodeURIComponent(window.location.hash.slice(1));}catch(ignore){return;}
    var target=document.getElementById(id);if(!target)return;
    target.focus({preventScroll:true});reveal(target,true);
  }
  document.addEventListener('click',function(e){
    var a=e.target.closest('a[href^="#"]');if(!a || e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey)return;
    var target=document.getElementById(a.getAttribute('href').slice(1));if(!target)return;
    e.preventDefault();target.focus({preventScroll:true});reveal(target,true);
    var destination=new URL(window.location.href);destination.hash=target.id;
    try{history.pushState(null,'',destination.href);}catch(ignore){}
  });
  document.addEventListener('focusin',function(e){
    if(e.target.closest && e.target.closest('main')) reveal(e.target,false);
  });
  document.addEventListener('selectionchange',function(){
    var selection=window.getSelection();if(!selection || !selection.focusNode)return;
    var node=selection.focusNode.nodeType===1?selection.focusNode:selection.focusNode.parentElement;
    var text=node && node.closest('.txt');if(text)reveal(text,false);
  });
  window.addEventListener('hashchange',followHash);
  window.addEventListener('popstate',followHash);
  if(window.location.hash)followHash();
})();
'''


def decorate(soup, pages, geometry_only, title):
    navigation_review = {}
    headings = navigation_headings(soup, title, navigation_review)
    nav = soup.new_tag('nav', attrs={'aria-label':'Document navigation'})
    used_ids = {n['id'] for n in soup.select('[id]')}
    duplicates = Counter(n['id'] for n in soup.select('[id]'))
    labels = Counter(text for _, text in headings)
    accessible_names = set()
    for label, entries, ordered in (
        ('Article sections', [(h, text) for h,text in headings], False),
        ('Jump to page', [(p.select_one('.pb'), f'Page {i}') for i,p in enumerate(pages,1)], True)):
        if not entries:
            continue
        details = soup.new_tag('details')
        summary = soup.new_tag('summary'); summary.string = label; details.append(summary)
        listing = soup.new_tag('ol' if ordered else 'ul')
        stack = []
        for index,(target,text) in enumerate(entries,1):
            if not ordered:
                if not target.get('id') or duplicates[target['id']] > 1:
                    number = index
                    while f'layout-section-{number}' in used_ids:
                        number += 1
                    target['id'] = f'layout-section-{number}'
                    used_ids.add(target['id'])
                target['tabindex'] = '-1'
                if target.name == 'h1': target.name = 'h2'
                level = int(target.name[1])
                while stack and stack[-1][0] >= level:
                    stack.pop()
            item=soup.new_tag('li'); link=soup.new_tag('a',href='#'+target['id']);link.string=text
            item.append(link)
            if not ordered and stack:
                parent = stack[-1][1]
                nested = parent.find('ul', recursive=False)
                if nested is None:
                    nested = soup.new_tag('ul');parent.append(nested)
                nested.append(item)
            else:
                listing.append(item)
            if not ordered:
                if labels[text] > 1:
                    pi = _page_position(target, pages)[0]
                    context = stack[-1][2] if stack else f'Page {pi+1}' if pi is not None else f'Section {index}'
                    name = f'{text} ({context})'
                    if name in accessible_names:
                        name = f'{text} ({context}, section {index})'
                    link['aria-label'] = name
                    accessible_names.add(name)
                stack.append((level, item, text))
        details.append(listing);nav.append(details)
    if not headings:
        note = soup.new_tag('p', attrs={'class':'layout-nav-note'})
        note.string = 'No reliable article sections were identified. Use Jump to page to navigate this document.'
        nav.insert(0, note)
    intro = BeautifulSoup('<header class="layout-intro"><a href="#main-content">Skip to document</a>'
        '<div class="layout-controls"></div><p class="layout-eyebrow">Article reader</p><h1></h1>'
        '<details class="layout-details"><summary>About this reading view</summary><p></p></details></header>', 'html.parser').header
    intro.h1.string = title
    button = soup.find(id='viewToggle').extract()
    button.attrs.update(type='button', role='switch', **{'aria-checked':'false', 'aria-controls':'main-content',
                                                       'aria-describedby':'viewHelp', 'title':'Turn Reading view on or off.'})
    button.attrs.pop('aria-pressed', None)
    button.clear()
    label = soup.new_tag('span');label.string = 'Reading view';button.append(label)
    state = soup.new_tag('span', attrs={'class':'view-switch-state', 'data-view-state':'', 'aria-hidden':'true'})
    state.string = 'Off';button.append(state)
    intro.select_one('.layout-controls').append(button)
    help_text=soup.find(id='viewHelp')
    if help_text:
        help_text.extract()
    else:
        help_text=soup.new_tag('p', id='viewHelp')
    help_text.string='Off: Page view. On: Reading view, with text-spacing adjustments and screen-reader support.'
    intro.select_one('.layout-controls').append(help_text)
    intro.select_one('.layout-details p').string = (
        'This is an automatically reconstructed view. Reading order, headings, table structure and image descriptions require review. '
        + ('Structure was inferred locally from the PDF.' if geometry_only else 'Structure was supplied by Bedrock Data Automation.'))
    intro.select_one('.layout-details').insert_before(nav)
    toolbar=soup.select_one('.toolbar')
    if toolbar:toolbar.decompose()
    width=max(float(re.search(r'width:([\d.]+)pt',p['style'])[1]) for p in pages)
    soup.body['style']=f'--document-width:{width+2:.2f}pt'
    soup.body.insert(0,intro)
    soup.select_one('main')['tabindex']='-1'
    style=soup.new_tag('style');style.string=CSS;soup.head.append(style)
    script=soup.new_tag('script');script.string=SCRIPT;soup.body.append(script)
    return navigation_review

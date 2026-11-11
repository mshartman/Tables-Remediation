import json
from pathlib import Path
import shutil
import subprocess
import unittest

from bs4 import BeautifulSoup
from layout_presentation import reading_label, navigation_headings, SCRIPT
from layout_structure import refine_headings


class PresentationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for view runtime test')
    def test_full_view_runtime_scales_pages_and_clears_scale_for_reading(self):
        from layout_compare import iris_layout
        script=BeautifulSoup(iris_layout.TEMPLATE,'html.parser').script.string
        harness=r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const classes=new Set(),attrs={},properties={},events={},buttons={};
const body={style:{setProperty:(k,v)=>properties[k]=v},setAttribute:(k,v)=>attrs[k]=v,
 classList:{contains:k=>classes.has(k),toggle:(k,force)=>{const on=force===undefined?!classes.has(k):force;on?classes.add(k):classes.delete(k);return on;}}};
const state={textContent:'Off'},help={textContent:''};
const button={textContent:'Reading view',querySelector:()=>state,setAttribute:(k,v)=>buttons[k]=v,addEventListener:(k,v)=>events['button-'+k]=v};
const page={style:{width:'481.89pt'}};
const line={style:{},dataset:{w:'75'},offsetWidth:100,
 getBoundingClientRect:()=>{throw Error('Zoomed rectangle must not be used for line fitting');}};
const document={body,getElementById:id=>id==='viewToggle'?button:help,querySelector:()=>page,
 querySelectorAll:q=>q==='main > .page'?[page]:q==='.ln'?[line]:[],
 addEventListener:(k,v)=>events[k]=v,fonts:{ready:{then:fn=>fn()}}};
const window={innerWidth:1920,devicePixelRatio:1,matchMedia:()=>({matches:false,addEventListener:()=>{}}),
 addEventListener:(k,v)=>events[k]=v};
vm.runInNewContext(SCRIPT,{window,document});
const scale=Number(page.style.zoom);assert.ok(scale>2);assert.equal(attrs['data-layout-reading'],'false');
assert.equal(buttons['aria-checked'],'false');assert.equal(state.textContent,'Off');
assert.equal(button.textContent,'Reading view');assert.ok(!help.textContent.includes('flowing text'));
assert.equal(line.style.transform,'scaleX(1.0000)');
events['button-click']();assert.equal(page.style.zoom,'');assert.equal(attrs['data-layout-reading'],'true');
assert.equal(buttons['aria-checked'],'true');assert.equal(state.textContent,'On');assert.equal(button.textContent,'Reading view');
events['button-click']();assert.equal(Number(page.style.zoom),scale);
assert.equal(buttons['aria-checked'],'false');assert.equal(state.textContent,'Off');
window.innerWidth=1536;window.devicePixelRatio=1.25;events.resize();assert.equal(Number(page.style.zoom),scale);
window.innerWidth=960;window.devicePixelRatio=2;events.resize();
assert.equal(page.style.zoom,'');assert.equal(attrs['data-layout-reading'],'true');assert.equal(button.disabled,true);
assert.equal(buttons['aria-checked'],'true');assert.equal(state.textContent,'On (automatic)');
assert.ok(help.textContent.includes('automatically'));assert.equal(button.textContent,'Reading view');
window.innerWidth=1920;window.devicePixelRatio=1;events.resize();
assert.equal(button.disabled,false);assert.equal(buttons['aria-checked'],'false');assert.equal(state.textContent,'Off');
'''.replace('SCRIPT',json.dumps(script))
        subprocess.run(['node','-e',harness],check=True,capture_output=True)

    @unittest.skipUnless(shutil.which('node'), 'Node required for sizing execution')
    def test_page_view_expands_and_browser_zoom_is_not_cancelled(self):
        from layout_compare import iris_layout
        harness=r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const context={};vm.runInNewContext(SCRIPT,context);
const fit=context.pageSizing;
for(const width of [800,1024,1366,1920,2560,3840]){
  const size=fit(width,642.52,1,1,1,false);
  assert.equal(size.reading,false);
  assert.ok(size.pageWidth>=width*.7,'Page too narrow at '+width);
  assert.ok(size.pageWidth<=width-48+.01,'Horizontal overflow at '+width);
}
const normal=fit(1920,642.52,1,1,1,false);
const zoom125=fit(1536,642.52,1.25,1,normal.scale,false);
assert.equal(zoom125.scale,normal.scale);
assert.equal(zoom125.reading,false);
const zoom200=fit(960,642.52,2,1,normal.scale,false);
assert.equal(zoom200.scale,normal.scale);
assert.equal(zoom200.automatic,true);
assert.equal(zoom200.reading,true);
assert.equal(fit(375,642.52,1,1,1,false).automatic,true);
assert.equal(fit(1920,642.52,1,1,1,true).reading,true);
'''.replace('SCRIPT',json.dumps(iris_layout.PAGE_SIZING_JS))
        subprocess.run(['node','-e',harness],check=True,capture_output=True)
        self.assertIn('l.offsetWidth',iris_layout.TEMPLATE)
        self.assertNotIn('var w=l.getBoundingClientRect().width',iris_layout.TEMPLATE)

    def test_navigation_uses_semantic_copy_once(self):
        soup=BeautifulSoup('<h2><span class="vis" aria-hidden="true">Results</span>'
                           '<span class="txt">Results</span></h2>', 'html.parser')
        self.assertEqual(reading_label(soup.h2), 'Results')

    def test_running_headers_are_not_sections(self):
        soup=BeautifulSoup('<main>'+''.join(
            '<div class="page" style="height:700pt"><h3><span class="vis"><span class="ln" style="top:30pt">'
            +str(n)+' Chronic Illness 19(2)</span></span><span class="txt">'
            +str(n)+' Chronic Illness 19(2)</span></h3></div>' for n in (410,416))+
            '<h2><span class="txt">Introduction</span></h2></main>', 'html.parser')
        self.assertEqual([label for _,label in navigation_headings(soup)], ['Introduction'])
        self.assertEqual(len(soup.select('[data-layout-running-header]')),2)
        self.assertIn('410 Chronic Illness',soup.get_text())

    def test_unknown_heading_font_uses_document_evidence(self):
        def block(text,y,font='UnknownFace',size=12,level=None):
            return {'bbox':(50,y,250,y+12), 'heading_level':level,
                    'lines':[{'spans':[{'text':text,'font':font,'size':size,'flags':0}]}]}
        pages=[[block('Introduction',90,level=2),block('Results',180,level=2)],
               [block('Methods',520),block('This short paragraph continues onto the next page and describes the procedure.',550,size=10)]]
        refine_headings(pages,[700,700],10)
        self.assertEqual(pages[1][0]['heading_level'],2)
        self.assertIsNone(pages[1][1]['heading_level'])

    @unittest.skipUnless(shutil.which('node'), 'Node required for script execution')
    def test_navigation_scrolls_to_text_without_horizontal_drift(self):
        harness=r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const events={},winEvents={};let focused=false,scroll;
const semantic={getBoundingClientRect:()=>({top:450,bottom:480})};
const target={id:'section-2',querySelector:()=>semantic,focus:()=>{focused=true;}};
const location={href:'https://sunysb.hosts.atlas-sys.com/nonshib/AccessibleHtmlViewer.html?job_id=test',hash:''};
const window={location,scrollY:900,scrollX:0,innerHeight:300,scrollTo:o=>{scroll=o;},
addEventListener:(k,v)=>{winEvents[k]=v;},getSelection:()=>null};
const document={baseURI:'https://example.awsapprunner.com/',getElementById:()=>target,
addEventListener:(k,v)=>{events[k]=v;}};
const history={pushState:(a,b,url)=>{assert.equal(new URL(url).origin,new URL(location.href).origin);assert.ok(url.includes('?job_id=test'));}};
vm.runInNewContext(SCRIPT,{window,document,history,URL});
events.click({target:{closest:()=>({getAttribute:()=>'#section-2'})},preventDefault:()=>{}});
assert.equal(focused,true);assert.equal(scroll.top,1326);assert.equal(scroll.left,0);
window.getSelection=()=>({focusNode:{nodeType:3,parentElement:{closest:()=>semantic}}});
events.selectionchange();assert.equal(scroll.top,1326);
assert.ok(winEvents.hashchange && winEvents.popstate);
'''.replace('SCRIPT',json.dumps(SCRIPT))
        subprocess.run(['node','-e',harness],check=True,capture_output=True)


if __name__=='__main__':unittest.main()

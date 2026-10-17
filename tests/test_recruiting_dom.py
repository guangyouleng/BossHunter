"""Execute local DOM scripts against synthetic jsdom pages, never BOSS."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from bosshunter.recruiting.dom import WATCH_JOB_LIST, PAGE_STATUS, SELECTORS, OPEN_JOB_MENU, PICK_JOB

ROOT=Path(__file__).resolve().parents[1]
JSDOM=ROOT/'src/bosshunter/web/frontend/node_modules/jsdom'


@unittest.skipUnless(shutil.which('node') and JSDOM.is_dir(),'requires frontend dev dependencies and Node')
class DomTests(unittest.TestCase):
    def run_dom(self, code):
        js="""
const fs=require('node:fs'),assert=require('node:assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const {JSDOM}=require(input.jsdom);
const dom=new JSDOM('<body><div class="job-selecter-options"><li class="job-item curr" value="old"></li></div><ul class="card-list"><li class="card-item"><div class="card-inner" data-geek="old-person"></div></li></ul></body>',{runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;
w.HTMLElement.prototype.getClientRects=function(){return w.getComputedStyle(this).display==='none'?[]:[{width:10,height:10}];};
Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent},set(value){this.textContent=value}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{CODE})().then(()=>{dom.window.close();}).catch(e=>{console.error(e);process.exitCode=1;});
""".replace('CODE',code)
        result=subprocess.run(['node','-e',js],input=json.dumps({'jsdom':str(JSDOM),'watch':WATCH_JOB_LIST,'status':PAGE_STATUS,'open':OPEN_JOB_MENU,'pick':PICK_JOB.replace('__JOB_ID__',json.dumps('new'))}),text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_dropdown_and_hover_changes_do_not_prove_candidate_refresh(self):
        self.run_dom("""
const tracker=w.eval(input.watch);
w.document.querySelector('.job-item').setAttribute('value','new');
w.document.body.className='hover';
w.document.querySelector('.job-selecter-options').appendChild(w.document.createElement('span'));
await flush();assert.equal(tracker.read().job_id,'new');assert.equal(tracker.read().changed,false);
w.document.querySelector('.card-inner').setAttribute('data-geek','new-person');
await flush();assert.equal(tracker.read().changed,true);assert.equal(tracker.read().ready,true);tracker.observer.disconnect();
""")

    def test_only_mutations_after_the_target_click_count(self):
        self.run_dom("""
const tracker=w.eval(input.watch);const card=w.document.querySelector('.card-inner');
const options=w.document.querySelector('.job-selecter-options');
options.insertAdjacentHTML('beforeend','<li class="job-item" value="new"></li>');
const target=options.lastElementChild;target.addEventListener('click',()=>{
 options.firstElementChild.classList.remove('curr');target.classList.add('curr');
});
card.setAttribute('data-geek','old-job-background-refresh');await flush();assert.equal(tracker.read().changed,true);
card.setAttribute('data-geek','pending-old-job-change');
assert.equal(w.eval(input.pick)(tracker),true);await flush();
assert.equal(tracker.read().job_id,'new');assert.equal(tracker.read().changed,false);
card.setAttribute('data-geek','new-job-result');await flush();assert.equal(tracker.read().changed,true);
tracker.observer.disconnect();
""")

    def test_an_open_job_menu_is_not_toggled_closed(self):
        self.run_dom("""
w.document.body.insertAdjacentHTML('beforeend','<div class="job-selecter-wrap"><button class="ui-dropmenu-label">岗位</button></div>');
let clicks=0;w.document.querySelector('.ui-dropmenu-label').addEventListener('click',()=>clicks++);
assert.equal(w.eval(input.open),true);assert.equal(clicks,0);
""")

    def test_spinner_only_does_not_make_old_cards_fresh(self):
        self.run_dom("""
const tracker=w.eval(input.watch);const list=w.document.querySelector('.card-list');
const spinner=w.document.createElement('div');spinner.className='ui-loading';list.appendChild(spinner);
await flush();assert.equal(tracker.read().loading,true);assert.equal(tracker.read().changed,false);
spinner.remove();await flush();assert.equal(tracker.read().ready,true);assert.equal(tracker.read().changed,false);
tracker.observer.disconnect();
""")

    def test_loading_and_explicit_empty_list_are_distinct(self):
        self.run_dom("""
const tracker=w.eval(input.watch);const list=w.document.querySelector('.card-list');
list.innerHTML='<div class="ui-loading">加载中</div>';await flush();assert.equal(tracker.read().ready,false);
list.innerHTML='<div class="empty-tips">暂无推荐</div>';await flush();assert.equal(tracker.read().ready,true);assert.equal(tracker.read().count,0);tracker.observer.disconnect();
""")

    def test_only_visible_page_prompts_are_classified(self):
        self.run_dom("""
w.document.body.insertAdjacentHTML('beforeend','<div class="chat-message-list">检测到辅助工具，请完成安全验证</div>');
assert.equal(w.eval(input.status),'');
const prompt=w.document.createElement('div');prompt.className='boss-dialog__wrapper';w.document.body.appendChild(prompt);
for(const [text,expected] of [['扫码登录','login_required'],['请完成安全验证','verification_required'],['检测到辅助工具','platform_restricted'],['购买会员权益','paywall'],['普通提示','modal_blocked']]) {
 prompt.textContent=text;assert.equal(w.eval(input.status),expected);
}
prompt.style.display='none';assert.equal(w.eval(input.status),'');
""")

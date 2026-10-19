"""Execute local DOM scripts against synthetic jsdom pages, never BOSS."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from bosshunter.recruiting.dom import WATCH_JOB_LIST, PAGE_STATUS, SELECTORS, OPEN_JOB_MENU, PICK_JOB, ARM_JOB_CLICK, ARM_GREET_CLICK, CLICK_GREET

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
        result=subprocess.run(['node','-e',js],input=json.dumps({'jsdom':str(JSDOM),'watch':WATCH_JOB_LIST,'status':PAGE_STATUS,'open':OPEN_JOB_MENU,'pick':PICK_JOB.replace('__JOB_ID__',json.dumps('new')), 'arm':ARM_JOB_CLICK.replace('__JOB_ID__',json.dumps('new')), 'greet':ARM_GREET_CLICK.replace('__UID__',json.dumps('old-person')).replace('__JOB_ID__',json.dumps('old')), 'standard_greet':CLICK_GREET.replace('__UID__',json.dumps('old-person')).replace('__JOB_ID__',json.dumps('old'))}),text=True,capture_output=True,timeout=10)
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

    def test_human_click_discards_changes_during_cursor_movement(self):
        self.run_dom("""
const tracker=w.eval(input.watch), card=w.document.querySelector('.card-inner');
const options=w.document.querySelector('.job-selecter-options');
options.insertAdjacentHTML('beforeend','<li class="job-item" value="new"><span>new</span></li>');
const target=options.lastElementChild;
target.addEventListener('click',()=>{options.firstElementChild.classList.remove('curr');target.classList.add('curr');});
assert.equal(w.eval(input.arm)(tracker),true);
card.setAttribute('data-geek','changed-during-pointer-travel');await flush();
assert.equal(tracker.read().changed,true);
target.firstElementChild.click();await flush();
assert.equal(tracker.clicked,true);assert.equal(tracker.read().job_id,'new');assert.equal(tracker.read().changed,false);
card.setAttribute('data-geek','actual-new-job-result');await flush();assert.equal(tracker.read().changed,true);
tracker.disarmClick();tracker.observer.disconnect();
""")

    def test_human_click_keeps_synchronous_new_job_refresh_evidence(self):
        self.run_dom("""
const tracker=w.eval(input.watch), card=w.document.querySelector('.card-inner');
const options=w.document.querySelector('.job-selecter-options');
options.insertAdjacentHTML('beforeend','<li class="job-item" value="new"></li>');
const target=options.lastElementChild;
target.addEventListener('click',()=>{options.firstElementChild.classList.remove('curr');target.classList.add('curr');card.setAttribute('data-geek','new-job');});
assert.equal(w.eval(input.arm)(tracker),true);card.setAttribute('data-geek','pending-old-job');
target.click();await flush();assert.equal(tracker.read().changed,true);
tracker.disarmClick();tracker.observer.disconnect();
""")

    def test_greeting_guard_blocks_identity_changes_during_click(self):
        self.run_dom("""
const inner=w.document.querySelector('.card-inner');inner.innerHTML='<button class="btn-greet">打招呼</button>';
const button=inner.firstElementChild;let clicks=0;button.addEventListener('click',()=>clicks++);
const gate=w.eval(input.greet);
button.dispatchEvent(new w.MouseEvent('pointerdown',{bubbles:true,cancelable:true}));
w.document.querySelector('.job-item').setAttribute('value','other');
button.click();assert.equal(clicks,0);assert.equal(gate.blocked,true);assert.equal(gate.clicked,false);gate.dispose();
w.document.querySelector('.job-item').setAttribute('value','old');
const valid=w.eval(input.greet);button.click();assert.equal(clicks,1);assert.equal(valid.clicked,true);valid.dispose();
""")

    def test_standard_greet_rechecks_job_in_the_click_script(self):
        self.run_dom("""
const inner=w.document.querySelector('.card-inner');inner.innerHTML='<button class="btn-greet">打招呼</button>';
let clicks=0;inner.firstElementChild.addEventListener('click',()=>clicks++);
w.document.querySelector('.job-item').setAttribute('value','other');
assert.throws(()=>w.eval(input.standard_greet),/岗位/);assert.equal(clicks,0);
""")

    def test_standard_greet_rejects_duplicate_candidate_cards(self):
        self.run_dom("""
const card=w.document.querySelector('.card-item');card.querySelector('.card-inner').innerHTML='<button class="btn-greet">打招呼</button>';
card.parentElement.append(card.cloneNode(true));let clicks=0;
w.document.addEventListener('click',()=>clicks++);
assert.throws(()=>w.eval(input.standard_greet),/唯一/);assert.equal(clicks,0);
""")

    def test_standard_greet_ignores_hidden_card_and_clicks_one_visible_card(self):
        self.run_dom("""
const card=w.document.querySelector('.card-item');card.querySelector('.card-inner').innerHTML='<button class="btn-greet">打招呼</button>';
const hidden=card.cloneNode(true);hidden.style.display='none';card.before(hidden);
let hiddenClicks=0,visibleClicks=0;
hidden.querySelector('button').addEventListener('click',()=>hiddenClicks++);
card.querySelector('button').addEventListener('click',()=>visibleClicks++);
assert.equal(w.eval(input.standard_greet),'clicked');assert.equal(hiddenClicks,0);assert.equal(visibleClicks,1);
""")

    def test_greeting_guard_blocks_replaced_button_instead_of_letting_click_escape(self):
        self.run_dom("""
const inner=w.document.querySelector('.card-inner');inner.innerHTML='<button class="btn-greet">打招呼</button>';
const gate=w.eval(input.greet), replacement=inner.firstElementChild.cloneNode(true);
inner.firstElementChild.replaceWith(replacement);let clicks=0;replacement.addEventListener('click',()=>clicks++);
w.document.querySelector('.job-item').setAttribute('value','other');replacement.click();
assert.equal(clicks,0);assert.equal(gate.blocked,true);assert.equal(gate.clicked,false);gate.dispose();
""")

    def test_greeting_guard_owns_visible_button_when_a_hidden_copy_exists(self):
        self.run_dom("""
const inner=w.document.querySelector('.card-inner');inner.innerHTML='<button class="btn-greet" style="display:none">打招呼</button><button class="btn-greet">打招呼</button>';
const gate=w.eval(input.greet);inner.lastElementChild.click();
assert.equal(gate.clicked,true);assert.equal(gate.blocked,false);gate.dispose();
""")

    def test_standard_greet_never_clicks_through_a_new_prompt(self):
        self.run_dom("""
const inner=w.document.querySelector('.card-inner');inner.innerHTML='<button class="btn-greet">打招呼</button>';
w.document.body.insertAdjacentHTML('beforeend','<div class="dialog-wrap active">请完成安全验证</div>');
let clicks=0;inner.firstElementChild.addEventListener('click',()=>clicks++);
assert.throws(()=>w.eval(input.standard_greet),/弹窗/);assert.equal(clicks,0);
""")

    def test_greeting_guard_blocks_a_prompt_appearing_after_arming(self):
        self.run_dom("""
const inner=w.document.querySelector('.card-inner');inner.innerHTML='<button class="btn-greet">打招呼</button>';
const gate=w.eval(input.greet);let clicks=0;inner.firstElementChild.addEventListener('click',()=>clicks++);
w.document.body.insertAdjacentHTML('beforeend','<div class="dialog-wrap active">请完成安全验证</div>');
inner.firstElementChild.click();assert.equal(clicks,0);assert.equal(gate.blocked,true);gate.dispose();
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

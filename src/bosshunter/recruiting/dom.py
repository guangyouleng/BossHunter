"""Recruiting DOM selectors and read/action scripts for known BOSS structures.

Unknown markup must fail explicitly. This module never installs global hooks,
changes tabs, bypasses verification or sends network requests.
"""

import json

# CSS changes belong here; each name describes its role, not a guessed fallback.
from pathlib import Path
PAGE_RULES = json.loads((Path(__file__).resolve().parents[1] / 'data/recruiting_dom.json').read_text(encoding='utf-8'))
SELECTORS = PAGE_RULES['selectors']


def render_dom(script):
    for name, selector in SELECTORS.items():
        script = script.replace('__dom_' + name + '__', json.dumps(selector, ensure_ascii=False))
    return script



READ_CHAT = r"""
const selected = document.querySelector(__dom_chat_selected__);
const editor = document.querySelector(__dom_chat_editor__);
if(location.hostname !== 'www.zhipin.com' || location.pathname !== '/web/chat/index')
    throw Error('请先打开 BOSS 招聘端沟通页面');
if(!selected || !editor) throw Error('请在招聘端选择一个会话');
const messages = [...document.querySelectorAll(__dom_chat_messages__)].map(e => {
    const text = e.querySelector(__dom_message_text__);
    const card = e.querySelector(__dom_message_card__);
    return {direction:e.querySelector(__dom_message_out__)?'out':e.querySelector(__dom_message_in__)?'in':'system',
        kind:text?'text':card?'card':'system',
        text:(text?.innerText || card?.innerText || e.innerText).trim(),
        time:e.querySelector(__dom_message_time__)?.innerText.trim() || ''};
}).filter(e=>e.text);
const snapshot = {
    id:selected.getAttribute('data-id'), name:selected.querySelector(__dom_contact_name__)?.innerText.trim() || '',
    position_title:document.querySelector(__dom_chat_position__)?.innerText.trim() || '',
    messages, editor_empty:!editor.innerText.trim(),
    coverage:'当前会话已加载消息；列表只展示近30天联系人',
    stable_message_ids:false
};
if(!snapshot.id || !snapshot.position_title) throw Error('无法核实会话或关联岗位');
"""


READ_CONTACT_LIST = r"""
const contacts = [...document.querySelectorAll(__dom_contacts__)].map(item => {
    const ident = item.getAttribute('data-id') || '';
    const name = (item.querySelector(__dom_contact_name__)?.innerText || item.querySelector(__dom_contact_name__)?.getAttribute('title') || '').trim();
    const position_title = (item.querySelector(__dom_contact_position__)?.innerText || item.querySelector(__dom_contact_position__)?.getAttribute('title') || '').trim();
    return {ident, name, position_title};
}).filter(c => c.ident && c.name);
"""


SCROLL_CONTACT_LIST = READ_CONTACT_LIST + r"""
const items = document.querySelectorAll(__dom_contacts__);
if (items.length) { items[items.length - 1].scrollIntoView(); }
return JSON.stringify({count: items.length, contacts});
"""


CLICK_GREET = r"""
(() => {
    const uid = __UID__;
    const job = __JOB_ID__;
    const selected = document.querySelector(__dom_selected_job__) || document.querySelector(__dom_selected_job_alternate__);
    if (selected?.getAttribute('value') !== job) throw Error('点击前岗位已变化，停止招呼');
    const visible = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
    const documents=[document];
    try { if(window.top.document!==document) documents.push(window.top.document); } catch {}
    if(documents.some(doc=>[...doc.querySelectorAll(__dom_page_prompts__)].some(visible))) throw Error('请先处理推荐页弹窗，停止招呼');
    const cards = [...document.querySelectorAll(__dom_recommend_cards__)].filter(card => {
        const inner = card.querySelector(__dom_recommend_identity__);
        const geekid = (inner?.getAttribute('data-geekid') || inner?.getAttribute('data-geek') || '').trim();
        return geekid === uid && visible(card);
    });
    if (!cards.length) return 'missing';
    if (cards.length !== 1) throw Error('无法唯一核实候选人卡片，停止招呼');
    const buttons = [...cards[0].querySelectorAll(__dom_greet_button__)].filter(visible);
    if (!buttons.length) return 'unavailable';
    if (buttons.length !== 1) throw Error('无法唯一核实打招呼按钮，停止招呼');
    const btn = buttons[0];
    if ((btn.innerText || '').trim() === '继续沟通') return 'already';
    if ((btn.innerText || '').trim() !== '打招呼' || btn.disabled || btn.getAttribute('aria-disabled') === 'true') return 'unavailable';
    btn.click();
    return 'clicked';
})()
"""


READ_GREET_TEXT = r"""
(() => {
    const uid = __UID__;
    const visible = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
    const cards = [...document.querySelectorAll(__dom_recommend_cards__)].filter(card => {
        const inner = card.querySelector(__dom_recommend_identity__);
        const geekid = (inner?.getAttribute('data-geekid') || inner?.getAttribute('data-geek') || '').trim();
        return geekid === uid && visible(card);
    });
    if (cards.length !== 1) return null;
    const buttons = [...cards[0].querySelectorAll(__dom_greet_button__)].filter(visible);
    return buttons.length === 1 ? (buttons[0].innerText || '').trim() : null;
})()
"""


READ_JOB_ID = r"""
(() => {
    const item = document.querySelector(__dom_selected_job__) || document.querySelector(__dom_selected_job_alternate__);
    return item ? item.getAttribute('value') : null;
})()
"""


WATCH_JOB_LIST = r"""
(() => {
    const cards = __dom_recommend_cards__;
    const lists = __dom_recommend_lists__;
    const empty = __dom_recommend_empty__;
    const visible = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
    const state = {changed:false, revision:0};
    state.read = () => {
        const selected = document.querySelector(__dom_selected_job__) || document.querySelector(__dom_selected_job_alternate__);
        const rows = [...document.querySelectorAll(cards)];
        const roots = [...document.querySelectorAll(lists)];
        const loading = roots.some(root => root.getAttribute('aria-busy') === 'true' ||
            [...root.querySelectorAll(__dom_list_loading__)].some(visible));
        return {job_id:selected?.getAttribute('value') || null, changed:state.changed, revision:state.revision,
            ready:!loading && (rows.some(row => row.querySelector(__dom_recommend_identity__)) || [...document.querySelectorAll(empty)].some(visible)),
            loading, count:rows.length};
    };
    const inside = node => node instanceof Element && (node.matches(cards + ', ' + empty) ||
        !!node.closest(cards + ', ' + empty));
    const addedOrRemoved = node => inside(node) || (node instanceof Element && !!node.querySelector(cards + ', ' + empty));
    state.observer = new MutationObserver(records => {
        const contentChanged = records.some(record => inside(record.target.nodeType === 3 ? record.target.parentElement : record.target) ||
            [...record.addedNodes, ...record.removedNodes].some(addedOrRemoved));
        if(contentChanged) state.changed=true;
        // A spinner/busy flag can reset settling, but cannot prove new candidates.
        if(contentChanged || records.some(record => record.target instanceof Element &&
            (record.target.matches(lists) || record.target.closest(lists)))) state.revision++;
    });
    state.observer.observe(document.documentElement, {childList:true, subtree:true, characterData:true,
        attributes:true, attributeFilter:['data-geek', 'data-geekid', 'aria-busy']});
    return state;
})()
"""


OPEN_JOB_MENU = r"""
(() => {
const visible=el=>el.getClientRects().length>0 && getComputedStyle(el).visibility!=='hidden';
if([...document.querySelectorAll(__dom_job_options__)].some(visible)) return true;
const label=document.querySelector(__dom_job_menu__);
if(!label) return false; label.click(); return true;})()
"""


PICK_JOB = r"""
(state => {const item=[...document.querySelectorAll(__dom_job_options__)]
    .find(item=>item.getAttribute('value')===__JOB_ID__);
if(!item || item.getClientRects().length===0 || getComputedStyle(item).visibility==='hidden') return false;
// Drop both delivered and pending mutations from before this specific click.
state.observer.takeRecords(); state.changed=false; state.revision=0;
item.click(); return true;})
"""


# Reset refresh evidence at the actual input event, after cursor travel.
ARM_JOB_CLICK = r"""
(state => {
    state.disarmClick?.(); state.clicked=false;
    const options=[...document.querySelectorAll(__dom_job_options__)]
        .filter(item=>item.getAttribute('value')===__JOB_ID__ && item.getClientRects().length && getComputedStyle(item).visibility!=='hidden');
    if(options.length!==1) return false;
    const item=options[0];
    const capture=event=>{
        if(!item.contains(event.target)) return;
        state.observer.takeRecords(); state.changed=false; state.revision=0; state.clicked=true;
        state.disarmClick();
    };
    state.disarmClick=()=>document.removeEventListener('click',capture,true);
    document.addEventListener('click',capture,true);
    return true;
})
"""


ARM_GREET_CLICK = r"""
(() => {
    const uid=__UID__, job=__JOB_ID__;
    const visible=e=>!!e && e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden';
    const matches=[...document.querySelectorAll(__dom_recommend_cards__)].filter(card=>{
        const inner=card.querySelector(__dom_recommend_identity__);
        return visible(card) && (inner?.getAttribute('data-geekid') || inner?.getAttribute('data-geek') || '').trim()===uid;
    });
    if(matches.length!==1) throw Error('无法唯一核实候选人卡片');
    const card=matches[0], buttons=[...card.querySelectorAll(__dom_greet_button__)].filter(visible);
    if(buttons.length!==1) throw Error('无法唯一核实可见打招呼按钮');
    const button=buttons[0];
    const state={clicked:false,blocked:false};
    const guard=event=>{
        const target=event.target.closest?.(__dom_greet_button__);
        if(!target) return;
        const selected=document.querySelector(__dom_selected_job__) || document.querySelector(__dom_selected_job_alternate__);
        const inner=card.querySelector(__dom_recommend_identity__);
        const current=(inner?.getAttribute('data-geekid') || inner?.getAttribute('data-geek') || '').trim();
        const documents=[document];
        try { if(window.top.document!==document) documents.push(window.top.document); } catch {}
        const prompt=documents.some(doc=>[...doc.querySelectorAll(__dom_page_prompts__)].some(visible));
        if(state.blocked || target!==button || !card.isConnected || !button.isConnected || !visible(button) ||
            selected?.getAttribute('value')!==job || current!==uid || button.innerText.trim()!=='打招呼' ||
            button.disabled || button.getAttribute('aria-disabled')==='true' || prompt) {
            state.blocked=true; event.preventDefault(); event.stopImmediatePropagation(); return;
        }
        if(event.type==='click') {state.clicked=true;state.dispose();}
    };
    const events=['pointerdown','mousedown','click'];
    state.dispose=()=>events.forEach(name=>window.removeEventListener(name,guard,true));
    events.forEach(name=>window.addEventListener(name,guard,true));
    return state;
})()
"""


CONTACT_SCROLL_TARGET = r"""
(() => {
    const items=[...document.querySelectorAll(__dom_contacts__)];
    if(!items.length) return {empty:true, at_bottom:true};
    let root=items[0].parentElement;
    while(root && root!==document.body && root!==document.documentElement) {
        if(/^(auto|scroll|overlay)$/.test(getComputedStyle(root).overflowY) && root.clientHeight>0) break;
        root=root.parentElement;
    }
    if(!root || root===document.body || root===document.documentElement) return {missing:true};
    const rect=root.getBoundingClientRect();
    const left=Math.max(0,rect.left), right=Math.min(innerWidth,rect.right);
    const top=Math.max(0,rect.top), bottom=Math.min(innerHeight,rect.bottom);
    const x=(left+right)/2, y=(top+bottom)/2;
    const hit=document.elementFromPoint(x,y);
    return {blocked:right-left<4 || bottom-top<4 || !hit || !root.contains(hit),
        x,y,delta:Math.max(40,Math.min(600,root.clientHeight*.7)),
        at_bottom:root.scrollTop+root.clientHeight>=root.scrollHeight-2};
})()
"""


READ_CANDIDATES = r"""
(() => {
    const out = [];
    for (const card of document.querySelectorAll(__dom_recommend_cards__)) {
        const inner = card.querySelector(__dom_recommend_identity__);
        const uid = (inner?.getAttribute('data-geekid') || inner?.getAttribute('data-geek') || '').trim();
        if (!uid) continue;
        const name = (card.querySelector(__dom_recommend_name__)?.innerText || card.querySelector(__dom_recommend_avatar__)?.getAttribute('alt') || '').trim();
        out.push({ name, uid, greetable: !!card.querySelector(__dom_greet_button__) });
    }
    return out;
})()
"""


PAGE_STATUS = r"""
(() => {
    const visible = el => !!el && el.getClientRects().length > 0 &&
        getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
    const documents = [document];
    for(const frame of document.querySelectorAll(__dom_frames__)) {
        try { if(visible(frame) && frame.contentDocument) documents.push(frame.contentDocument); } catch {}
    }
    const prompts = documents.flatMap(doc => [...doc.querySelectorAll(__dom_page_prompts__)])
        .filter(visible).filter(el => !el.querySelector(__dom_prompt_exclusions__));
    for(const prompt of prompts) {
        const text = (prompt.innerText || '').replace(/\s+/g,'');
        if(/扫码登录|登录后继续|请先登录/.test(text)) return 'login_required';
        if(/安全验证|滑动验证|请完成验证/.test(text)) return 'verification_required';
        if(/检测到.{0,20}(辅助工具|自动化)|(?:请勿|禁止|停止使用).{0,20}辅助工具|操作过于频繁|访问受限/.test(text)) return 'platform_restricted';
        if(/购买|开通|升级/.test(text) && /会员|权益|套餐|直豆/.test(text)) return 'paywall';
    }
    return prompts.length ? 'modal_blocked' : '';
})()
"""


# Render constants once; public constants remain compatible with their callers.
READ_CHAT = render_dom(READ_CHAT)
READ_CONTACT_LIST = render_dom(READ_CONTACT_LIST)
SCROLL_CONTACT_LIST = render_dom(SCROLL_CONTACT_LIST)
CLICK_GREET = render_dom(CLICK_GREET)
READ_GREET_TEXT = render_dom(READ_GREET_TEXT)
READ_JOB_ID = render_dom(READ_JOB_ID)
WATCH_JOB_LIST = render_dom(WATCH_JOB_LIST)
OPEN_JOB_MENU = render_dom(OPEN_JOB_MENU)
PICK_JOB = render_dom(PICK_JOB)
ARM_JOB_CLICK = render_dom(ARM_JOB_CLICK)
ARM_GREET_CLICK = render_dom(ARM_GREET_CLICK)
CONTACT_SCROLL_TARGET = render_dom(CONTACT_SCROLL_TARGET)
READ_CANDIDATES = render_dom(READ_CANDIDATES)
PAGE_STATUS = render_dom(PAGE_STATUS)

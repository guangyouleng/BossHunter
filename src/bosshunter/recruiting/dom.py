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
    for (const card of document.querySelectorAll(__dom_recommend_cards__)) {
        const inner = card.querySelector(__dom_recommend_identity__);
        const geekid = (inner?.getAttribute('data-geekid') || inner?.getAttribute('data-geek') || '').trim();
        if (geekid !== uid) continue;
        const btn = card.querySelector(__dom_greet_button__);
        if (!btn) return 'unavailable';
        if ((btn.innerText || '').trim() === '继续沟通') return 'already';
        if ((btn.innerText || '').trim() !== '打招呼') return 'unavailable';
        btn.click();
        return 'clicked';
    }
    return 'missing';
})()
"""


READ_GREET_TEXT = r"""
(() => {
    const uid = __UID__;
    for (const card of document.querySelectorAll(__dom_recommend_cards__)) {
        const inner = card.querySelector(__dom_recommend_identity__);
        const geekid = (inner?.getAttribute('data-geekid') || inner?.getAttribute('data-geek') || '').trim();
        if (geekid !== uid) continue;
        const btn = card.querySelector(__dom_greet_button__);
        return btn ? (btn.innerText || '').trim() : '';
    }
    return null;
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
READ_CANDIDATES = render_dom(READ_CANDIDATES)
PAGE_STATUS = render_dom(PAGE_STATUS)

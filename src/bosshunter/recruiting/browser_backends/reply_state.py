"""Scoped guards for incremental reply input, owned only by a JS handle.

Never attach state to window/DOM. Release listeners after success or failure.
Input.insertText avoids Enter shortcuts and works with Unicode/IME text.
"""
import json
from ..dom import READ_CHAT, render_dom
from .common import reply_guard


STATE = render_dom(r"""
const intended=__CONTENT__;
const originalEditor=editor;
const state={prefix:'', pending:null, received:false, interfered:false, clicked:false, phase:'typing'};
const valueMatches=prefix=>originalEditor.innerText===prefix ||
    (prefix.endsWith('\n') && originalEditor.innerText===prefix+'\n');
// Chromium contenteditable keeps one extra rendered newline as a caret
// placeholder at the end of a just-inserted line. Input events still account
// for each exact character; any unowned edit latches interfered.
const read=()=>{__READ_CHAT__ return {snapshot,editor};};
const verify=(focus=false)=>{
    const current=read();
    const editors=[...document.querySelectorAll(__dom_chat_editor__)].filter(visible);
    if(state.interfered || current.editor!==originalEditor || !originalEditor.isConnected ||
        editors.length!==1 || editors[0]!==originalEditor ||
        current.snapshot.id!==expected.id || current.snapshot.position_title!==expected.position_title ||
        JSON.stringify(current.snapshot.messages)!==JSON.stringify(expected.messages) ||
        !valueMatches(state.prefix) || [...document.querySelectorAll(__dom_active_dialog__)].some(visible))
        throw Error('会话、消息或人工草稿已变化，停止输入和发送');
    if(focus) {
        const selection=getSelection();
        if(!(document.activeElement===originalEditor || originalEditor.contains(document.activeElement)) ||
            !selection || !selection.isCollapsed || !selection.rangeCount ||
            !originalEditor.contains(selection.anchorNode)) throw Error('编辑器焦点或光标位置已变化，停止输入');
        const tail=selection.getRangeAt(0).cloneRange();
        tail.setEnd(originalEditor,originalEditor.childNodes.length);
        if(tail.toString()) throw Error('编辑器光标已离开末尾，停止输入');
    }
    return true;
};
state.arm=character=>{
    verify(true);
    if(state.phase!=='typing' || state.pending!==null || !intended.startsWith(state.prefix+character))
        throw Error('回复输入状态不匹配，停止输入');
    state.pending=character;state.received=false;return true;
};
state.finishCharacter=()=>{
    if(!state.received || state.pending!==null) throw Error('未确认本次字符输入，停止发送');
    return verify(true);
};
state.armSend=()=>{
    verify();
    if(state.phase!=='typing' || state.pending!==null || state.prefix!==intended)
        throw Error('回复尚未完整输入，停止发送');
    const candidates=[...document.querySelectorAll(__dom_send_ready__)].filter(visible);
    if(candidates.length!==1) throw Error('发送按钮尚未唯一就绪');
    state.send=candidates[0];state.phase='sending';return true;
};
const block=event=>{event.preventDefault();event.stopImmediatePropagation();};
const beforeInput=event=>{
    if(state.pending===null) {state.interfered=true;return;}
    let valid=false;
    try {valid=verify(true) && event.target===originalEditor && event.data===state.pending && event.inputType==='insertText';} catch {}
    if(!valid) {state.interfered=true;block(event);}
};
const input=event=>{
    if(state.phase==='submitted') return; // The page may emit input while clearing after send.
    const owned=event.data===state.pending && event.inputType==='insertText' ||
        state.pending==='\n' && event.data===null && event.inputType==='insertParagraph';
    if(event.target!==originalEditor || state.pending===null || !owned) {
        state.interfered=true;return;
    }
    state.prefix+=state.pending;state.pending=null;state.received=true;
};
const keydown=event=>{
    state.interfered=true;
    // Never let a manual Enter submit a partially generated reply.
    if(originalEditor.contains(event.target) && event.key==='Enter') block(event);
};
const pointer=event=>{
    const sending=state.send && state.send.contains(event.target);
    if(state.phase==='typing') {
        state.interfered=true;
        if(event.target.closest?.(__dom_send_button__)) block(event);
        return;
    }
    let valid=false;
    try {valid=state.phase==='sending' && sending && verify();} catch {}
    if(!valid) {state.interfered=true;block(event);return;}
    if(event.type==='click') {state.clicked=true;state.phase='submitted';}
};
const listeners=[['beforeinput',beforeInput],['input',input],['keydown',keydown],
                 ['pointerdown',pointer],['mousedown',pointer],['click',pointer]];
state.dispose=()=>listeners.forEach(([name,handler])=>window.removeEventListener(name,handler,true));
verify(true);
listeners.forEach(([name,handler])=>window.addEventListener(name,handler,true));
return state;
""")


def create_state(page, before, content):
    script = STATE.replace('__READ_CHAT__', READ_CHAT).replace('__CONTENT__', json.dumps(content, ensure_ascii=False))
    return page.evaluate_handle('(() => {' + reply_guard(before) + script + '})()')

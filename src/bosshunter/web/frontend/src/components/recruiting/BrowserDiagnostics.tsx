import type { BrowserDiagnostic, BrowserOperation } from '../../pages/RecruitingPage'

const labels: Record<string, string> = {
  login_required: '需要重新登录', verification_required: '需要人工验证', platform_restricted: '平台限制或风险提醒',
  paywall: '付费或权益提示', modal_blocked: '有未处理弹窗', browser_busy: '浏览器正在处理其他任务',
  job_transition_timeout: '岗位或推荐列表尚未就绪', job_menu_missing: '未找到岗位下拉框',
  job_option_missing: '未找到目标岗位选项', job_identity_changed: '岗位身份已变化',
  recommend_frame_missing: '未找到推荐页内容', dom_structure_changed: '页面结构无法识别',
  account_mismatch: '登录账号不一致', account_paused: '账号需要人工处理',
  greeting_uncertain: '招呼结果待人工核实',
  browser_operation_failed: '浏览器操作中断', browser_unavailable: '浏览器操作未完成',
}
const fields: Record<string, string> = {
  page_url: '页面路径', frame_url: '内容框架路径', target_bound: '已绑定标签页', frame_found: '找到推荐框架',
  job_matches: '岗位核对一致', list_ready: '列表已就绪', list_changed: '列表已更新', loading: '正在加载',
  candidate_count: '页面卡片数量', http_status: 'HTTP 状态', page_kind: '页面分类', exception_type: '异常类型',
}
export function BrowserOperationStatus({ operation }: { operation?: BrowserOperation }) {
  if (operation?.status === 'running') return <p className="rc-alert warning" role="status">浏览器正在处理：{operation.operation}。其他浏览器操作需稍后重试。</p>
  if (operation?.status === 'interrupted') return <p className="rc-alert warning" role="status">上次浏览器操作已中断，请检查 BOSS 页面；不会自动重试外发。</p>
  return null
}
export function BrowserDiagnostics({ entries = [] }: { entries?: BrowserDiagnostic[] }) {
  return <section className="rc-panel" aria-label="浏览器故障诊断"><h3>浏览器故障诊断</h3>
    <p className="rc-caption">仅记录失败阶段和页面结构状态，不记录 Cookie、聊天正文或简历内容。诊断不会触发平台请求。</p>
    {!entries.length ? <p className="rc-muted">暂无浏览器故障记录</p> : entries.slice(0, 20).map(entry => <details key={entry.id} className="rc-browser-diagnostic">
      <summary>{new Date(entry.created_at).toLocaleString('zh-CN', { hour12: false })} · {entry.stage} · {labels[entry.code] || '页面操作未完成'}</summary>
      <dl><dt>诊断编号</dt><dd>{entry.id}</dd><dt>错误分类</dt><dd>{entry.code}</dd>{Object.entries(entry.details || {}).filter(([key]) => fields[key]).map(([key, value]) => <div key={key}><dt>{fields[key]}</dt><dd>{typeof value === 'boolean' ? value ? '是' : '否' : String(value)}</dd></div>)}</dl>
    </details>)}
  </section>
}

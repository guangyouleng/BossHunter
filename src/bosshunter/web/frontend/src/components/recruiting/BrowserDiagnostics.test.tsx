import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { BrowserDiagnostics, BrowserOperationStatus } from './BrowserDiagnostics'
afterEach(cleanup)
describe('browser diagnostics', () => {
  it('shows failure category, stage and structural details', () => {
    render(<BrowserDiagnostics entries={[{ id:'diagnostic-1', created_at:'2026-10-17T00:00:00Z', stage:'切换并核验岗位', code:'job_transition_timeout', details:{job_matches:true,list_ready:false,page_url:'https://www.zhipin.com/web/chat/recommend'} }]} />)
    expect(screen.getByText(/切换并核验岗位 · 岗位或推荐列表尚未就绪/)).toBeTruthy()
    expect(screen.getByText('岗位核对一致')).toBeTruthy()
    expect(screen.getByText('否')).toBeTruthy()
  })
  it('shows running and interrupted operations without offering automatic retries', () => {
    const { rerender } = render(<BrowserOperationStatus operation={{status:'running',operation:'刷新推荐页'}} />)
    expect(screen.getByRole('status').textContent).toContain('刷新推荐页')
    rerender(<BrowserOperationStatus operation={{status:'interrupted'}} />)
    expect(screen.getByRole('status').textContent).toContain('不会自动重试外发')
    expect(screen.queryByRole('button')).toBeNull()
  })
})

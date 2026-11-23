import { Spin } from 'antd'
import { lazy, Suspense, useEffect, useState } from 'react'

import { ChatPage } from '../pages/ChatPage'
import { clearLegacyWorkspaceStorage } from '../state/conversations'

const SetupPage = lazy(async () => {
  const module = await import('../pages/SetupPage')
  return { default: module.SetupPage }
})

export function ProductShell() {
  const [showSetup, setShowSetup] = useState(() => window.location.hash === '#/site-setup')

  useEffect(() => {
    clearLegacyWorkspaceStorage()
    const followRoute = () => setShowSetup(window.location.hash === '#/site-setup')
    window.addEventListener('hashchange', followRoute)
    return () => window.removeEventListener('hashchange', followRoute)
  }, [])

  if (showSetup) {
    return (
      <Suspense fallback={<div className="app-loading" role="status"><Spin />正在打开站点登记…</div>}>
        <SetupPage />
      </Suspense>
    )
  }

  return (
    <main className="workspace-shell">
      <ChatPage />
    </main>
  )
}

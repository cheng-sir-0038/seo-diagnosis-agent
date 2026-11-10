import {
  CommentOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { Button, Tabs, Tooltip } from 'antd'
import { useEffect, useState } from 'react'
import type { ComponentType } from 'react'

import { useProduct } from '../context/ProductContext'
import { ChatPage } from '../pages/ChatPage'

type ViewKey = 'chat'

const views: Record<ViewKey, ComponentType> = {
  chat: ChatPage,
}

const navigation: Array<{ key: ViewKey; label: string; icon: typeof CommentOutlined; disabled?: boolean }> = [
  { key: 'chat', label: '对话', icon: CommentOutlined },
]

function currentView(): ViewKey {
  const key = window.location.hash.replace('#/', '') as ViewKey
  return key in views ? key : 'chat'
}

export function ProductShell() {
  const { site, reset } = useProduct()
  const [view, setView] = useState<ViewKey>(currentView)
  const Page = views[view]

  useEffect(() => {
    const handleHashChange = () => setView(currentView())
    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  const navigate = (key: string) => {
    const next = key as ViewKey
    setView(next)
    const nextHash = `#/${next}`
    if (window.location.hash !== nextHash) window.location.hash = `/${next}`
  }

  return (
    <div className={`product-shell product-view-${view}`}>
      <header className="product-header">
        <div className="product-brand">
          <div className="product-logo" aria-hidden="true">S</div>
          <div className="product-brand-copy">
            <strong>SEO 优化专员</strong>
            <span>AI 增长工作台</span>
          </div>
        </div>

        <div className="product-site-summary">
          <span className="site-avatar" aria-hidden="true">{site?.name.slice(0, 1).toUpperCase()}</span>
          <div>
            <strong>{site?.name}</strong>
            <span>{site?.domain} · {site?.default_country ?? '—'} / {site?.default_language ?? '—'}</span>
          </div>
        </div>

        <div className="product-header-actions">
          <div className="connection-status"><span className="online-dot" />服务已连接</div>
          <Tooltip title="重新初始化站点">
            <Button aria-label="重新初始化站点" type="text" icon={<SettingOutlined />} onClick={reset} />
          </Tooltip>
        </div>
      </header>

      <div className="product-tabbar">
        <Tabs
          activeKey={view}
          animated={false}
          onChange={navigate}
          items={navigation.map((item) => {
            const Icon = item.icon
            return {
              key: item.key,
              disabled: item.disabled ?? false,
              label: (
                <Tooltip title={item.disabled ? '即将上线，敬请期待' : undefined}>
                  <span className="product-tab-label"><Icon />{item.label}</span>
                </Tooltip>
              ),
            }
          })}
        />
      </div>

      <main className={view === 'chat' ? 'product-content product-content-chat' : 'product-content'}>
        <Page />
      </main>
    </div>
  )
}

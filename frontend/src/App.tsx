import { ConfigProvider, Result, Spin } from 'antd'
import zhCN from 'antd/locale/zh_CN'

import { ProductShell } from './components/ProductShell'
import { ProductProvider, useProduct } from './context/ProductContext'
import { SetupPage } from './pages/SetupPage'
import './styles.css'

function ProductRoot() {
  const { config, site, loading, error, reset } = useProduct()
  if (loading) return <div className="app-loading"><Spin size="large" /><p>正在连接 SEO 优化专员...</p></div>
  if (!config) return <SetupPage />
  if (!site) return <Result status="error" title="无法加载站点" subTitle={error ?? '站点配置不可用'} extra={<button className="plain-action" onClick={reset}>重新初始化</button>} />
  return <ProductShell />
}

export default function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#2563eb',
          colorInfo: '#2563eb',
          colorSuccess: '#059669',
          colorText: '#0f172a',
          colorTextSecondary: '#64748b',
          colorBgLayout: '#f8fafc',
          colorBorder: '#e2e8f0',
          borderRadius: 10,
          controlHeight: 38,
          fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        },
      }}
    >
      <ProductProvider><ProductRoot /></ProductProvider>
    </ConfigProvider>
  )
}

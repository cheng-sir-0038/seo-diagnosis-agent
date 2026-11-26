import './components/disclosure.css'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'

import { ProductShell } from './components/ProductShell'
import './styles.css'
import './pages/workspace.css'

const themeVariables = {
  colorPrimary: '--color-primary',
  colorInfo: '--color-primary',
  colorSuccess: '--color-success',
  colorText: '--color-text',
  colorTextSecondary: '--color-text-muted',
  colorBgLayout: '--color-bg',
  colorBorder: '--color-border',
  fontFamily: '--font-ui',
}

export default function App() {
  const rootStyle = getComputedStyle(document.documentElement)
  const tokens = Object.fromEntries(
    Object.entries(themeVariables)
      .map(([key, variable]) => [key, rootStyle.getPropertyValue(variable).trim()])
      .filter(([, value]) => value),
  )
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        components: {
          Tabs: { horizontalItemMargin: '0 0 0 32px', horizontalItemMarginRTL: '0 32px 0 0' },
        },
        token: {
          ...tokens,
          borderRadius: 10,
          controlHeight: 38,
        },
      }}
    >
      <ProductShell />
    </ConfigProvider>
  )
}

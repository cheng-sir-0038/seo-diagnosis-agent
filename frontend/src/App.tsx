import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'

import { ProductShell } from './components/ProductShell'
import './styles.css'

export default function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        components: {
          Tabs: { horizontalItemMargin: '0 0 0 32px', horizontalItemMarginRTL: '0 32px 0 0' },
        },
        token: {
          colorPrimary: '#6948e9',
          colorInfo: '#6948e9',
          colorSuccess: '#11754d',
          colorText: '#221a38',
          colorTextSecondary: '#5d5572',
          colorBgLayout: '#f7f5ff',
          colorBorder: '#e4def6',
          borderRadius: 10,
          controlHeight: 38,
          fontFamily: 'system-ui, "PingFang SC", "Microsoft YaHei", sans-serif',
        },
      }}
    >
      <ProductShell />
    </ConfigProvider>
  )
}

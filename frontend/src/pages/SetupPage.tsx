import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  GlobalOutlined,
  LockOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Alert, Button, Form, Input, Select } from 'antd'
import type { FormProps } from 'antd'
import { useState } from 'react'

import { useProduct } from '../context/ProductContext'

interface SetupValues {
  siteName: string
  domain: string
  country: string
  language: string
  timezone: string
  ownerName: string
  ownerEmail: string
  executorName: string
  executorEmail: string
}

const setupSteps = [
  ['连接站点', '建立站点、语言和时区上下文'],
  ['配置角色', '创建负责人、SEO 专员和人工执行者'],
  ['进入对话', '从 GPT 风格工作区开始分析与协作'],
]

const countryOptions = [
  { value: 'US', label: '美国（US）' },
  { value: 'GB', label: '英国（GB）' },
  { value: 'CA', label: '加拿大（CA）' },
  { value: 'AU', label: '澳大利亚（AU）' },
  { value: 'CN', label: '中国大陆（CN）' },
  { value: 'TW', label: '中国台湾（TW）' },
  { value: 'HK', label: '中国香港（HK）' },
  { value: 'SG', label: '新加坡（SG）' },
  { value: 'JP', label: '日本（JP）' },
  { value: 'KR', label: '韩国（KR）' },
  { value: 'DE', label: '德国（DE）' },
  { value: 'FR', label: '法国（FR）' },
  { value: 'ES', label: '西班牙（ES）' },
  { value: 'IN', label: '印度（IN）' },
  { value: 'BR', label: '巴西（BR）' },
]

const languageOptions = [
  { value: 'en-US', label: '英语（美国）· en-US' },
  { value: 'zh-CN', label: '简体中文 · zh-CN' },
  { value: 'zh-TW', label: '繁体中文 · zh-TW' },
  { value: 'fr-FR', label: '法语 · fr-FR' },
  { value: 'de-DE', label: '德语 · de-DE' },
  { value: 'es-ES', label: '西班牙语 · es-ES' },
  { value: 'ja-JP', label: '日语 · ja-JP' },
  { value: 'ko-KR', label: '韩语 · ko-KR' },
]

export function SetupPage() {
  const { initialize, loading, error } = useProduct()
  const [form] = Form.useForm<SetupValues>()
  const [validationError, setValidationError] = useState<string | null>(null)

  const handleFinish = (values: SetupValues) => {
    setValidationError(null)
    void initialize(values)
  }

  const handleFinishFailed: FormProps<SetupValues>['onFinishFailed'] = ({ errorFields }) => {
    setValidationError('请先完成所有必填项')
    const firstError = errorFields[0]?.name
    if (!firstError) return
    form.scrollToField(firstError, { behavior: 'smooth', block: 'center' })
    const field = form.getFieldInstance(firstError) as { focus?: () => void } | undefined
    field?.focus?.()
  }

  return (
    <main className="setup-page">
      <section className="setup-intro">
        <div className="setup-brand">
          <div className="setup-brand-mark" aria-hidden="true">S</div>
          <div><strong>SEO 优化专员</strong><span>AI 增长工作台</span></div>
        </div>

        <div className="setup-copy">
          <span className="eyebrow">SEO SPECIALIST AGENT</span>
          <h1>先建立工作区，<br />再从一次对话开始。</h1>
          <p>SEO 优化专员会围绕站点持续整理机会、证据、任务和执行记录。这里仅创建业务身份，不在浏览器保存 API Key 或邮箱授权码。</p>
        </div>

        <div className="setup-benefits">
          <div><GlobalOutlined /><span><strong>站点上下文</strong><small>国家、语言和时区统一进入后续分析</small></span></div>
          <div><TeamOutlined /><span><strong>清晰的协作角色</strong><small>负责人审批，人工执行者完成交付</small></span></div>
          <div><LockOutlined /><span><strong>凭证留在后端</strong><small>前端只保存站点与角色业务 ID</small></span></div>
        </div>

        <div className="setup-flow">
          {setupSteps.map(([title, description], index) => (
            <div className="setup-flow-row" key={title}>
              <span>{index + 1}</span>
              <div><strong>{title}</strong><small>{description}</small></div>
              {index === 0 ? <em>当前步骤</em> : null}
            </div>
          ))}
        </div>
      </section>

      <section className="setup-form-column">
        <div className="setup-panel">
          <header className="setup-panel-head">
            <span>工作区初始化</span>
            <h2>配置你的 SEO 优化专员</h2>
            <p>完成后会直接进入对话主界面，其他能力通过顶部 Tab 切换。</p>
          </header>

          {error ? <Alert type="error" showIcon message={error} /> : null}

          <Form<SetupValues>
            form={form}
            layout="vertical"
            size="large"
            validateTrigger="onBlur"
            initialValues={{
              siteName: 'SEO 演示站点',
              country: 'US',
              language: 'en-US',
              timezone: 'Asia/Shanghai',
              ownerName: '站点负责人',
              executorName: '内容运营执行者',
            }}
            onFinish={handleFinish}
            onFinishFailed={handleFinishFailed}
            onFieldsChange={() => validationError && setValidationError(null)}
          >
            <div className="setup-form-scroll">
              <section className="form-section">
                <div className="form-section-heading">
                  <span><GlobalOutlined /></span>
                  <div><h3>站点信息</h3><p>用于确定后续查询和展示的默认范围。</p></div>
                </div>
                <div className="form-grid two-columns">
                  <Form.Item label="站点名称" name="siteName" rules={[{ required: true, message: '请输入站点名称' }]}>
                    <Input placeholder="例如：Waterjet Growth Lab" autoComplete="organization" />
                  </Form.Item>
                  <Form.Item label="站点域名" name="domain" rules={[{ required: true, message: '请输入站点域名' }]}>
                    <Input placeholder="example.com" autoComplete="url" />
                  </Form.Item>
                  <Form.Item label="目标国家" name="country" rules={[{ required: true, message: '请选择目标国家' }]}>
                    <Select
                      showSearch
                      optionFilterProp="label"
                      placeholder="请选择目标国家"
                      options={countryOptions}
                    />
                  </Form.Item>
                  <Form.Item label="默认语言" name="language" rules={[{ required: true, message: '请选择默认语言' }]}>
                    <Select
                      showSearch
                      optionFilterProp="label"
                      placeholder="请选择默认语言"
                      options={languageOptions}
                    />
                  </Form.Item>
                  <Form.Item className="form-span-two" label="站点时区" name="timezone" rules={[{ required: true, message: '请选择站点时区' }]}>
                    <Select
                      options={[
                        { value: 'Asia/Shanghai', label: 'Asia/Shanghai' },
                        { value: 'UTC', label: 'UTC' },
                        { value: 'Europe/London', label: 'Europe/London' },
                        { value: 'America/New_York', label: 'America/New_York' },
                      ]}
                    />
                  </Form.Item>
                </div>
              </section>

              <section className="form-section">
                <div className="form-section-heading">
                  <span><TeamOutlined /></span>
                  <div><h3>协作角色</h3><p>负责人接收决策，执行者负责实际内容交付。</p></div>
                </div>
                <div className="form-grid two-columns">
                  <Form.Item label="负责人名称" name="ownerName" rules={[{ required: true, message: '请输入负责人名称' }]}>
                    <Input autoComplete="name" />
                  </Form.Item>
                  <Form.Item label="负责人邮箱" name="ownerEmail" rules={[{ required: true, message: '请输入负责人邮箱' }, { type: 'email', message: '请输入有效邮箱' }]}>
                    <Input placeholder="owner@qq.com" autoComplete="email" />
                  </Form.Item>
                  <Form.Item label="执行者名称" name="executorName" rules={[{ required: true, message: '请输入执行者名称' }]}>
                    <Input autoComplete="name" />
                  </Form.Item>
                  <Form.Item label="执行者邮箱" name="executorEmail" rules={[{ required: true, message: '请输入执行者邮箱' }, { type: 'email', message: '请输入有效邮箱' }]}>
                    <Input placeholder="writer@qq.com" autoComplete="email" />
                  </Form.Item>
                </div>
              </section>
            </div>

            <div className="setup-actions">
              {validationError ? <Alert type="warning" showIcon title={validationError} /> : null}
              <div className="setup-submit-note"><CheckCircleOutlined />创建后仍可重新初始化；后端凭证需要在服务端单独配置。</div>
              <Button type="primary" htmlType="submit" size="large" block loading={loading} iconPlacement="end" icon={<ArrowRightOutlined />}>
                创建并进入对话工作区
              </Button>
            </div>
          </Form>
        </div>
      </section>
    </main>
  )
}

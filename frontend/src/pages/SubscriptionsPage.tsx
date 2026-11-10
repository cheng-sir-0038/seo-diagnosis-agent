import { MailOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Form, Input, message, Modal, Skeleton, Switch } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { useProduct } from '../context/ProductContext'
import { productApi } from '../services/productApi'
import type { NotificationPreference, NotificationSubscription } from '../types/product'

const eventDescriptions: Record<string, string> = {
  approval_requested: '具体动作进入待审批时立即发送，默认审批人不可关闭。',
  high_value_opportunity: '发现评分达到高价值门槛的机会。',
  task_progressed: '任务派发、交付、退回等进展。',
  task_completed: '任务完成或效果验证结束。',
  ranking_changed: '达到配置阈值的排名变化。',
  daily_digest: 'SEO 优化专员每日工作摘要。',
  weekly_digest: '每周机会、任务和结果总结。',
  monthly_digest: '扩展占位，MVP 不执行。',
}

export function SubscriptionsPage() {
  const { config } = useProduct()
  const [subscriptions, setSubscriptions] = useState<NotificationSubscription[]>([])
  const [preferences, setPreferences] = useState<Record<string, NotificationPreference[]>>({})
  const [loading, setLoading] = useState(true)
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    if (!config) return
    setLoading(true)
    try {
      const items = await productApi.listSubscriptions(config.siteId)
      setSubscriptions(items)
      const pairs = await Promise.all(items.map(async (item) => [item.id, await productApi.listPreferences(item.id)] as const))
      setPreferences(Object.fromEntries(pairs))
    } finally {
      setLoading(false)
    }
  }, [config])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const create = async (values: { email: string; role: string }) => {
    if (!config) return
    await productApi.createSubscription({
      site_id: config.siteId,
      recipient_email: values.email,
      recipient_role: values.role,
      is_default_approver: false,
    })
    message.success('订阅人已添加')
    setModalOpen(false)
    form.resetFields()
    await load()
  }

  const toggle = async (subscriptionId: string, preference: NotificationPreference, enabled: boolean) => {
    try {
      await productApi.setPreference(subscriptionId, preference.event_key, enabled)
      await load()
    } catch (cause) {
      message.error((cause as Error).message)
    }
  }

  if (loading) return <Skeleton active paragraph={{ rows: 10 }} />
  return (
    <div className="page-content">
      <PageHeader title="订阅设置" description="MVP 只实现邮件渠道；QQ 邮箱通过标准 SMTP Provider 配置。" actions={<><Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>添加订阅人</Button></>} />
      <Alert type="info" showIcon message="发件账号和 QQ SMTP 授权码只保存在后端 .env.local；这里配置的是站点收件人和事件偏好。" />
      {subscriptions.length === 0 ? <section className="panel-card"><Empty description="暂无邮件订阅人" /></section> : (
        <div className="subscription-grid">
          {subscriptions.map((subscription) => (
            <section className="panel-card subscription-card" key={subscription.id}>
              <div className="subscription-head"><div className="mail-icon"><MailOutlined /></div><div><h2>{subscription.recipient_email}</h2><p>{subscription.recipient_role}</p></div><StatusPill value={subscription.is_default_approver ? 'DEFAULT_APPROVER' : 'SUBSCRIBER'} /></div>
              <div className="preference-list">
                {(preferences[subscription.id] ?? []).map((preference) => (
                  <div className="preference-row" key={preference.id}>
                    <div><strong>{preference.name}</strong><p>{eventDescriptions[preference.event_key] ?? preference.description}</p><small>{preference.delivery_mode}</small></div>
                    <Switch checked={Boolean(preference.enabled)} disabled={Boolean(preference.mandatory)} onChange={(checked) => void toggle(subscription.id, preference, checked)} />
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
      <Modal title="添加邮件订阅人" open={modalOpen} onCancel={() => setModalOpen(false)} onOk={() => form.submit()}>
        <Form form={form} layout="vertical" initialValues={{ role: 'OBSERVER' }} onFinish={(values) => void create(values)}>
          <Form.Item label="收件邮箱" name="email" rules={[{ required: true, type: 'email' }]}><Input /></Form.Item>
          <Form.Item label="订阅角色" name="role" rules={[{ required: true }]}><Input /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

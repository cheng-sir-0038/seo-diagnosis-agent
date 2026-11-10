import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Drawer, Empty, Form, Input, message, Modal, Select, Table, Tabs } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { useProduct } from '../context/ProductContext'
import { productApi } from '../services/productApi'
import type { Opportunity } from '../types/product'

export function OpportunitiesPage() {
  const { config } = useProduct()
  const [items, setItems] = useState<Opportunity[]>([])
  const [activePriority, setActivePriority] = useState<string>('ALL')
  const [selected, setSelected] = useState<Opportunity | null>(null)
  const [taskModal, setTaskModal] = useState(false)
  const [loading, setLoading] = useState(true)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    if (!config) return
    setLoading(true)
    try {
      setItems(await productApi.listOpportunities(config.siteId))
    } finally {
      setLoading(false)
    }
  }, [config])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const filtered = useMemo(
    () => activePriority === 'ALL' ? items : items.filter((item) => item.priority_level === activePriority),
    [items, activePriority],
  )

  const openDetail = async (item: Opportunity) => setSelected(await productApi.getOpportunity(item.id))

  const createTask = async (values: { title: string; taskType: string; priority: string }) => {
    if (!config || !selected) return
    await productApi.createTask({
      site_id: config.siteId,
      title: values.title,
      task_type: values.taskType,
      priority: values.priority,
      opportunity_ids: [selected.id],
      owner_actor_id: config.ownerActorId,
    })
    message.success('内部任务已创建；PRD 内容等待真实模型配置后生成')
    setTaskModal(false)
    form.resetFields()
    setSelected(await productApi.getOpportunity(selected.id))
  }

  const columns: ColumnsType<Opportunity> = [
    { title: '机会', dataIndex: 'title', render: (_, item) => <button className="link-button" onClick={() => void openDetail(item)}><strong>{item.title}</strong><small>{item.normalized_intent}</small></button> },
    { title: '评分', dataIndex: 'current_score', width: 90, render: (value) => <span className="table-score">{value}</span> },
    { title: '优先级', dataIndex: 'priority_level', width: 110, render: (value) => <StatusPill value={value} /> },
    { title: '状态', dataIndex: 'status', width: 180, render: (value) => <StatusPill value={value} /> },
    { title: '最近发现', dataIndex: 'last_seen_at', width: 180, render: (value) => new Date(value).toLocaleString() },
  ]

  return (
    <div className="page-content">
      <PageHeader title="增长机会" description="识别并更新同一机会；历史观测不会被覆盖。" actions={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>} />
      <Tabs activeKey={activePriority} onChange={setActivePriority} items={[
        { key: 'ALL', label: `全部 ${items.length}` },
        { key: 'HIGH', label: `高价值 ${items.filter((item) => item.priority_level === 'HIGH').length}` },
        { key: 'MEDIUM', label: `中等 ${items.filter((item) => item.priority_level === 'MEDIUM').length}` },
        { key: 'LOW', label: `低价值 ${items.filter((item) => item.priority_level === 'LOW').length}` },
      ]} />
      <section className="panel-card table-card">
        <Table rowKey="id" columns={columns} dataSource={filtered} loading={loading} pagination={{ pageSize: 20 }} locale={{ emptyText: <Empty description="暂无机会；诊断层只会通过结构化候选写入这里" /> }} />
      </section>

      <Drawer title="机会详情" size="large" open={Boolean(selected)} onClose={() => setSelected(null)} extra={selected ? <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.setFieldsValue({ title: selected.title, taskType: 'ARTICLE', priority: selected.priority_level }); setTaskModal(true) }}>创建执行任务</Button> : null}>
        {selected ? <div className="detail-stack">
          <div className="detail-hero"><div className="score-orb large">{selected.current_score}</div><div><h2>{selected.title}</h2><p>{selected.current_summary}</p><div className="inline-meta"><StatusPill value={selected.priority_level} /><StatusPill value={selected.status} /></div></div></div>
          <section><h3>关键词</h3><div className="tag-list">{selected.keywords?.map((keyword) => <span key={keyword.id}>{keyword.normalized_keyword}</span>)}</div></section>
          <section><h3>观测历史</h3><pre className="json-preview">{JSON.stringify(selected.observations ?? [], null, 2)}</pre></section>
          <section><h3>状态历史</h3><pre className="json-preview">{JSON.stringify(selected.status_history ?? [], null, 2)}</pre></section>
          <section><h3>关联任务</h3>{selected.tasks?.length ? selected.tasks.map((task) => <div className="compact-row" key={task.id}><strong>{task.title}</strong><StatusPill value={task.status} /></div>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未创建任务" />}</section>
        </div> : null}
      </Drawer>

      <Modal title="创建内部任务" open={taskModal} onCancel={() => setTaskModal(false)} onOk={() => form.submit()}>
        <Form form={form} layout="vertical" onFinish={(values) => void createTask(values)}>
          <Form.Item label="任务标题" name="title" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="任务类型" name="taskType" rules={[{ required: true }]}><Select options={[{ value: 'ARTICLE', label: '文章' }, { value: 'FAQ', label: 'FAQ' }, { value: 'LANDING_PAGE', label: '落地页' }]} /></Form.Item>
          <Form.Item label="优先级" name="priority" rules={[{ required: true }]}><Select options={['HIGH', 'MEDIUM', 'LOW'].map((value) => ({ value, label: value }))} /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

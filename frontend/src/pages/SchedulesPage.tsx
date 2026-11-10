import { HistoryOutlined, PlusOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { Button, Drawer, Empty, Form, Input, message, Modal, Select, Switch, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { useProduct } from '../context/ProductContext'
import { productApi } from '../services/productApi'
import type { ScheduleRun, WorkSchedule } from '../types/product'

export function SchedulesPage() {
  const { config, site } = useProduct()
  const [items, setItems] = useState<WorkSchedule[]>([])
  const [runs, setRuns] = useState<ScheduleRun[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [form] = Form.useForm()
  const [selectedFrequency, setSelectedFrequency] = useState('DAILY')

  const load = useCallback(async () => {
    if (!config) return
    setLoading(true)
    try { setItems(await productApi.listSchedules(config.siteId)) } finally { setLoading(false) }
  }, [config])
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const createSchedule = async (values: { name: string; frequency: string; executionTime: string; weekday?: number }) => {
    if (!config || !site) return
    await productApi.createSchedule({
      site_id: config.siteId,
      schedule_type: 'OPPORTUNITY_SCAN',
      name: values.name,
      frequency: values.frequency,
      timezone: site.timezone,
      execution_time: values.executionTime,
      weekday: values.weekday,
    })
    message.success('工作计划已创建')
    setModalOpen(false)
    await load()
  }

  const runNow = async (schedule: WorkSchedule) => {
    const result = await productApi.startScan(schedule.id)
    message.info(result.status === 'RUNNING' ? '扫描运行已创建，等待诊断引擎提交结构化候选' : `运行状态：${result.status}`)
    await load()
  }

  const openHistory = async (schedule: WorkSchedule) => {
    setRuns(await productApi.listScheduleRuns(schedule.id))
    setHistoryOpen(true)
  }

  const columns: ColumnsType<WorkSchedule> = [
    { title: '计划', dataIndex: 'name', render: (_, item) => <div><strong>{item.name}</strong><small>{item.schedule_type}</small></div> },
    { title: '频率', dataIndex: 'frequency', width: 100 },
    { title: '执行时间', dataIndex: 'execution_time', width: 120, render: (value) => value ?? '—' },
    { title: '时区', dataIndex: 'timezone', width: 160 },
    { title: '启用', dataIndex: 'enabled', width: 90, render: (value, item) => <Switch checked={Boolean(value)} onChange={async (checked) => { await productApi.setScheduleEnabled(item.id, checked); await load() }} /> },
    { title: '操作', width: 210, render: (_, item) => <div className="table-actions"><Button icon={<ThunderboltOutlined />} onClick={() => void runNow(item)}>立即执行</Button><Button icon={<HistoryOutlined />} onClick={() => void openHistory(item)}>历史</Button></div> },
  ]

  return (
    <div className="page-content">
      <PageHeader title="工作计划" description="MVP 每个站点只允许一个启用中的主机会扫描计划。" actions={<Button type="primary" icon={<PlusOutlined />} disabled={items.some((item) => item.enabled)} onClick={() => setModalOpen(true)}>创建计划</Button>} />
      <section className="panel-card table-card"><Table rowKey="id" columns={columns} dataSource={items} loading={loading} pagination={false} locale={{ emptyText: <Empty description="尚未创建工作计划" /> }} /></section>
      <Modal title="创建主机会扫描计划" open={modalOpen} onCancel={() => setModalOpen(false)} onOk={() => form.submit()}>
        <Form
          form={form}
          layout="vertical"
          initialValues={{ name: 'SEO 机会扫描', frequency: 'DAILY', executionTime: '06:30' }}
          onValuesChange={(changed) => {
            if (typeof changed.frequency === 'string') setSelectedFrequency(changed.frequency)
          }}
          onFinish={(values) => void createSchedule(values)}
        >
          <Form.Item label="计划名称" name="name" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="执行频率" name="frequency" rules={[{ required: true }]}><Select options={[{ value: 'DAILY', label: '每天' }, { value: 'WEEKLY', label: '每周' }]} /></Form.Item>
          <Form.Item label="执行时间" name="executionTime" rules={[{ required: true }]}><Input type="time" /></Form.Item>
          {selectedFrequency === 'WEEKLY' ? (
            <Form.Item label="星期" name="weekday">
              <Select options={[1, 2, 3, 4, 5, 6, 7].map((value) => ({ value, label: `星期${value}` }))} />
            </Form.Item>
          ) : null}
        </Form>
      </Modal>
      <Drawer title="运行历史" size="large" open={historyOpen} onClose={() => setHistoryOpen(false)}>
        {runs.length === 0 ? <Empty description="暂无运行历史" /> : runs.map((run) => <article className="history-row" key={run.id}><div><strong>{run.trigger_type}</strong><p>{run.started_at ? new Date(run.started_at).toLocaleString() : '未启动'}</p></div><StatusPill value={run.status} /></article>)}
      </Drawer>
    </div>
  )
}

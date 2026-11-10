import { AuditOutlined, DownloadOutlined, ReloadOutlined, RobotOutlined } from '@ant-design/icons'
import { Button, Drawer, Empty, message, Skeleton, Table, Tabs } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { useProduct } from '../context/ProductContext'
import { productApi } from '../services/productApi'
import type { ExecutionTask } from '../types/product'

export function TasksPage() {
  const { config } = useProduct()
  const [items, setItems] = useState<ExecutionTask[]>([])
  const [selected, setSelected] = useState<ExecutionTask | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    if (!config) return
    setLoading(true)
    try { setItems(await productApi.listTasks(config.siteId)) } finally { setLoading(false) }
  }, [config])
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const open = async (task: ExecutionTask) => setSelected(await productApi.getTask(task.id))
  const renderArtifacts = async (prdId: string) => {
    const artifacts = await productApi.renderPrdArtifacts(prdId)
    message.success(`已生成 ${artifacts.length} 个固定版本产物`)
    if (selected) setSelected(await productApi.getTask(selected.id))
  }

  const generatePrd = async () => {
    if (!config || !selected) return
    try {
      await productApi.generateTaskPrd(selected.id, config.ownerActorId)
      message.success('DeepSeek 已生成结构化 PRD 和固定下载产物')
      setSelected(await productApi.getTask(selected.id))
      await load()
    } catch (cause) {
      message.error((cause as Error).message)
    }
  }

  const reviewDelivery = async (deliveryId: string) => {
    if (!config || !selected) return
    try {
      await productApi.reviewDeliveryWithAgent(deliveryId, config.ownerActorId)
      message.success('DeepSeek 已完成结构化交付验收')
      setSelected(await productApi.getTask(selected.id))
      await load()
    } catch (cause) {
      message.error((cause as Error).message)
    }
  }

  const columns: ColumnsType<ExecutionTask> = [
    { title: '任务', dataIndex: 'title', render: (_, task) => <button className="link-button" onClick={() => void open(task)}><strong>{task.title}</strong><small>{task.task_type}</small></button> },
    { title: '优先级', dataIndex: 'priority', width: 100, render: (value) => <StatusPill value={value} /> },
    { title: '状态', dataIndex: 'status', width: 190, render: (value) => <StatusPill value={value} /> },
    { title: '截止时间', dataIndex: 'deadline_at', width: 180, render: (value) => value ? new Date(value).toLocaleString() : '—' },
    { title: '创建时间', dataIndex: 'created_at', width: 180, render: (value) => new Date(value).toLocaleString() },
  ]

  return (
    <div className="page-content">
      <PageHeader title="任务中心" description="任务、PRD、派发、交付、验收、发布和效果状态在同一对象中追踪。" actions={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>} />
      {loading ? <Skeleton active /> : <section className="panel-card table-card"><Table rowKey="id" columns={columns} dataSource={items} pagination={{ pageSize: 20 }} locale={{ emptyText: <Empty description="暂无执行任务；从机会详情创建任务" /> }} /></section>}
      <Drawer title="任务详情" size="large" open={Boolean(selected)} onClose={() => setSelected(null)}>
        {selected ? <div className="detail-stack">
          <div className="detail-title-row"><div><h2>{selected.title}</h2><p>{selected.task_type}</p></div><StatusPill value={selected.status} /></div>
          <Tabs items={[
            { key: 'overview', label: '概览', children: <div className="detail-stack"><section><h3>关联机会</h3>{selected.opportunities?.map((item) => <div className="compact-row" key={item.id}><span>{item.title}</span><StatusPill value={item.priority_level} /></div>)}</section><section><h3>参与者</h3><pre className="json-preview">{JSON.stringify(selected.assignments ?? [], null, 2)}</pre></section></div> },
            {
              key: 'prd',
              label: `PRD ${selected.prd_versions?.length ?? 0}`,
              children: (
                <div className="detail-stack">
                  <div className="detail-toolbar">
                    <Button type="primary" icon={<RobotOutlined />} onClick={() => void generatePrd()}>
                      {selected.prd_versions?.length ? 'AI 生成新版本' : 'AI 生成 PRD'}
                    </Button>
                    <span>需要后端配置真实 DEEPSEEK_API_KEY；未配置时会明确失败。</span>
                  </div>
                  {selected.prd_versions?.length ? (
                    <div className="version-list">
                      {selected.prd_versions.map((prd) => (
                        <article key={prd.id}>
                          <div>
                            <strong>V{prd.version_no} · {prd.title}</strong>
                            <p>{new Date(prd.created_at).toLocaleString()}</p>
                          </div>
                          <Button icon={<DownloadOutlined />} onClick={() => void renderArtifacts(prd.id)}>
                            生成下载文件
                          </Button>
                          <pre>{prd.markdown_content}</pre>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <Empty description="尚未生成 PRD" />
                  )}
                </div>
              ),
            },
            {
              key: 'delivery',
              label: `交付 ${selected.deliveries?.length ?? 0}`,
              children: selected.deliveries?.length ? (
                <div className="version-list">
                  {selected.deliveries.map((delivery) => (
                    <article key={delivery.id}>
                      <div className="compact-row">
                        <strong>V{delivery.version_no} · {delivery.title}</strong>
                        <div className="table-actions">
                          <StatusPill value={delivery.status} />
                          <Button
                            icon={<AuditOutlined />}
                            disabled={delivery.status === 'ACCEPTED'}
                            onClick={() => void reviewDelivery(delivery.id)}
                          >
                            AI 验收
                          </Button>
                        </div>
                      </div>
                      <p>{delivery.summary}</p>
                      <pre className="json-preview">{JSON.stringify(delivery.reviews ?? [], null, 2)}</pre>
                    </article>
                  ))}
                </div>
              ) : (
                <Empty description="尚未提交交付物" />
              ),
            },
            { key: 'history', label: '状态历史', children: <pre className="json-preview">{JSON.stringify(selected.status_history ?? [], null, 2)}</pre> },
          ]} />
        </div> : null}
      </Drawer>
    </div>
  )
}

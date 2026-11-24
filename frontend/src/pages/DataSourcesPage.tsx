import {
  BarChartOutlined, DatabaseOutlined, MedicineBoxOutlined, ReloadOutlined, RobotOutlined,
  SendOutlined, SettingOutlined, ShoppingCartOutlined,
} from '@ant-design/icons'
import { Alert, Button, Input, Modal, Popconfirm, Switch } from 'antd'
import { createElement, useEffect, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { listWorkspaceSources, saveWorkspaceSource } from '../services/workspaceApi'
import type { WorkspaceSource } from '../types/workspace'

const sourceGroupIcons = new Map<string, typeof DatabaseOutlined>([
  ['模型', RobotOutlined],
  ['SEO / GEO', BarChartOutlined],
  ['商旅行程', SendOutlined],
  ['购物采购', ShoppingCartOutlined],
  ['医疗健康', MedicineBoxOutlined],
])

const modeLabels: Record<string, { text: string, tone: string }> = {
  live: { text: '客户端就绪', tone: 'success' },
  configured: { text: '已配置', tone: 'running' },
  builtin: { text: '内置来源', tone: 'success' },
  pseudo: { text: '兼容模式', tone: 'neutral' },
  disabled: { text: '已关闭', tone: 'neutral' },
  unconfigured: { text: '待配置', tone: 'waiting' },
  unavailable: { text: '当前不可用', tone: 'waiting' },
  missing_client: { text: '缺少客户端', tone: 'waiting' },
}

export default function DataSourcesPage() {
  const [sources, setSources] = useState<WorkspaceSource[]>([])
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState<number>()
  const [error, setError] = useState<string>()
  const [notice, setNotice] = useState<string>()
  const [editing, setEditing] = useState<WorkspaceSource>()
  const [changes, setChanges] = useState<Record<string, string | boolean>>({})
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string>()

  useEffect(() => {
    const controller = new AbortController()
    listWorkspaceSources(controller.signal).then(result => {
      if (controller.signal.aborted) return
      setSources(result)
      setError(undefined)
      setLoaded(attempt)
    }).catch((cause: Error) => {
      if (controller.signal.aborted) return
      setError(cause.message)
      setLoaded(attempt)
    })
    return () => controller.abort()
  }, [attempt])

  const startEdit = (source: WorkspaceSource) => {
    setChanges({})
    setFormError(undefined)
    setNotice(undefined)
    setEditing(source)
  }

  const save = async (values: Record<string, string | boolean>) => {
    if (!editing || !Object.keys(values).length) return
    setFormError(undefined)
    setSaving(true)
    try {
      const next = await saveWorkspaceSource(editing.id, values)
      setSources(next)
      const after = next.find(source => source.id === editing.id)
      setNotice(after?.restart_required ? '配置已保存，重启后端后生效。' : '配置已保存。')
      setEditing(undefined)
      setChanges({})
    } catch (cause) {
      setFormError((cause as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const clearable = editing?.fields.filter(field => field.editable && field.configured && field.kind !== 'switch') ?? []
  const editableChanges = Object.fromEntries(Object.entries(changes).filter(([, value]) =>
    typeof value === 'boolean' || value.trim().length > 0,
  ))

  return (
    <div className="page-content workspace-resources workspace-sources">
      <PageHeader title="数据源管理" description="查看当前客户端状态，管理本地数据源配置。"
        actions={<Button icon={<ReloadOutlined />} loading={loaded !== attempt} onClick={() => setAttempt(number => number + 1)}>刷新状态</Button>} />
      {error && <Alert type="error" showIcon title={error} />}
      {notice && <Alert type="success" showIcon title={notice} />}
      {sources.some(source => source.restart_required) && (
        <Alert type="warning" showIcon title="有已保存的配置待重启生效。" />
      )}
      {loaded !== attempt && sources.length === 0 && <p role="status" className="workbench-empty">正在读取数据源…</p>}
      {Array.from(new Set(sources.map(source => source.group))).map(group => (
        <section className="workbench-panel workspace-source-group" key={group} aria-label={group}>
          <h2>{createElement(sourceGroupIcons.get(group) ?? DatabaseOutlined, { 'aria-hidden': true })}{group}</h2>
          <ul>
            {sources.filter(source => source.group === group).map(source => {
              const mode = modeLabels[source.mode] ?? { text: source.mode, tone: 'neutral' }
              return (
                <li key={source.id}>
                  <div className="workspace-source-copy">
                    <strong>{source.name}</strong>
                    <small>{source.description}</small>
                  </div>
                  <span className={`workspace-state workspace-state-${mode.tone}`}>{mode.text}</span>
                  {source.restart_required && <span className="workspace-state workspace-state-waiting">待重启</span>}
                  {source.fields.some(field => field.editable) ? (
                    <Button size="small" icon={<SettingOutlined />} aria-label={`配置 ${source.name}`} onClick={() => startEdit(source)}>配置</Button>
                  ) : source.fields.length > 0 ? <small>由服务器管理</small> : null}
                </li>
              )
            })}
          </ul>
        </section>
      ))}

      <Modal title={editing ? `配置 ${editing.name}` : '数据源配置'} open={Boolean(editing)}
        onCancel={() => { if (!saving) { setEditing(undefined); setChanges({}) } }}
        onOk={() => void save(editableChanges)} okText="保存配置" cancelText="取消"
        confirmLoading={saving} okButtonProps={{ disabled: !Object.keys(editableChanges).length }}
      >
        {formError && <Alert type="error" showIcon title={formError} />}
        <div className="workspace-source-form">
          {editing?.fields.map(field => {
            if (field.kind === 'switch') {
              const pending = changes[field.id]
              return (
                <label key={field.id}>{field.label}<Switch aria-label={field.label} disabled={!field.editable}
                  checked={typeof pending === 'boolean' ? pending : field.configured}
                  onChange={value => setChanges(current => ({ ...current, [field.id]: value }))} /></label>
              )
            }
            const Field = field.kind === 'secret' ? Input.Password : Input
            const value = changes[field.id]
            return (
              <label key={field.id}>
                <span>{field.label} <small>{field.editable ? field.configured ? '已保存' : '待填写' : '由服务器管理'}</small></span>
                <Field aria-label={field.label} disabled={!field.editable} value={typeof value === 'string' ? value : ''}
                  placeholder={field.configured ? '留空保留当前配置' : '输入配置值…'} autoComplete="off"
                  onChange={event => setChanges(current => ({ ...current, [field.id]: event.target.value }))} />
              </label>
            )
          })}
        </div>
        {clearable.length > 0 && (
          <Popconfirm title="清空此数据源的可编辑凭据？" description="重启后端后，客户端读取空凭据。" okText="清空" cancelText="取消"
            onConfirm={() => save(Object.fromEntries(clearable.map(field => [field.id, ''])))}>
            <Button type="link" danger disabled={saving}>清空凭据</Button>
          </Popconfirm>
        )}
        <p className="workspace-resource-hint">当前状态来自本地客户端与配置。外部权限和额度以实际调用为准。</p>
      </Modal>
    </div>
  )
}

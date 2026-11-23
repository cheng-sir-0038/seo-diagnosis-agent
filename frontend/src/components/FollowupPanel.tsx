import { FileAddOutlined, ProfileOutlined } from '@ant-design/icons'
import { Alert, Button, Input, Tag } from 'antd'
import { useEffect, useRef, useState } from 'react'

import {
  followupActions,
  followupRequest,
  type Evidence,
  type FollowupAction,
  type FollowupCase,
  type MedicalRecord,
  type RecordFields,
} from '../services/followupApi'
import './followup-panel.css'

const recordLabels: Record<MedicalRecord['kind'], string> = {
  report: '检查报告',
  visit_plan: '已有安排',
  self_report: '用户自述',
  purchase: '购药记录',
  medication_note: '用药资料',
}

const planLabels = {
  unknown: '完成状态待确认',
  self_reported_done: '用户确认完成',
  documented_done: '有记录支持完成',
} as const

const editFields: Record<keyof RecordFields, string> = {
  record_date: '记录日期',
  value: '数值',
  unit: '单位',
  specimen: '标本',
  method: '方法',
}

const readValue = ({ value, unit }: RecordFields): string => [value, unit].filter(Boolean).join(' ') || '原值待核对'
const subjectPattern = /^[A-Za-z0-9_-]{1,40}$/

interface Props {
  runId: string
  access: string
  canContinue: boolean
  onContinue: (action: FollowupAction) => void
}

function FollowupResults({ data }: { data: FollowupCase }) {
  if (!data.draft) return null
  const evidence = new Map(data.records.map(entry => [entry.id, entry]))
  return (
    <div className="followup-results">
      <section aria-label="报告对照">
        <h4>报告对照</h4>
        {data.draft.comparisons.length === 0 && <p>目前没有具备两次记录的同名项目。</p>}
        <ul className="followup-findings">
          {data.draft.comparisons.map(({ item, earlier, later, delta, unit, reasons }) => {
            const before = evidence.get(earlier)
            const after = evidence.get(later)
            if (!before || !after) return null
            return (
              <li key={later}>
                <strong>{item}</strong>
                <p>{before.record.record_date ?? '日期待核对'} {readValue(before.record)} → {after.record.record_date ?? '日期待核对'} {readValue(after.record)}</p>
                <p>{delta !== null ? `后次 − 前次 = ${delta} ${unit}` : `待核对 · ${reasons.join('、')}`}</p>
                <small>{before.source} · {after.source}{(before.original || after.original) && ' · 含人工修订'}</small>
              </li>
            )
          })}
        </ul>
        {data.draft.issues.map(({ item, text, source }, index) => (
          <p key={`${source ?? item}:${index}`} className="followup-fact">{item} · {text}{source && <small>{source}</small>}</p>
        ))}
      </section>
      <section aria-label="复诊准备清单">
        <h4>复诊准备清单</h4>
        <ul className="followup-list">
          {data.files.filter(file => file.rows > 0).map(file => <li key={file.digest}>携带 {file.name}</li>)}
          {data.draft.plans.map(id => {
            const entry = evidence.get(id)
            if (!entry) return null
            const status = planLabels[entry.record.plan_status ?? 'unknown']
            return (
              <li key={id}>
                {entry.record.item} · {status} · {entry.record.due_date ?? '安排日期待确认'}
                <small>{entry.source}</small>
              </li>
            )
          })}
        </ul>
        {data.saved && (
          <div className="followup-saved">
            <strong>已保存 {data.saved.tasks.length} 条待办</strong>
            <ul className="followup-list">
              {data.saved.tasks.map(task => <li key={task.id}>{task.title} · {task.due_date ?? '日期待确认'}<small>{task.source}</small></li>)}
            </ul>
          </div>
        )}
      </section>
    </div>
  )
}

export default function FollowupPanel({ runId, access, canContinue, onContinue }: Props) {
  const [data, setData] = useState<FollowupCase>()
  const [subject, setSubject] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<{ entry: Evidence, fields: RecordFields }>()
  const [attempt, setAttempt] = useState(0)
  const picker = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    followupRequest(runId, access, '', { signal: controller.signal }).then(setData).catch(cause => {
      if (!controller.signal.aborted) setError((cause as Error).message)
    })
    return () => controller.abort()
  }, [runId, access, attempt])

  async function update(path: string, options: RequestInit) {
    setLoading(true)
    setError('')
    try {
      setData(await followupRequest(runId, access, path, options))
      setEditing(undefined)
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }

  function edit(entry: Evidence) {
    const { record_date, value, unit, specimen, method } = entry.record
    setEditing({ entry, fields: { record_date, value, unit, specimen, method } })
  }

  if (!data) return error
    ? <Alert type="error" title={error} action={<Button onClick={() => { setError(''); setAttempt(attempt + 1) }}>重试</Button>} />
    : <p role="status">正在读取本次资料…</p>

  const subjectId = data.subject_id ?? subject.trim()
  const writable = canContinue && !data.saved
  const reportDates = new Set(data.records.filter(entry => entry.record.kind === 'report' && entry.record.record_date)
    .map(entry => entry.record.record_date)).size

  return (
    <section className="followup-panel" aria-label="复诊资料区">
      <header className="followup-header">
        <div><ProfileOutlined aria-hidden="true" /><h3>复诊资料区</h3></div>
        <div>
          {data.demo && <Tag>合成演示</Tag>}
          {data.saved && <Tag color="success">已保存</Tag>}
        </div>
      </header>
      {error && <Alert type="error" title={error} showIcon />}
      <div className="followup-input">
        <span>{data.subject_id ? `对象 ${data.subject_id}` : '先填写本次资料的匿名对象编号'}</span>
        {writable && (
          <div className="followup-buttons">
            {!data.subject_id && <Input value={subject} onChange={event => setSubject(event.target.value)} aria-label="对象编号" placeholder="例如 CASE-01" maxLength={40} />}
            <input ref={picker} type="file" hidden accept=".csv,.json,.pdf" onChange={event => {
              const file = event.target.files?.item(0)
              event.target.value = ''
              if (!file) return
              void update('/materials', {
                method: 'POST',
                headers: { 'X-Followup-Subject': subjectId, 'X-Followup-File': encodeURIComponent(file.name) },
                body: file,
              })
            }} />
            <Button icon={<FileAddOutlined />} loading={loading} disabled={!subjectPattern.test(subjectId)}
              onClick={() => picker.current?.click()}>导入资料</Button>
            {data.demo && !data.files.some(file => file.name === '合成补充报告.csv') && (
              <Button loading={loading} onClick={() => void update('/demo-supplement', { method: 'POST' })}>载入合成补充报告</Button>
            )}
          </div>
        )}
      </div>
      {writable && <small className="followup-hint">支持 CSV、JSON 和模板文字 PDF。请使用匿名编号，由当前服务整理资料。</small>}
      <ul className="followup-files">
        {data.files.map(file => <li key={file.digest}>
          <strong>{file.name}</strong>
          <span role={file.error ? 'alert' : undefined}>{file.error ?? `${file.rows} 条记录`}</span>
          <small>导入于 {new Date(file.uploaded_at).toLocaleDateString('zh-CN')}</small>
        </li>)}
      </ul>
      {data.records.length > 0 && (
        <details className="followup-timeline" open={data.draft === null}>
          <summary>记录时间线 <span>{data.records.length} 条</span></summary>
          <div className="followup-table-scroll">
            <table aria-label="资料与记录摘要">
              <thead><tr><th>记录日期</th><th>项目与原值</th><th>资料来源</th>{writable && <th>核对</th>}</tr></thead>
              <tbody>{data.records.map(entry => <tr key={entry.id}>
                <td>{entry.record.record_date ?? '待确认'}</td>
                <td>
                  <strong>{entry.record.item}</strong> · {recordLabels[entry.record.kind]}
                  {entry.record.kind === 'report' && <small>
                    {[readValue(entry.record), entry.record.specimen, entry.record.method, entry.record.reference_range && `参考范围 ${entry.record.reference_range}`, entry.record.flag].filter(Boolean).join(' · ')}
                  </small>}
                  {entry.record.note && <small>{entry.record.note}</small>}
                </td>
                <td>{entry.source}{entry.original && <small>已修订 · 原记录 {entry.original.record_date ?? '日期待确认'}，{readValue(entry.original)}</small>}</td>
                {writable && <td><Button type="link" size="small" onClick={() => edit(entry)}>修订</Button></td>}
              </tr>)}</tbody>
            </table>
          </div>
          {editing && (
            <form className="followup-edit" onSubmit={event => {
              event.preventDefault()
              void update(`/records/${encodeURIComponent(editing.entry.id)}`, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ revision: data.revision, fields: editing.fields }),
              })
            }}>
              <strong>修订 {editing.entry.record.item}</strong>
              {(Object.keys(editFields) as (keyof RecordFields)[]).map(field => (
                <label key={field}>{editFields[field]}<Input value={editing.fields[field] ?? ''} type={field === 'record_date' ? 'date' : 'text'} onChange={event => {
                  setEditing({ ...editing, fields: { ...editing.fields, [field]: event.target.value || null } })
                }} /></label>
              ))}
              <div className="followup-buttons">
                <Button htmlType="submit" type="primary" loading={loading}>保存修订</Button>
                <Button onClick={() => setEditing(undefined)}>取消修订</Button>
              </div>
            </form>
          )}
        </details>
      )}
      <FollowupResults data={data} />
      {writable && (
        <footer className="followup-buttons">
          {data.draft
            ? <Button type="primary" disabled={loading || !!editing} onClick={() => onContinue(followupActions.confirm)}>保存资料包与 {data.draft.pending_plans.length} 条待办</Button>
            : <Button type="primary" disabled={loading || !!editing || reportDates < 2} onClick={() => onContinue(followupActions.prepare)}>整理并核对资料</Button>}
          {!data.draft && data.records.length > 0 && reportDates < 2 && <Button disabled={loading || !!editing} onClick={() => onContinue(followupActions.available)}>按现有资料整理</Button>}
          <Button type="text" disabled={loading} onClick={() => onContinue(followupActions.finish)}>结束本次整理</Button>
        </footer>
      )}
    </section>
  )
}

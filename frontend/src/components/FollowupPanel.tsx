import { FileAddOutlined, FileTextOutlined } from '@ant-design/icons'
import { Alert, Button, Input, Tag } from 'antd'
import { useEffect, useRef, useState } from 'react'

import {
  followupActions,
  followupRequest,
  type ChecklistItem,
  type Evidence,
  type FollowupAction,
  type FollowupCase,
  type MedicalRecord,
  type RecordFields,
} from '../services/followupApi'
import './followup-panel.css'

const recordLabels: Record<MedicalRecord['kind'], string> = {
  report: '检查报告', visit_plan: '已有安排', self_report: '日常记录',
  purchase: '购药记录', medication_note: '用药资料', visit_summary: '门诊小结',
}
const planLabels = { unknown: '暂无完成记录', self_reported_done: '用户确认完成', documented_done: '有记录支持完成' }
const historicalFields: Record<keyof RecordFields, string> = {
  record_date: '日期', value: '数值', unit: '单位', specimen: '标本', method: '方法',
}
const subjectPattern = /^[A-Za-z0-9_-]{1,40}$/

interface Props {
  runId: string
  access: string
  canContinue: boolean
  requestId?: string
  submissionError?: string
  onContinue: (action: FollowupAction, revision?: number) => void
}

function RecordDetail({ entry }: { entry: Evidence }) {
  const { record, original, source } = entry
  const value = [record.value, record.unit].filter(Boolean).join(' ')
  const basis = [record.specimen && `标本 ${record.specimen}`, record.method && `方法 ${record.method}`].filter(Boolean).join(' · ')
  const fields = (Object.keys(historicalFields) as (keyof RecordFields)[])
    .filter(field => original && original[field] !== record[field])
  return (
    <div className="followup-record">
      <strong>{record.item}</strong>
      <small>{source}</small>
      <p>{[recordLabels[record.kind], record.record_date, value, basis].filter(Boolean).join(' · ')}</p>
      {record.reference_range && <p>原报告参考范围 {record.reference_range}</p>}
      {record.flag && <p>原报告标记 {record.flag}</p>}
      {record.note && <p>{record.note}</p>}
      {record.kind === 'purchase' && <small>此记录仅说明购买情况。</small>}
      {original && fields.length > 0 && (
        <small>导入时的记录：{fields.map(field => `${historicalFields[field]} ${original[field] ?? '未填写'}`).join(' · ')}</small>
      )}
    </div>
  )
}

function ChecklistGroup({ name, items, empty, evidence }: {
  name: string
  items: ChecklistItem[]
  empty?: string
  evidence: Map<string, Evidence>
}) {
  return (
    <section className="followup-checklist-group" aria-label={name}>
      <h4>{name}</h4>
      {!items.length && empty && <p className="followup-empty">{empty}</p>}
      <ul className="followup-list">
        {items.map(item => (
          <li key={item.id}>
            <strong>{item.title}</strong>
            {(item.due_date || item.plan_status) && (
              <p>{[item.due_date, item.plan_status && planLabels[item.plan_status]].filter(Boolean).join(' · ')}</p>
            )}
            {item.detail && <p>{item.detail}</p>}
            {item.gaps?.map(gap => <p className="followup-gap" key={gap}>{gap}</p>)}
            <details className="followup-sources">
              <summary className="workbench-disclosure">查看来源（{item.sources.length} 行）</summary>
              {item.sources.map(({ record_id, source }) => {
                const entry = evidence.get(record_id)
                return entry ? <RecordDetail key={record_id} entry={entry} /> : <small key={record_id}>{source}</small>
              })}
            </details>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ReportDetails({ draft, evidence }: {
  draft: NonNullable<FollowupCase['draft']>
  evidence: Map<string, Evidence>
}) {
  if (!draft.comparisons.length && !draft.issues.length) return null
  return (
    <details className="followup-detail">
      <summary className="workbench-disclosure">报告原值与比较依据</summary>
      <ul className="followup-list">
        {draft.comparisons.map(({ item, earlier, later, delta, unit, reasons }) => (
          <li key={later}>
            <strong>{item}</strong>
            <p>{delta !== null ? `后次 − 前次 = ${delta} ${unit}` : `未计算差值（${reasons.join('、')}）`}</p>
            {[earlier, later].map(id => {
              const entry = evidence.get(id)
              return entry && <RecordDetail key={id} entry={entry} />
            })}
          </li>
        ))}
        {draft.issues.map(({ item, text, source }, index) => (
          <li key={`${source ?? item}:${index}`}><strong>{item}</strong> {text}{source && <small>{source}</small>}</li>
        ))}
      </ul>
    </details>
  )
}

function HistoricalDraft({ draft, evidence }: {
  draft: NonNullable<FollowupCase['draft']>
  evidence: Map<string, Evidence>
}) {
  return (
    <details className="followup-detail">
      <summary className="workbench-disclosure">查看旧版资料包</summary>
      {draft.plans.map(id => {
        const entry = evidence.get(id)
        return entry && <RecordDetail key={id} entry={entry} />
      })}
      <ReportDetails draft={draft} evidence={evidence} />
    </details>
  )
}

export default function FollowupPanel({ runId, access, canContinue, requestId, submissionError, onContinue }: Props) {
  const [data, setData] = useState<FollowupCase>()
  const [subject, setSubject] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const picker = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    followupRequest(runId, access, '', { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setData(result)
    }).catch(cause => {
      if (!controller.signal.aborted) setError((cause as Error).message)
    })
    return () => controller.abort()
  }, [runId, access, requestId, attempt])

  async function update(path: string, options: RequestInit) {
    setLoading(true)
    setError('')
    try {
      setData(await followupRequest(runId, access, path, options))
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }

  if (!data) return error
    ? <Alert type="error" title={error} action={<Button onClick={() => { setError(''); setAttempt(count => count + 1) }}>重试</Button>} />
    : <p role="status">正在读取本次资料…</p>

  const { draft, records, saved } = data
  const checklist = draft?.checklist
  const revision = checklist ? draft?.revision : undefined
  const canConfirm = revision !== undefined && revision === data.revision
  const writable = canContinue && !saved
  const subjectId = data.subject_id ?? subject.trim()
  const evidence = new Map(records.map(entry => [entry.id, entry]))
  const rejected = data.files.filter(file => file.error)
  const pendingPlans = draft?.pending_plans.length ?? 0

  return (
    <section className="followup-panel" aria-label="复诊资料区" aria-busy={loading}>
      <header className="followup-header">
        <h3><FileTextOutlined aria-hidden="true" />复诊资料区</h3>
        <div>{data.demo && <Tag>合成样例</Tag>}{saved && <Tag color="success">已保存</Tag>}</div>
      </header>
      {(error || submissionError) && <Alert type="error" title={error || submissionError} showIcon />}
      {writable && (
        <div className="followup-intake">
          {loading ? <p role="status">正在读取资料…</p> : (
            <p role="status">{records.length
              ? `已读取 ${records.length} 条记录。${checklist ? '' : '可以整理复诊前准备清单。'}`
              : data.files.length ? '请导入一份可读取的资料。' : '先导入一份可读取的资料，或载入合成样例。'}</p>
          )}
          {!data.demo && (
            <>
              <div className="followup-buttons">
                {!data.subject_id && <Input value={subject} onChange={event => setSubject(event.target.value)} aria-label="匿名对象编号" placeholder="匿名编号，例如 CASE-01" maxLength={40} />}
                <input ref={picker} type="file" hidden accept=".csv,.json,.pdf" onChange={event => {
                  const file = event.target.files?.item(0)
                  event.target.value = ''
                  if (!file) return
                  void update('/materials', {
                    method: 'POST', headers: { 'X-Followup-Subject': subjectId, 'X-Followup-File': encodeURIComponent(file.name) }, body: file,
                  })
                }} />
                <Button icon={<FileAddOutlined aria-hidden="true" />} disabled={loading || !subjectPattern.test(subjectId)}
                  onClick={() => picker.current?.click()}>导入资料</Button>
                {data.files.length === 0 && (
                  <Button disabled={loading} onClick={() => void update('/demo-materials', { method: 'POST' })}>载入合成样例</Button>
                )}
              </div>
              <small>{data.subject_id ? `对象 ${data.subject_id}。` : '匿名编号需与文件内的 subject_id 一致。'}支持 CSV、JSON 和模板文字 PDF。</small>
            </>
          )}
          {rejected.length > 0 && (
            <ul className="followup-list" aria-label="未能读取的资料">
              {rejected.map(file => <li key={file.digest}><strong>{file.name}</strong><p>{file.error}</p></li>)}
            </ul>
          )}
          {!canConfirm && (
            <div className="followup-buttons">
              {records.length > 0 && (
                <Button type="primary" disabled={loading} onClick={() => onContinue(followupActions.prepare)}>整理复诊前准备清单</Button>
              )}
              <Button type="text" disabled={loading} onClick={() => onContinue(followupActions.finish)}>结束本次整理</Button>
            </div>
          )}
        </div>
      )}
      {checklist && (
        <div className="followup-checklist" aria-label="复诊前准备清单">
          <ChecklistGroup name="要带的资料" items={checklist.materials_to_bring} evidence={evidence} />
          <ChecklistGroup name="已有安排" items={checklist.existing_arrangements} evidence={evidence}
            empty={data.demo ? '此样例没有预约记录。' : '当前资料没有预约记录。可以导入预约单补充。'} />
          <ChecklistGroup name="要问医生的问题" items={checklist.questions_for_doctor} evidence={evidence}
            empty="可以把复诊时想问的问题记在日常记录里。" />
        </div>
      )}
      {writable && canConfirm && (
        <div className="followup-confirm">
          {pendingPlans > 0 && <p>确认后保存这版资料包，并将已有安排中的 {pendingPlans} 条待办一同保存。</p>}
          <div className="followup-buttons">
            <Button type="primary" disabled={loading} onClick={() => onContinue(followupActions.confirm, revision)}>确认保存资料包</Button>
            <Button type="text" disabled={loading} onClick={() => onContinue(followupActions.finish)}>结束本次整理</Button>
          </div>
        </div>
      )}
      {saved && (
        <div className="followup-saved" role="status">
          <strong>已保存资料包{saved.tasks.length > 0 && `和 ${saved.tasks.length} 条待办`}</strong>
          {saved.tasks.length > 0 && (
            <ul className="followup-list">
              {saved.tasks.map(task => <li key={task.id}>{task.title}{task.due_date && ` · ${task.due_date}`}<small>{task.source}</small></li>)}
            </ul>
          )}
        </div>
      )}
      {draft && (checklist ? <ReportDetails draft={draft} evidence={evidence} /> : <HistoricalDraft draft={draft} evidence={evidence} />)}
      {records.length > 0 && (
        <details className="followup-detail">
          <summary className="workbench-disclosure">资料记录（{records.length} 条）</summary>
          {records.map(entry => <RecordDetail key={entry.id} entry={entry} />)}
        </details>
      )}
    </section>
  )
}

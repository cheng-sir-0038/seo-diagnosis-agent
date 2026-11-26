import {
  BookOutlined, DownloadOutlined, FileTextOutlined, PaperClipOutlined, SearchOutlined, UploadOutlined,
} from '@ant-design/icons'
import { Alert, Button, Input, Popconfirm, Tag } from 'antd'
import { useEffect, useRef, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { deleteLibraryDocument, downloadDocument, listLibrary, uploadDocument, WORKSPACE_FILE_ACCEPT } from '../services/workspaceApi'
import type { DocumentSummary, WorkspaceLibrary } from '../types/workspace'

const dateLabel = new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', year: 'numeric' })
const integer = new Intl.NumberFormat('zh-CN')

export default function KnowledgePage({ onSelect, onDelete, picker = false, selectedIds = [] }: {
  onSelect: (document: DocumentSummary) => void
  onDelete?: (document: DocumentSummary) => void
  picker?: boolean
  selectedIds?: string[]
}) {
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [request, setRequest] = useState(0)
  const [data, setData] = useState<{ request: number, library?: WorkspaceLibrary, error?: string }>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const inputFile = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    listLibrary(query, !picker, controller.signal).then(library => {
      if (!controller.signal.aborted) setData({ request, library })
    }).catch((cause: Error) => {
      if (!controller.signal.aborted) setData({ request, error: cause.message })
    })
    return () => controller.abort()
  }, [query, request, picker])

  const refresh = () => setRequest(number => number + 1)
  const importFile = async (file: File) => {
    setError(undefined)
    setBusy(true)
    try {
      await uploadDocument(file)
      setSearch('')
      setQuery('')
      refresh()
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (document: DocumentSummary) => {
    setError(undefined)
    try {
      await deleteLibraryDocument(document.id)
      onDelete?.(document)
      refresh()
    } catch (cause) {
      setError((cause as Error).message)
    }
  }

  const loading = data?.request !== request
  const items = data?.library?.documents ?? []
  const references = data?.library?.references ?? []
  const list = (
    <section className={`workbench-panel workspace-library${picker ? ' workspace-library-picker' : ''}`} aria-label="我的资料">
      <div className="workbench-panel-header">
        <h2><BookOutlined aria-hidden="true" />我的资料</h2>
        {!picker && (
          <>
            <input ref={inputFile} hidden type="file" accept={WORKSPACE_FILE_ACCEPT} onChange={event => {
              const file = event.target.files?.item(0)
              event.target.value = ''
              if (file) void importFile(file)
            }} />
            <Button type="primary" icon={<UploadOutlined aria-hidden="true" />} loading={busy} onClick={() => inputFile.current?.click()}>上传资料</Button>
          </>
        )}
      </div>
      {!picker && <p className="workspace-resource-hint">支持 TXT、Markdown、CSV、JSON 和文字 PDF，单份最多 1 MB。</p>}
      <form className="workspace-library-search" onSubmit={event => {
        event.preventDefault()
        setQuery(search.trim())
        refresh()
      }}>
        <Input value={search} onChange={event => setSearch(event.target.value)} maxLength={160} allowClear
          prefix={<SearchOutlined aria-hidden="true" />} aria-label="搜索知识库" placeholder="搜索文件名、正文或资料出处…" />
        <Button aria-label="搜索" htmlType="submit">搜索</Button>
      </form>
      {error && <Alert type="error" title={error} showIcon />}
      {data?.error && <Alert type="error" title={data.error} showIcon action={<Button size="small" onClick={refresh}>重试</Button>} />}
      {loading ? <p role="status" className="workbench-empty">正在读取资料…</p> : items.length ? (
        <ul className="workspace-document-list">
          {items.map(document => (
            <li key={document.id}>
              <span className="workspace-row-icon"><FileTextOutlined aria-hidden="true" /></span>
              <div className="workspace-document-copy">
                <strong title={document.name}>{document.name}</strong>
                <small>{integer.format(document.text_chars)} 字 · {dateLabel.format(new Date(document.uploaded_at))}</small>
                {document.excerpt && (
                  <p><small>{document.excerpt.location}</small> · {document.excerpt.text}</p>
                )}
              </div>
              <div className="workspace-document-actions">
                <Button size="small" icon={<PaperClipOutlined aria-hidden="true" />} disabled={selectedIds.includes(document.id)} onClick={() => onSelect(document)}>
                  {selectedIds.includes(document.id) ? '已选中' : picker ? '选用' : '带到新对话'}
                </Button>
                {!picker && (
                  <>
                    <Button size="small" type="text" aria-label={`下载 ${document.name}`} icon={<DownloadOutlined />}
                      onClick={() => void downloadDocument(document).catch((cause: Error) => setError(cause.message))} />
                    <Popconfirm title="从知识库删除这份资料？" okText="删除" cancelText="取消" onConfirm={() => remove(document)}>
                      <Button size="small" type="text" danger>删除</Button>
                    </Popconfirm>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      ) : !data?.error && (query || picker ? (
        <p className="workbench-empty">{query ? '没有匹配的上传资料。' : '这里还没有上传资料。'}</p>
      ) : (
        <div className="workspace-library-empty">
          <img src={`${import.meta.env.BASE_URL}workbench-knowledge-empty.webp`} alt="" width={600} height={400} />
          <p>这里还没有上传资料。</p>
        </div>
      ))}
    </section>
  )

  if (picker) return list
  return (
    <div className="page-content workspace-resources">
      <PageHeader title="知识库" description="上传和搜索自己的资料，或查看专员已收录的公开来源。" />
      <div className="workspace-library-layout">
        {list}
        <section className="workbench-panel workspace-reference-list" aria-label="内置公开资料">
          <h2><BookOutlined aria-hidden="true" />内置公开资料</h2>
          <p className="workspace-resource-hint">已收录院校招生目录的原文摘录。</p>
          {!loading && references.length ? (
            <ul>{references.map(reference => (
              <li key={`${reference.source_url}:${reference.section}`}>
                <a href={reference.source_url} target="_blank" rel="noreferrer">{reference.title}</a>
                <Tag>{reference.section}</Tag>
                <p>{reference.quote}</p>
              </li>
            ))}</ul>
          ) : <p className="workbench-empty">{loading ? '正在读取公开资料…' : '没有匹配的公开摘录。'}</p>}
        </section>
      </div>
    </div>
  )
}

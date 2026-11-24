import {
  CloseOutlined, CloudUploadOutlined, DownloadOutlined, PaperClipOutlined,
} from '@ant-design/icons'
import { Button, Tooltip } from 'antd'
import { useRef } from 'react'

import { WORKSPACE_FILE_ACCEPT } from '../services/workspaceApi'
import type { DocumentSummary } from '../types/workspace'

export function ChatAttachmentControls({ busy, onUpload, onLibrary }: {
  busy: boolean
  onUpload: (files: File[]) => void
  onLibrary: () => void
}) {
  const fileInput = useRef<HTMLInputElement>(null)
  return (
    <div className="workspace-attachment-controls">
      <input ref={fileInput} type="file" hidden multiple accept={WORKSPACE_FILE_ACCEPT} onChange={event => {
        const files = Array.from(event.target.files ?? [])
        event.target.value = ''
        if (files.length) onUpload(files)
      }} />
      <Tooltip title="上传附件">
        <Button type="text" aria-label="上传附件" icon={<PaperClipOutlined />} loading={busy} onClick={() => fileInput.current?.click()} />
      </Tooltip>
      <Tooltip title="从知识库选附件">
        <Button type="text" aria-label="从知识库选附件" icon={<CloudUploadOutlined />} disabled={busy} onClick={onLibrary} />
      </Tooltip>
    </div>
  )
}

export function DocumentChips({ documents, onDownload, onRemove, onReuse }: {
  documents: DocumentSummary[]
  onDownload: (document: DocumentSummary) => void
  onRemove?: (id: string) => void
  onReuse?: (document: DocumentSummary) => void
}) {
  return (
    <ul className="workspace-document-chips" aria-label={onRemove ? '待发送附件' : '本条消息的附件'}>
      {documents.map(document => (
        <li key={document.id}>
          <button type="button" title={document.name} onClick={() => onDownload(document)}>
            {onRemove ? <PaperClipOutlined aria-hidden="true" /> : <DownloadOutlined aria-hidden="true" />}
            <span>{document.name}</span>
          </button>
          {onRemove && (
            <Button type="text" size="small" aria-label={`移除 ${document.name}`} icon={<CloseOutlined />} onClick={() => onRemove(document.id)} />
          )}
          {onReuse && (
            <Button type="text" size="small" aria-label={`再次引用 ${document.name}`} onClick={() => onReuse(document)}>再次引用</Button>
          )}
        </li>
      ))}
    </ul>
  )
}

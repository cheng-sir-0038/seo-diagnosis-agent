import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Drawer, Empty, Form, Input, InputNumber, message, Modal, Popconfirm, Select, Table, Tabs } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { productApi } from '../services/productApi'
import type {
  EvaluationRun,
  PromptDataset,
  PromptDefinition,
  PromptDeployment,
  PromptExperiment,
  PromptVersion,
} from '../types/product'

interface VersionFormValues {
  systemTemplate: string
  userTemplate: string
  outputSchema: string
  changeSummary?: string
  parentVersionId?: string
}

export function PromptOpsPage() {
  const [prompts, setPrompts] = useState<PromptDefinition[]>([])
  const [datasets, setDatasets] = useState<PromptDataset[]>([])
  const [evaluations, setEvaluations] = useState<EvaluationRun[]>([])
  const [experiments, setExperiments] = useState<PromptExperiment[]>([])
  const [selectedPrompt, setSelectedPrompt] = useState<PromptDefinition | null>(null)
  const [versions, setVersions] = useState<PromptVersion[]>([])
  const [deployments, setDeployments] = useState<PromptDeployment[]>([])
  const [promptModal, setPromptModal] = useState(false)
  const [versionModal, setVersionModal] = useState(false)
  const [datasetModal, setDatasetModal] = useState(false)
  const [canaryVersion, setCanaryVersion] = useState<PromptVersion | null>(null)
  const [canaryPercent, setCanaryPercent] = useState(10)
  const [optimizeModal, setOptimizeModal] = useState(false)
  const [loading, setLoading] = useState(true)
  const [promptForm] = Form.useForm()
  const [versionForm] = Form.useForm<VersionFormValues>()
  const [datasetForm] = Form.useForm()
  const [optimizeForm] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [nextPrompts, nextDatasets, nextEvaluations, nextExperiments] = await Promise.all([
        productApi.listPrompts(),
        productApi.listPromptDatasets(),
        productApi.listEvaluationRuns(),
        productApi.listPromptExperiments(),
      ])
      setPrompts(nextPrompts)
      setDatasets(nextDatasets)
      setEvaluations(nextEvaluations)
      setExperiments(nextExperiments)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const openPrompt = async (prompt: PromptDefinition) => {
    const [nextVersions, nextDeployments] = await Promise.all([
      productApi.listPromptVersions(prompt.id),
      productApi.listPromptDeployments(prompt.id),
    ])
    setSelectedPrompt(prompt)
    setVersions(nextVersions)
    setDeployments(nextDeployments)
  }

  const refreshSelected = async () => {
    if (!selectedPrompt) return
    await openPrompt(selectedPrompt)
    await load()
  }

  const createPrompt = async (values: { promptKey: string; name: string; purpose: string }) => {
    await productApi.createPrompt({
      prompt_key: values.promptKey,
      name: values.name,
      purpose: values.purpose,
    })
    message.success('Prompt 已创建')
    setPromptModal(false)
    promptForm.resetFields()
    await load()
  }

  const createVersion = async (values: VersionFormValues) => {
    if (!selectedPrompt) return
    let outputSchema: Record<string, unknown>
    try {
      outputSchema = JSON.parse(values.outputSchema) as Record<string, unknown>
    } catch {
      message.error('输出 Schema 必须是有效 JSON')
      return
    }
    await productApi.createPromptVersion(selectedPrompt.id, {
      system_template: values.systemTemplate,
      user_template: values.userTemplate,
      output_schema: outputSchema,
      stage: 'CHALLENGER',
      parent_version_id: values.parentVersionId,
      change_summary: values.changeSummary,
      created_by: 'product-workbench',
    })
    message.success('Challenger 版本已创建')
    setVersionModal(false)
    versionForm.resetFields()
    await refreshSelected()
  }

  const createDataset = async (values: { name: string; description?: string }) => {
    await productApi.createPromptDataset({
      name: values.name,
      description: values.description,
      prompt_id: selectedPrompt?.id,
    })
    message.success('评测数据集已创建')
    setDatasetModal(false)
    datasetForm.resetFields()
    await load()
  }

  const startCanary = async () => {
    if (!selectedPrompt || !canaryVersion) return
    await productApi.startPromptCanary(selectedPrompt.id, canaryVersion.id, canaryPercent)
    message.success(`已启动 ${canaryPercent}% 灰度`)
    setCanaryVersion(null)
    await refreshSelected()
  }

  const promote = async (version: PromptVersion) => {
    if (!selectedPrompt) return
    await productApi.promotePromptVersion(selectedPrompt.id, version.id, '工作台显式升级')
    message.success(`${version.version_label} 已成为 CURRENT`)
    await refreshSelected()
  }

  const rollback = async (version: PromptVersion) => {
    if (!selectedPrompt) return
    await productApi.rollbackPromptVersion(selectedPrompt.id, version.id, '工作台显式回滚')
    message.success(`已回滚到 ${version.version_label}`)
    await refreshSelected()
  }

  const optimize = async (values: {
    datasetId: string
    failures: string
    successCriteria: string
    canaryPercentage: number
  }) => {
    if (!selectedPrompt) return
    let failures: Record<string, unknown>[]
    let successCriteria: Record<string, unknown>
    try {
      failures = JSON.parse(values.failures) as Record<string, unknown>[]
      successCriteria = JSON.parse(values.successCriteria) as Record<string, unknown>
    } catch {
      message.error('失败样本和成功门槛必须是有效 JSON')
      return
    }
    const result = await productApi.optimizePrompt(selectedPrompt.id, {
      dataset_id: values.datasetId,
      failures,
      success_criteria: successCriteria,
      canary_percentage: values.canaryPercentage,
    })
    message.success(`自动优化完成：${result.recommendation}`)
    setOptimizeModal(false)
    optimizeForm.resetFields()
    await refreshSelected()
  }

  const deployment = deployments.find((item) => item.environment === 'production')
  const currentVersionId = deployment?.current_version_id
  const promptColumns: ColumnsType<PromptDefinition> = [
    {
      title: 'Prompt',
      dataIndex: 'name',
      render: (_, prompt) => (
        <button className="link-button" onClick={() => void openPrompt(prompt)}>
          <strong>{prompt.name}</strong><small>{prompt.prompt_key}</small>
        </button>
      ),
    },
    { title: '用途', dataIndex: 'purpose' },
    { title: '状态', dataIndex: 'status', width: 120, render: (value) => <StatusPill value={value} /> },
    { title: '创建时间', dataIndex: 'created_at', width: 190, render: (value) => new Date(value).toLocaleString() },
  ]

  const versionColumns: ColumnsType<PromptVersion> = [
    { title: '版本', dataIndex: 'version_label', width: 100 },
    { title: '阶段', dataIndex: 'stage', width: 130, render: (value) => <StatusPill value={value} /> },
    { title: '变更', dataIndex: 'change_summary', render: (value) => value ?? '—' },
    { title: '哈希', dataIndex: 'content_hash', width: 130, render: (value) => String(value).slice(0, 10) },
    {
      title: '操作',
      width: 250,
      render: (_, version) => (
        <div className="table-actions">
          {version.id !== currentVersionId ? <Button size="small" onClick={() => setCanaryVersion(version)}>灰度</Button> : null}
          {version.id !== currentVersionId ? <Popconfirm title="确认升级为 CURRENT？" onConfirm={() => void promote(version)}><Button size="small" type="primary">升级</Button></Popconfirm> : null}
          {version.id !== currentVersionId ? <Popconfirm title="确认回滚到该版本？" onConfirm={() => void rollback(version)}><Button size="small">回滚</Button></Popconfirm> : <StatusPill value="CURRENT" />}
        </div>
      ),
    },
  ]

  const evaluationColumns: ColumnsType<EvaluationRun> = [
    { title: '评测运行', dataIndex: 'id', render: (value) => String(value).slice(0, 18) },
    { title: '状态', dataIndex: 'status', width: 120, render: (value) => <StatusPill value={value} /> },
    { title: '通过率', dataIndex: 'pass_rate', width: 110, render: (value) => `${(Number(value) * 100).toFixed(1)}%` },
    { title: '平均分', dataIndex: 'average_score', width: 110, render: (value) => Number(value).toFixed(3) },
    { title: '成本', dataIndex: 'total_cost', width: 100, render: (value) => Number(value).toFixed(4) },
    { title: '延迟', dataIndex: 'average_latency_ms', width: 120, render: (value) => `${Number(value).toFixed(1)} ms` },
  ]

  const experimentColumns: ColumnsType<PromptExperiment> = [
    { title: '实验', dataIndex: 'id', render: (value) => String(value).slice(0, 18) },
    { title: '状态', dataIndex: 'status', width: 120, render: (value) => <StatusPill value={value} /> },
    { title: '建议', dataIndex: 'recommendation', width: 130, render: (value) => value ? <StatusPill value={value} /> : '—' },
    { title: '基线', dataIndex: 'baseline_version_id', render: (value) => String(value).slice(0, 14) },
    { title: '挑战者', dataIndex: 'challenger_version_id', render: (value) => String(value).slice(0, 14) },
  ]

  const promptStats = useMemo(
    () => ({
      current: prompts.length,
      datasets: datasets.length,
      evaluations: evaluations.length,
      experiments: experiments.length,
    }),
    [prompts, datasets, evaluations, experiments],
  )

  return (
    <div className="page-content">
      <PageHeader
        title="提示词实验"
        description="版本化 Prompt、固定数据集重放、Challenger、Canary、显式升级与回滚。"
        actions={<div className="table-actions"><Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => setPromptModal(true)}>创建 Prompt</Button></div>}
      />
      <section className="metric-grid observability-metrics">
        {[
          ['Prompt', promptStats.current, '独立提示词定义'],
          ['数据集', promptStats.datasets, '固定回归样本'],
          ['评测运行', promptStats.evaluations, '可比较结果'],
          ['实验', promptStats.experiments, '基线与挑战者'],
        ].map(([label, value, help]) => <article className="metric-card" key={String(label)}><span>{label}</span><strong>{value}</strong><small>{help}</small></article>)}
      </section>
      <section className="panel-card table-card">
        <Tabs items={[
          { key: 'prompts', label: `Prompt ${prompts.length}`, children: <Table rowKey="id" columns={promptColumns} dataSource={prompts} loading={loading} pagination={false} locale={{ emptyText: <Empty description="暂无 Prompt" /> }} /> },
          { key: 'datasets', label: `数据集 ${datasets.length}`, children: <><div className="tab-action-row"><Button icon={<PlusOutlined />} onClick={() => setDatasetModal(true)}>创建数据集</Button></div><Table rowKey="id" dataSource={datasets} pagination={false} columns={[{ title: '名称', dataIndex: 'name' }, { title: '描述', dataIndex: 'description', render: (value) => value ?? '—' }, { title: '状态', dataIndex: 'status', width: 120, render: (value) => <StatusPill value={value} /> }]} /></> },
          { key: 'evaluations', label: `评测 ${evaluations.length}`, children: <Table rowKey="id" columns={evaluationColumns} dataSource={evaluations} pagination={{ pageSize: 20 }} locale={{ emptyText: <Empty description="暂无评测运行" /> }} /> },
          { key: 'experiments', label: `实验 ${experiments.length}`, children: <Table rowKey="id" columns={experimentColumns} dataSource={experiments} pagination={{ pageSize: 20 }} locale={{ emptyText: <Empty description="暂无实验" /> }} /> },
        ]} />
      </section>

      <Drawer
        title={selectedPrompt?.name ?? 'Prompt 详情'}
        size="large"
        open={Boolean(selectedPrompt)}
        onClose={() => setSelectedPrompt(null)}
        extra={<div className="table-actions">
          <Button onClick={() => { optimizeForm.setFieldsValue({ failures: '[\n  {"issue": "missing evidence"}\n]', successCriteria: '{\n  "minimum_pass_rate": 0.8\n}', canaryPercentage: 10 }); setOptimizeModal(true) }}>自动优化</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { versionForm.setFieldsValue({ userTemplate: '{{input}}', outputSchema: '{\n  "required": []\n}', parentVersionId: currentVersionId ?? undefined }); setVersionModal(true) }}>创建 Challenger</Button>
        </div>}
      >
        {selectedPrompt ? <div className="detail-stack">
          <div className="detail-toolbar"><span>{selectedPrompt.prompt_key}</span><StatusPill value={selectedPrompt.status} />{deployment ? <span>生产：{deployment.current_version_id?.slice(0, 14) ?? '未部署'} · Canary {deployment.canary_percentage}%</span> : <span>尚未部署</span>}</div>
          <Table rowKey="id" columns={versionColumns} dataSource={versions} pagination={false} />
          <section><h3>部署</h3><pre className="json-preview">{JSON.stringify(deployments, null, 2)}</pre></section>
        </div> : null}
      </Drawer>

      <Modal title="创建 Prompt" open={promptModal} onCancel={() => setPromptModal(false)} onOk={() => promptForm.submit()}>
        <Form form={promptForm} layout="vertical" onFinish={(values) => void createPrompt(values)}>
          <Form.Item label="Prompt Key" name="promptKey" rules={[{ required: true }]}><Input placeholder="seo.opportunity.classify" /></Form.Item>
          <Form.Item label="名称" name="name" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="用途" name="purpose" rules={[{ required: true }]}><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="创建 Challenger 版本" open={versionModal} onCancel={() => setVersionModal(false)} onOk={() => versionForm.submit()} width={760}>
        <Form form={versionForm} layout="vertical" onFinish={(values) => void createVersion(values)}>
          <Form.Item label="系统提示词" name="systemTemplate" rules={[{ required: true }]}><Input.TextArea rows={8} /></Form.Item>
          <Form.Item label="用户模板" name="userTemplate" rules={[{ required: true }]}><Input.TextArea rows={3} /></Form.Item>
          <Form.Item label="输出 Schema JSON" name="outputSchema" rules={[{ required: true }]}><Input.TextArea rows={6} /></Form.Item>
          <Form.Item label="父版本" name="parentVersionId"><Select allowClear options={versions.map((version) => ({ value: version.id, label: version.version_label }))} /></Form.Item>
          <Form.Item label="变更摘要" name="changeSummary"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="创建评测数据集" open={datasetModal} onCancel={() => setDatasetModal(false)} onOk={() => datasetForm.submit()}>
        <Form form={datasetForm} layout="vertical" onFinish={(values) => void createDataset(values)}>
          <Form.Item label="名称" name="name" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="描述" name="description"><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="启动 Canary" open={Boolean(canaryVersion)} onCancel={() => setCanaryVersion(null)} onOk={() => void startCanary()}>
        <p>{canaryVersion?.version_label} 将作为 Challenger 灰度运行，CURRENT 不会被覆盖。</p>
        <InputNumber min={1} max={100} value={canaryPercent} onChange={(value) => setCanaryPercent(value ?? 10)} addonAfter="%" />
      </Modal>

      <Modal title="自动生成 Challenger 并评测" open={optimizeModal} onCancel={() => setOptimizeModal(false)} onOk={() => optimizeForm.submit()} width={760}>
        <Form form={optimizeForm} layout="vertical" onFinish={(values) => void optimize(values)}>
          <Form.Item label="固定评测数据集" name="datasetId" rules={[{ required: true }]}>
            <Select options={datasets.filter((dataset) => !dataset.prompt_id || dataset.prompt_id === selectedPrompt?.id).map((dataset) => ({ value: dataset.id, label: dataset.name }))} />
          </Form.Item>
          <Form.Item label="失败样本 JSON" name="failures" rules={[{ required: true }]}><Input.TextArea rows={6} /></Form.Item>
          <Form.Item label="成功门槛 JSON" name="successCriteria" rules={[{ required: true }]}><Input.TextArea rows={6} /></Form.Item>
          <Form.Item label="通过后 Canary 比例" name="canaryPercentage" rules={[{ required: true }]}><InputNumber min={1} max={100} addonAfter="%" /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

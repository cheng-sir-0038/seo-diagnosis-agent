import type { ToolRun } from '../types/agent'
import { isPresentationBase, resolveLocalizedText, unwrapToolResult } from './renderers/contracts'

const labels: Record<string, string> = {
  pharmacy_coupon_rules: '核对会员券规则',
  pharmacy_reminder_candidates: '筛选提醒名单',
  medical_exam_registration: '核对医考报名条件',
  medical_exam_syllabus: '查证考纲条款',
  medicare_drug_catalogue: '查询医保药品目录',
  pubmed_literature_search: '检索 PubMed 题录',
  postgraduate_admissions_search: '检索招生目录',
  tal_math_practice: '数学练习',
  math_resource_lookup: '查询官方学习资料',
  ntce_written_exam_lookup: '核对教资笔试公告',
  followup_read_materials: '读取资料回执',
  followup_prepare_materials: '核对报告并整理资料包',
  followup_save_confirmed: '保存已确认资料和待办',
  travel_compare_plans: '比对交通与住宿',
  travel_weather: '查目的地天气和出行建议',
  travel_save_plan: '保存所选出差方案',
  shopping_catalog: '查商品资料',
  shopping_compare_quotes: '核算同规格报价',
  shopping_build_list: '整理采购清单',
  shopping_reconcile: '核对订单和到货凭证',
  seo_page_metadata: '读取网页内容',
  seo_page_performance: '检查网页性能',
  seo_search_metrics: '查询站点指标',
  seo_organic_keywords: '查询自然排名',
  seo_serp_overview: '核对搜索结果',
  seo_keyword_overview: '查询关键词指标',
  seo_autocomplete: '查询搜索联想词',
  seo_news_search: '搜索新闻',
  seo_image_search: '搜索图片',
  seo_video_search: '搜索视频',
  seo_organic_competitors: '查询自然搜索竞品',
  seo_backlinks_list: '查询外链',
  seo_geo_questions: '发现 GEO 问题',
  seo_geo_citations: '核验 GEO 引用',
  seo_geo_content: '拟定 GEO 改稿',
  seo_keyword_opportunity: '研究关键词机会',
  seo_site_audit: '检查单页 SEO',
  seo_site_diagnosis: '诊断站点',
  seo_page_suggestions: '拟定页面建议',
  seo_search_diagnosis: '诊断搜索表现',
  seo_page_change_review: '复检页面变更',
  seo_ranking_page_optimization: '优化已有排名页',
  seo_semrush_competition: '分析月度搜索竞品',
  seo_share_image: '生成分享图',
}

export const toolLabel = (id: string) => labels[id] ?? id

const healthcareDescriptions: Record<string, string> = {
  medicare_drug_catalogue: '按通用名查询国家医保局目录中的甲乙分类、剂型与原文页码。',
  pubmed_literature_search: '按主题和发表时间检索 PubMed，返回 PMID、期刊和原文入口。',
  medical_exam_registration: '根据年份、考区和考生条件核对报名及免考要求。',
  medical_exam_syllabus: '核对医学人文考纲的修订年份、适用考试和公告原文。',
  postgraduate_admissions_search: '按院校、招生年份和专业检索目录，返回命中段落及官方出处。',
}

export const toolDescription = (id: string, description: string) => healthcareDescriptions[id] ?? description

export function toolResultLabel(tool: ToolRun): string {
  const result = unwrapToolResult(tool.result)
  return isPresentationBase(result) ? resolveLocalizedText(result.title, toolLabel(tool.name)) : toolLabel(tool.name)
}

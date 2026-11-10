from src.agent.harness.store import AgentChatHarnessStore
from src.agent.run_manager import run_manager
from src.core.config import settings
from src.facade.data_facade import facade
from src.observability.runtime import observability_client
from src.promptops.optimization import PromptOptimizationLoop
from src.promptops.runtime import (
    prompt_evaluation_service,
    prompt_experiment_service,
    prompt_registry,
)
from src.seo_agent.adapters.registry import AdapterDefinition, AdapterRegistry
from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.automation.digests import DigestService
from src.seo_agent.automation.lifecycle import AutomationLifecycle
from src.seo_agent.automation.outbox import NotificationOutboxWorker
from src.seo_agent.automation.prompt_optimization import (
    DeepSeekCandidateGenerator,
    DeepSeekPromptExecutor,
)
from src.seo_agent.automation.prompts import SeoPromptSeeder
from src.seo_agent.automation.schedules import (
    DigestScheduleCoordinator,
    ScheduleAutomationCoordinator,
)
from src.seo_agent.automation.structured import StructuredModelAutomation
from src.seo_agent.chat.service import SeoChatRecoveryService
from src.seo_agent.checkpoints import CheckpointManager
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.models.deepseek import LangChainDeepSeekFactory
from src.seo_agent.monitoring.execution import MonitoringExecutionService
from src.seo_agent.monitoring.service import MonitoringService
from src.seo_agent.notifications.email import SmtpEmailProvider, SmtpSettings
from src.seo_agent.notifications.reminders import ApprovalReminderCoordinator
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.opportunities.service import OpportunityService, ScanRunService
from src.seo_agent.publishing.html_site import HtmlSitePublishAdapter, HtmlSiteSettings
from src.seo_agent.publishing.service import PublishingService
from src.seo_agent.schedules.provider import LocalScheduleProvider
from src.seo_agent.schedules.service import WorkScheduleService
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.service import TaskDeliveryService
from src.seo_agent.tools.seo_data import SeoDataToolBridge
from src.seo_agent.workbench.service import WorkbenchService

seo_agent_database = SeoAgentDatabase(settings.seo_agent_sqlite_path)
checkpoint_manager = CheckpointManager(settings.langgraph_checkpoint_sqlite_path)
seo_chat_harness_store = AgentChatHarnessStore(seo_agent_database)
seo_chat_recovery_service = SeoChatRecoveryService(seo_chat_harness_store, run_manager)
adapter_registry = AdapterRegistry()

adapter_registry.register_definition(
    AdapterDefinition(category="email_provider", key="smtp", display_name="SMTP", implemented=True)
)
smtp_email_provider = SmtpEmailProvider(
    SmtpSettings(
        host=settings.smtp_host,
        port=settings.smtp_port,
        security=settings.smtp_security,
        username=settings.smtp_username,
        auth_code=settings.smtp_auth_code,
        from_address=settings.email_from_address,
        from_name=settings.email_from_name,
    ),
    observability=observability_client,
)
adapter_registry.register(smtp_email_provider)

seo_agent_system_service = SeoAgentSystemService(
    seo_agent_database,
    graph_version=settings.seo_agent_graph_version,
    policy_version=settings.seo_agent_policy_version,
)
approval_service = ApprovalService(
    seo_agent_database, reminder_hours=settings.approval_reminder_interval_hours
)
notification_service = NotificationService(seo_agent_database, smtp_email_provider)
work_schedule_service = WorkScheduleService(seo_agent_database)
local_schedule_provider = LocalScheduleProvider()
approval_reminder_coordinator = ApprovalReminderCoordinator(approval_service, notification_service)

task_delivery_service = TaskDeliveryService(seo_agent_database, notifications=notification_service)

monitoring_service = MonitoringService(
    seo_agent_database,
    indexing_days=tuple(
        int(value.strip())
        for value in settings.monitoring_indexing_days.split(",")
        if value.strip()
    ),
    ranking_weeks=settings.monitoring_ranking_weeks,
    policy_version=settings.seo_agent_policy_version,
)
monitoring_execution_service = MonitoringExecutionService(
    seo_agent_database, monitoring_service, observability_client
)
html_site_publish_adapter = HtmlSitePublishAdapter(
    HtmlSiteSettings(
        root_path=settings.html_site_root_path,
        base_url=settings.html_site_base_url,
        content_subdir=settings.html_site_content_subdir,
        request_timeout=settings.html_site_request_timeout,
    ),
    observability=observability_client,
)
publishing_service = PublishingService(
    seo_agent_database,
    html_site_publish_adapter,
    monitoring_service,
    observability=observability_client,
)

scan_run_service = ScanRunService(seo_agent_database)
opportunity_service = OpportunityService(
    seo_agent_database, approvals=approval_service, notifications=notification_service
)
workbench_service = WorkbenchService(seo_agent_database)
seo_data_tool_bridge = SeoDataToolBridge(facade, observability=observability_client)


def build_structured_model():
    return LangChainDeepSeekFactory(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout=settings.deepseek_request_timeout,
    ).create(temperature=0.0)


seo_prompt_seeder = SeoPromptSeeder(prompt_registry)
structured_model_automation = StructuredModelAutomation(
    seo_agent_database,
    task_delivery_service,
    model_factory=build_structured_model,
    prompt_registry=prompt_registry,
    observability=observability_client,
)
notification_outbox_worker = NotificationOutboxWorker(
    seo_agent_database,
    notification_service,
    smtp_email_provider,
    max_attempts=settings.notification_max_attempts,
)
digest_service = DigestService(seo_agent_database, notification_service)
schedule_automation_coordinator = ScheduleAutomationCoordinator(
    seo_agent_database,
    local_schedule_provider,
    scan_run_service,
    seo_agent_system_service,
    observability=observability_client,
)
digest_schedule_coordinator = DigestScheduleCoordinator(
    seo_agent_database,
    local_schedule_provider,
    digest_service,
    observability=observability_client,
)

prompt_candidate_generator = DeepSeekCandidateGenerator(
    model_factory=build_structured_model,
    prompt_registry=prompt_registry,
    observability=observability_client,
)
prompt_evaluation_executor = DeepSeekPromptExecutor(
    model_factory=build_structured_model,
    observability=observability_client,
)
prompt_optimization_loop = PromptOptimizationLoop(
    database=observability_client.database,
    registry=prompt_registry,
    evaluator=prompt_evaluation_service,
    experiments=prompt_experiment_service,
    candidate_generator=prompt_candidate_generator,
)

automation_lifecycle = AutomationLifecycle(
    local_schedule_provider,
    notification_outbox_worker,
    schedule_automation_coordinator,
    digest_schedule_coordinator,
    outbox_interval_minutes=settings.notification_outbox_interval_minutes,
    sync_interval_minutes=settings.automation_schedule_sync_minutes,
    observability=observability_client,
)

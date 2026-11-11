"""保留原导入路径，业务实现位于 workflows/task_delivery.py。"""

from src.seo_agent.workflows.task_delivery import StructuredModel, TaskDeliveryWorkflows

StructuredModelAutomation = TaskDeliveryWorkflows

__all__ = ["StructuredModel", "StructuredModelAutomation"]

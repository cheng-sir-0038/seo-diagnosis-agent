from src.observability.runtime import observability_database
from src.promptops.evaluation import EvaluationService
from src.promptops.experiments import ExperimentService
from src.promptops.registry import PromptRegistry

prompt_registry = PromptRegistry(observability_database)
prompt_evaluation_service = EvaluationService(observability_database)
prompt_experiment_service = ExperimentService(
    observability_database,
    prompt_registry,
    prompt_evaluation_service,
)

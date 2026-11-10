from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionPolicyDecision:
    action_type: str
    target_environment: str
    risk_level: str
    requires_approval: bool
    reason: str
    policy_version: str


@dataclass(frozen=True)
class ActionPolicy:
    """MVP 的版本化动作风险规则。

    未知写操作在 production 环境默认 fail closed，避免新动作绕过审批。
    """

    version: str = "v1"

    def evaluate(
        self,
        *,
        action_type: str,
        target_environment: str = "production",
    ) -> ActionPolicyDecision:
        action = action_type.strip().upper()
        environment = target_environment.strip().lower()

        if action == "ADD_OPPORTUNITY_TO_PRODUCTION":
            return self._decision(action, environment, "HIGH", True, "production_opportunity")
        if action == "PUBLISH_CONTENT" and environment == "production":
            return self._decision(action, environment, "HIGH", True, "production_publish")
        if action == "PUBLISH_CONTENT":
            return self._decision(action, environment, "LOW", False, "non_production_publish")
        if action == "DISPATCH_INTERNAL_HUMAN_TASK":
            return self._decision(action, environment, "LOW", False, "internal_dispatch")

        if environment == "production":
            return self._decision(
                action,
                environment,
                "HIGH",
                True,
                "unknown_write_action_fail_closed",
            )
        return self._decision(action, environment, "MEDIUM", True, "unknown_action_review")

    def _decision(
        self,
        action_type: str,
        target_environment: str,
        risk_level: str,
        requires_approval: bool,
        reason: str,
    ) -> ActionPolicyDecision:
        return ActionPolicyDecision(
            action_type=action_type,
            target_environment=target_environment,
            risk_level=risk_level,
            requires_approval=requires_approval,
            reason=reason,
            policy_version=self.version,
        )

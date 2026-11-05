"""Deployment settings, read from CDK context (``cdk.json``, or ``-c key=value`` on the command line)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from constructs import Node

REGION = "us-east-1"


@dataclass(frozen=True)
class Settings:
    env_name: str
    """Names every resource (``sbc-<env>-...``) and the SSM prefix (``/sbc/<env>/``)."""
    github_repository: str
    github_branch: str
    alarm_email: str
    monthly_budget_usd: int
    demo_user_ids: tuple[str, ...]
    warmer: bool
    github_oidc_provider_arn: str
    """An existing GitHub OIDC provider in the account (there can be only one); empty creates it."""
    api_image_tag: str
    """The API image tag in ECR to deploy (``SbcApp`` only); required for ``SbcApp``."""
    build_image_tag: str

    @property
    def prefix(self) -> str:
        return f"sbc-{self.env_name}"

    @property
    def ssm_prefix(self) -> str:
        return f"/sbc/{self.env_name}/"


def _get(node: Node, key: str, default: str = "") -> str:
    value = node.try_get_context(key)
    return default if value is None else str(value)


def load(node: Node) -> Settings:
    demo = json.loads(_get(node, "demoUserIds", "[]") or "[]")
    if not isinstance(demo, list) or not all(isinstance(x, str) for x in demo):
        raise ValueError("demoUserIds must be a JSON list of Cognito sub strings")
    return Settings(
        env_name=_get(node, "envName", "dev"),
        github_repository=_get(node, "githubRepository"),
        github_branch=_get(node, "githubBranch", "main_AWS"),
        alarm_email=_get(node, "alarmEmail"),
        monthly_budget_usd=int(_get(node, "monthlyBudgetUsd", "10")),
        demo_user_ids=tuple(demo),
        warmer=_get(node, "warmer", "false").lower() == "true",
        github_oidc_provider_arn=_get(node, "githubOidcProviderArn"),
        api_image_tag=_get(node, "apiImageTag"),
        build_image_tag=_get(node, "buildImageTag", "latest"),
    )

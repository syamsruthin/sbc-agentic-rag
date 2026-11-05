"""ECR repositories for the API and build images (lifecycle: keep the last few)."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_ecr as ecr
from constructs import Construct

from sbc_infra.config import Settings


class Registry(Construct):
    def __init__(self, scope: Construct, cid: str, *, settings: Settings) -> None:
        super().__init__(scope, cid)
        self.api = ecr.Repository(
            self,
            "ApiRepo",
            repository_name=f"{settings.prefix}-api",
            image_scan_on_push=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[ecr.LifecycleRule(description="keep the last 3 images", max_image_count=3)],
        )
        self.build = ecr.Repository(
            self,
            "BuildRepo",
            repository_name=f"{settings.prefix}-build",
            image_scan_on_push=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[ecr.LifecycleRule(description="keep the last 2 images", max_image_count=2)],
        )

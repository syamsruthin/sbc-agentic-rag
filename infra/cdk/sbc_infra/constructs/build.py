"""The offline SBC build as an on-demand ECS Fargate Spot task (WP8 image, WP9 infrastructure).

There is deliberately no VPC here: an ECS cluster needs none, and ``scripts/aws/run-build.sh`` passes the
account's default-VPC public subnets with a public IP when it starts a task, so no NAT gateway ever exists.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_logs as logs
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from sbc_infra.config import REGION, Settings
from sbc_infra.constructs.data import Data


class Build(Construct):
    def __init__(
        self, scope: Construct, cid: str, *, settings: Settings, data: Data, repo: ecr.IRepository
    ) -> None:
        super().__init__(scope, cid)
        self.cluster = ecs.CfnCluster(
            self,
            "Cluster",
            cluster_name=f"{settings.prefix}-build",
            capacity_providers=["FARGATE", "FARGATE_SPOT"],
            default_capacity_provider_strategy=[
                ecs.CfnCluster.CapacityProviderStrategyItemProperty(
                    capacity_provider="FARGATE_SPOT", weight=1
                )
            ],
        )
        log_group = logs.LogGroup(
            self,
            "Logs",
            log_group_name=f"/ecs/{settings.prefix}-build",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        self.task = ecs.FargateTaskDefinition(
            self,
            "Task",
            family=f"{settings.prefix}-build",
            cpu=4096,
            memory_limit_mib=8192,
            ephemeral_storage_gib=50,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        openrouter = ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "OpenRouterKey", parameter_name=f"{settings.ssm_prefix}OPENROUTER_API_KEY_BROKER"
        )
        self.container = self.task.add_container(
            "build",
            image=ecs.ContainerImage.from_ecr_repository(repo, tag=settings.build_image_tag),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="build", log_group=log_group),
            environment={"S3_DATA_BUCKET": data.bucket.bucket_name, "SBC_YEAR": "2027", "AWS_REGION": REGION},
            secrets={"OPENROUTER_API_KEY_BROKER": ecs.Secret.from_ssm_parameter(openrouter)},
            command=["bundle"],
        )
        data.bucket.grant_read_write(self.task.task_role)

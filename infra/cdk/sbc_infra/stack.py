"""The two stacks (docs/aws/MIGRATION_PLAN.md WP9).

- ``SbcFoundation-<env>``: what must exist before any image does: the data bucket, the DynamoDB tables, the
  Cognito pool, the ECR repositories and the cost guardrails.
- ``SbcApp-<env>``: what runs from those images: the API Lambda (with its alias and streaming Function URL),
  CloudFront and the web bucket, the offline build task, the alarms and the GitHub deploy role.

Splitting them solves the first-deploy chicken-and-egg (a Lambda needs an image in ECR before it can exist):
deploy the foundation, push the images, then deploy the app with ``-c apiImageTag=<tag>``.
"""

from __future__ import annotations

import aws_cdk as cdk
from constructs import Construct

from sbc_infra.config import Settings
from sbc_infra.constructs.api import Api
from sbc_infra.constructs.auth import Auth
from sbc_infra.constructs.build import Build
from sbc_infra.constructs.cicd import Cicd
from sbc_infra.constructs.data import Data
from sbc_infra.constructs.ops import CostGuards, Ops
from sbc_infra.constructs.registry import Registry
from sbc_infra.constructs.web import Web


class FoundationStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, settings: Settings, **kwargs: object) -> None:
        super().__init__(scope, cid, **kwargs)  # type: ignore[arg-type]
        self.settings = settings
        self.data = Data(self, "Data", settings=settings)
        self.auth = Auth(self, "Auth", settings=settings)
        self.registry = Registry(self, "Registry", settings=settings)
        CostGuards(self, "CostGuards", settings=settings)
        out = {
            "DataBucket": self.data.bucket.bucket_name,
            "UserPoolId": self.auth.pool.user_pool_id,
            "UserPoolClientId": self.auth.client.user_pool_client_id,
            "ApiRepositoryUri": self.registry.api.repository_uri,
            "BuildRepositoryUri": self.registry.build.repository_uri,
        }
        for name, value in out.items():
            cdk.CfnOutput(self, name, value=value)


class AppStack(cdk.Stack):
    def __init__(
        self, scope: Construct, cid: str, *, settings: Settings, foundation: FoundationStack, **kwargs: object
    ) -> None:
        super().__init__(scope, cid, **kwargs)  # type: ignore[arg-type]
        f = foundation
        api = Api(self, "Api", settings=settings, data=f.data, auth=f.auth, repo=f.registry.api)
        web = Web(self, "Web", settings=settings, api_url=api.url)
        build = Build(self, "Build", settings=settings, data=f.data, repo=f.registry.build)
        Ops(self, "Ops", settings=settings, function=api.function, alias=api.alias, log_group=api.log_group)
        cicd = Cicd(
            self,
            "Cicd",
            settings=settings,
            repos=(f.registry.api, f.registry.build),
            web_bucket=web.bucket,
            data_bucket=f.data.bucket,
            distribution=web.distribution,
            task=build.task,
            cluster=build.cluster,
        )
        out = {
            "SiteUrl": f"https://{web.distribution.distribution_domain_name}",
            "DistributionId": web.distribution.distribution_id,
            "WebBucket": web.bucket.bucket_name,
            "ApiFunctionName": api.function.function_name,
            "ApiAliasName": api.alias.alias_name,
            "BuildCluster": build.cluster.ref,
            "BuildTaskDefinition": build.task.task_definition_arn,
            "DeployRoleArn": cicd.role.role_arn,
        }
        for name, value in out.items():
            cdk.CfnOutput(self, name, value=value)

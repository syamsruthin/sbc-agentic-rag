"""The API: one container-image Lambda for both bots, a ``live`` alias and its streaming Function URL.

Least privilege (DEPLOYMENT_ANALYSIS.md 6.5): read/write on the app and checkpoints tables, **PutItem
only** on the audit table, the data bucket's ``pdf-cache/`` and ``checkpoints/`` prefixes, and the SSM
parameters under this environment's prefix. No VPC, so outbound HTTPS (OpenRouter, the insurer portal,
Turnstile, Cognito JWKS) needs no NAT.
"""

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from sbc_infra.config import REGION, Settings
from sbc_infra.constructs.auth import Auth
from sbc_infra.constructs.data import Data

LOG_LEVEL = "INFO"
"""The API's log level, for both Lambda's filter and the app (``LOG_LEVEL``)."""


class Api(Construct):
    def __init__(
        self, scope: Construct, cid: str, *, settings: Settings, data: Data, auth: Auth, repo: ecr.IRepository
    ) -> None:
        super().__init__(scope, cid)
        stack = cdk.Stack.of(self)
        self.log_group = logs.LogGroup(
            self,
            "Logs",
            log_group_name=f"/aws/lambda/{settings.prefix}-api",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        self.function = lambda_.DockerImageFunction(
            self,
            "Function",
            function_name=f"{settings.prefix}-api",
            code=lambda_.DockerImageCode.from_ecr(repo, tag_or_digest=settings.api_image_tag),
            architecture=lambda_.Architecture.ARM_64,
            memory_size=2048,
            timeout=cdk.Duration.seconds(60),
            ephemeral_storage_size=cdk.Size.mebibytes(1024),
            log_group=self.log_group,
            # JSON log format (WP12): Lambda's own lines (START, REPORT…) become JSON too, and the application
            # level filter matches the API's JSON lines by their "level" field. LOG_LEVEL below is the same
            # level, applied inside the app.
            logging_format=lambda_.LoggingFormat.JSON,
            application_log_level_v2=lambda_.ApplicationLogLevel[LOG_LEVEL],
            system_log_level_v2=lambda_.SystemLogLevel.INFO,
            description=f"sbc-agentic-rag API ({settings.env_name}), image {settings.api_image_tag}",
            environment={
                "APP_ENV": "dev" if settings.env_name == "dev" else "prd",
                "LOG_LEVEL": LOG_LEVEL,
                "SSM_PREFIX": settings.ssm_prefix,
                "DDB_TABLE_APP": data.tables["app"].table_name,
                "DDB_TABLE_AUDIT": data.tables["audit"].table_name,
                "DDB_TABLE_CHECKPOINTS": data.tables["checkpoints"].table_name,
                "S3_DATA_BUCKET": data.bucket.bucket_name,
                "COGNITO_USER_POOL_ID": auth.pool.user_pool_id,
                "COGNITO_APP_CLIENT_ID": auth.client.user_pool_client_id,
                "SBC_PORTFOLIO__DEMO_USER_IDS": json.dumps(list(settings.demo_user_ids)),
            },
        )
        self.alias = self.function.add_alias("live")
        self.url = self.alias.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM, invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM
        )

        data.tables["app"].grant_read_write_data(self.function)
        data.tables["checkpoints"].grant_read_write_data(self.function)
        # The audit log is append-only: the API may add records, never read, change or delete them.
        self.function.add_to_role_policy(
            iam.PolicyStatement(actions=["dynamodb:PutItem"], resources=[data.tables["audit"].table_arn])
        )
        for prefix in ("pdf-cache/*", "checkpoints/*"):
            data.bucket.grant_read_write(self.function, prefix)
        param_arn = f"arn:aws:ssm:{REGION}:{stack.account}:parameter{settings.ssm_prefix.rstrip('/')}"
        self.function.add_to_role_policy(
            iam.PolicyStatement(actions=["ssm:GetParametersByPath"], resources=[param_arn, f"{param_arn}/*"])
        )
        self.function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["kms:Decrypt"],
                resources=["*"],
                conditions={"StringEquals": {"kms:ViaService": f"ssm.{REGION}.amazonaws.com"}},
            )
        )

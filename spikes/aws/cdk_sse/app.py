"""Spike stack: ECR repo -> Lambda (container, arm64, 2 GB) -> Function URL (RESPONSE_STREAM, AWS_IAM)
-> CloudFront with origin access control. Deploy it, run the SSE client and the cold-start check, then
destroy it.

Two steps, because the function needs an image in the repository first (see spikes/aws/README.md):

    npx aws-cdk@2.1147.0 deploy -c deployFunction=false     # creates the ECR repository only
    (push the image)                                         # spikes/aws/deploy_spike.sh does both steps
    npx aws-cdk@2.1147.0 deploy -c imageTag=<tag>
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct


class SseSpikeStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]
        repo = ecr.Repository(
            self, "Repo", repository_name="sbc-spike", removal_policy=cdk.RemovalPolicy.DESTROY,
            empty_on_delete=True,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=2)],
        )  # fmt: skip
        cdk.CfnOutput(self, "RepoUri", value=repo.repository_uri)
        if self.node.try_get_context("deployFunction") == "false":
            return

        fn = lambda_.DockerImageFunction(
            self, "Api",
            code=lambda_.DockerImageCode.from_ecr(repo, tag_or_digest=self.node.get_context("imageTag")),
            architecture=lambda_.Architecture.ARM_64,
            memory_size=2048,
            timeout=cdk.Duration.seconds(60),
            ephemeral_storage_size=cdk.Size.mebibytes(1024),
            environment={"DDB_TABLE_CHECKPOINTS": "unused-in-spike"},
            log_group=logs.LogGroup(
                self, "Logs", retention=logs.RetentionDays.ONE_WEEK, removal_policy=cdk.RemovalPolicy.DESTROY
            ),
        )  # fmt: skip
        url = fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM, invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM
        )
        dist = cf.Distribution(
            self, "Cdn",
            price_class=cf.PriceClass.PRICE_CLASS_100,
            default_behavior=cf.BehaviorOptions(
                origin=origins.FunctionUrlOrigin.with_origin_access_control(url),
                allowed_methods=cf.AllowedMethods.ALLOW_ALL,
                cache_policy=cf.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
        )  # fmt: skip
        cdk.CfnOutput(self, "FunctionName", value=fn.function_name)
        cdk.CfnOutput(self, "FunctionUrl", value=url.url)
        cdk.CfnOutput(self, "CloudFrontUrl", value=f"https://{dist.distribution_domain_name}")


app = cdk.App()
SseSpikeStack(app, "SbcSseSpike", env=cdk.Environment(region="us-east-1"))
app.synth()

"""GitHub Actions deploys through OIDC (no long-lived keys): a role only the configured branch can assume.

The deploy workflow (``.github/workflows/deploy-aws.yml``, WP11) builds and pushes the API image, then deploys
``SbcApp`` through the CDK, which assumes the CDK bootstrap roles. CloudFormation owns the Lambda's ``live``
alias, so this role can't change the function directly: a raw alias move would be undone by the next deploy.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

from sbc_infra.config import Settings

GITHUB_OIDC = "token.actions.githubusercontent.com"
CDK_QUALIFIER = "hnb659fds"
"""The default ``cdk bootstrap`` qualifier; the bootstrap roles' names contain it."""
CDK_ROLES = ("deploy", "file-publishing")
"""Bootstrap roles a ``cdk deploy`` of ``SbcApp`` needs: CloudFormation, and the template upload. The stack
has no Docker or file assets of its own (images come from ECR by tag) and no context lookups."""
DEPLOY_CONTEXT_PREFIX = "sbc-deploy"
"""SSM path ``/sbc-deploy/<env>/``: the CDK context saved by ``scripts/aws/deploy.sh`` (alarm email, demo user
IDs, warmer, OIDC provider, build image tag), so a CI deploy keeps it. It sits outside ``/sbc/<env>/``, which
the API loads into its environment."""


class Cicd(Construct):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        settings: Settings,
        repos: tuple[ecr.IRepository, ...],
        web_bucket: s3.IBucket,
        data_bucket: s3.IBucket,
        distribution: cf.Distribution,
        task: ecs.FargateTaskDefinition,
        cluster: ecs.CfnCluster,
    ) -> None:
        super().__init__(scope, cid)
        stack = cdk.Stack.of(self)
        if settings.github_oidc_provider_arn:
            provider_arn = settings.github_oidc_provider_arn
        else:
            provider = iam.OidcProviderNative(
                self, "GithubOidc", url=f"https://{GITHUB_OIDC}", client_ids=["sts.amazonaws.com"]
            )
            provider_arn = provider.oidc_provider_arn
        branch_subject = f"repo:{settings.github_repository}:ref:refs/heads/{settings.github_branch}"
        self.role = iam.Role(
            self,
            "DeployRole",
            role_name=f"{settings.prefix}-github-deploy",
            max_session_duration=cdk.Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider_arn,
                conditions={
                    "StringEquals": {f"{GITHUB_OIDC}:aud": "sts.amazonaws.com"},
                    "StringLike": {f"{GITHUB_OIDC}:sub": branch_subject},
                },
            ),
        )
        for repo in repos:
            repo.grant_pull_push(self.role)
        # cdk deploy: CloudFormation runs as the bootstrap roles, which trust the account.
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[
                    f"arn:aws:iam::{stack.account}:role/cdk-{CDK_QUALIFIER}-{kind}-role-{stack.account}-{stack.region}"
                    for kind in CDK_ROLES
                ],
            )
        )
        # The scripts read stack outputs (repositories, bucket, site URL) and the saved deploy context.
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks"],
                resources=[
                    f"arn:aws:cloudformation:{stack.region}:{stack.account}:stack/{name}-{settings.env_name}/*"
                    for name in ("SbcFoundation", "SbcApp")
                ],
            )
        )
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParametersByPath"],
                resources=[
                    f"arn:aws:ssm:{stack.region}:{stack.account}:parameter/{DEPLOY_CONTEXT_PREFIX}/{settings.env_name}"
                ],
            )
        )
        web_bucket.grant_read_write(self.role)
        web_bucket.grant_delete(self.role)
        data_bucket.grant_read(self.role, "bundles/*")
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"],
                resources=[
                    f"arn:aws:cloudfront::{stack.account}:distribution/{distribution.distribution_id}"
                ],
            )
        )
        # Starting the offline build (workflow_dispatch): the task, on the build cluster, in default subnets.
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[task.task_definition_arn],
                conditions={"ArnEquals": {"ecs:cluster": cluster.attr_arn}},
            )
        )
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:DescribeTasks"],
                resources=["*"],
                conditions={"ArnEquals": {"ecs:cluster": cluster.attr_arn}},
            )
        )
        self.role.add_to_policy(
            iam.PolicyStatement(actions=["ec2:DescribeSubnets", "ec2:DescribeVpcs"], resources=["*"])
        )
        passable = [task.task_role.role_arn]
        if task.execution_role is not None:
            passable.append(task.execution_role.role_arn)
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=passable,
                conditions={"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
            )
        )

"""Template tests: the cost guardrails (DEPLOYMENT_ANALYSIS.md section 12) and the security rules the API
relies on are properties of the synthesized templates, checked here on every change."""

from __future__ import annotations

import json
from typing import Any

from aws_cdk.assertions import Match, Template
from sbc_infra.constructs.web import API_HEADERS
from sbc_infra.tables import TABLES

COSTLY = [
    "AWS::EC2::NatGateway",
    "AWS::EC2::EIP",
    "AWS::EC2::VPC",
    "AWS::ElasticLoadBalancingV2::LoadBalancer",
    "AWS::SecretsManager::Secret",
    "AWS::KMS::Key",
    "AWS::RDS::DBInstance",
    "AWS::RDS::DBCluster",
    "AWS::ElastiCache::ServerlessCache",
    "AWS::WAFv2::WebACL",
    "AWS::Lambda::EventSourceMapping",
]


def resources(t: Template, kind: str) -> dict[str, Any]:
    return t.find_resources(kind)


def test_nothing_that_bills_by_the_hour_while_idle(stacks: dict[str, Template]) -> None:
    for template in stacks.values():
        for kind in COSTLY:
            template.resource_count_is(kind, 0)
        # No custom resources either (each would add a Lambda function and its logs).
        assert not [k for k in template.to_json()["Resources"].values() if k["Type"].startswith("Custom::")]
    stacks["app"].resource_count_is("AWS::Lambda::Function", 1)


def test_tables_match_the_app_schema(stacks: dict[str, Template]) -> None:
    tables = resources(stacks["foundation"], "AWS::DynamoDB::Table")
    got = {
        t["Properties"]["TableName"].rsplit("-", 1)[-1]: (
            t["Properties"]["KeySchema"][0]["AttributeName"],
            t["Properties"]["KeySchema"][1]["AttributeName"],
            t["Properties"]["TimeToLiveSpecification"]["AttributeName"],
            t["Properties"]["BillingMode"],
            t["DeletionPolicy"],
        )
        for t in tables.values()
    }
    assert got == {role: (pk, sk, ttl, "PAY_PER_REQUEST", "Retain") for role, pk, sk, ttl in TABLES}


def test_the_api_lambda_streams_behind_iam(stacks: dict[str, Template]) -> None:
    app = stacks["app"]
    app.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Architectures": ["arm64"],
            "MemorySize": 2048,
            "Timeout": 60,
            "PackageType": "Image",
            "EphemeralStorage": {"Size": 1024},
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "APP_ENV": "dev",
                        "SSM_PREFIX": "/sbc/dev/",
                        "SBC_PORTFOLIO__DEMO_USER_IDS": '["11111111-2222-3333-4444-555555555555"]',
                    }
                )
            },
        },
    )
    app.has_resource_properties(
        "AWS::Lambda::Url", {"AuthType": "AWS_IAM", "InvokeMode": "RESPONSE_STREAM", "Qualifier": "live"}
    )
    perms = sorted(
        p["Properties"]["Action"]
        for p in resources(app, "AWS::Lambda::Permission").values()
        if p["Properties"]["Principal"] == "cloudfront.amazonaws.com"
    )
    assert perms == ["lambda:InvokeFunction", "lambda:InvokeFunctionUrl"]  # spike 2: CloudFront needs both


def _statements(app: Template) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for policy in resources(app, "AWS::IAM::Policy").values():
        out += policy["Properties"]["PolicyDocument"]["Statement"]
    return out


def test_the_audit_table_is_append_only_for_the_api(stacks: dict[str, Template]) -> None:
    text = json.dumps(stacks["app"].to_json())
    audit_refs = [s for s in _statements(stacks["app"]) if "AuditTable" in json.dumps(s["Resource"])]
    assert audit_refs, "the API must be allowed to write audit records"
    for statement in audit_refs:
        assert statement["Action"] == "dynamodb:PutItem", statement
    assert "AuditTable" in text


def test_secrets_come_only_from_this_environments_ssm_prefix(stacks: dict[str, Template]) -> None:
    ssm = [s for s in _statements(stacks["app"]) if "ssm:GetParametersByPath" in json.dumps(s["Action"])]
    assert len(ssm) == 1
    assert "parameter/sbc/dev" in json.dumps(ssm[0]["Resource"])
    kms = [s for s in _statements(stacks["app"]) if s["Action"] == "kms:Decrypt"]
    assert kms
    assert kms[0]["Condition"] == {"StringEquals": {"kms:ViaService": "ssm.us-east-1.amazonaws.com"}}


def test_every_log_group_expires(stacks: dict[str, Template]) -> None:
    for template in stacks.values():
        for group in resources(template, "AWS::Logs::LogGroup").values():
            assert group["Properties"].get("RetentionInDays"), group


def test_cloudfront_forwards_the_token_header_and_the_viewer_ip(stacks: dict[str, Template]) -> None:
    app = stacks["app"]
    app.has_resource_properties(
        "AWS::CloudFront::OriginRequestPolicy",
        {
            "OriginRequestPolicyConfig": Match.object_like(
                {
                    "HeadersConfig": {"HeaderBehavior": "whitelist", "Headers": list(API_HEADERS)},
                }
            )
        },
    )
    assert "X-Sbc-Authorization" in API_HEADERS  # OAC replaces Authorization with its own signature
    assert "CloudFront-Viewer-Address" in API_HEADERS  # per-IP limits and Turnstile's remoteip
    app.has_resource_properties(
        "AWS::CloudFront::OriginAccessControl",
        {
            "OriginAccessControlConfig": Match.object_like(
                {
                    "OriginAccessControlOriginType": "lambda",
                    "SigningBehavior": "always",
                    "SigningProtocol": "sigv4",
                }
            )
        },
    )
    dist = next(iter(resources(app, "AWS::CloudFront::Distribution").values()))["Properties"][
        "DistributionConfig"
    ]
    api = next(b for b in dist["CacheBehaviors"] if b["PathPattern"] == "/api/*")
    assert api["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"  # Managed-CachingDisabled
    assert set(api["AllowedMethods"]) >= {"GET", "POST", "OPTIONS"}
    assert dist["PriceClass"] == "PriceClass_100"
    assert "Aliases" not in dist  # the default *.cloudfront.net domain


def test_buckets_are_private_encrypted_and_kept(stacks: dict[str, Template]) -> None:
    for template in stacks.values():
        for bucket in resources(template, "AWS::S3::Bucket").values():
            props = bucket["Properties"]
            assert props["PublicAccessBlockConfiguration"] == {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }
            assert (
                props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
                    "ServerSideEncryptionByDefault"
                ]["SSEAlgorithm"]
                == "AES256"
            )
            assert bucket["DeletionPolicy"] == "Retain"
    rules = next(iter(resources(stacks["foundation"], "AWS::S3::Bucket").values()))["Properties"][
        "LifecycleConfiguration"
    ]["Rules"]
    pdf = next(r for r in rules if r["Id"] == "pdf-cache-1-day")
    assert (pdf["Prefix"], pdf["ExpirationInDays"]) == ("pdf-cache/", 1)


def test_cognito_suits_a_public_demo_login(stacks: dict[str, Template]) -> None:
    f = stacks["foundation"]
    f.has_resource_properties(
        "AWS::Cognito::UserPool",
        {
            "MfaConfiguration": "OPTIONAL",
            "EnabledMfas": ["SOFTWARE_TOKEN_MFA"],
            "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        },
    )
    client = next(iter(resources(f, "AWS::Cognito::UserPoolClient").values()))["Properties"]
    assert client["GenerateSecret"] is False
    assert "AllowedOAuthScopes" not in client  # so no aws.cognito.signin.user.admin scope (decision D2)
    assert client.get("AllowedOAuthFlowsUserPoolClient") is False
    assert sorted(client["ExplicitAuthFlows"]) == ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]
    groups = sorted(
        g["Properties"]["GroupName"] for g in resources(f, "AWS::Cognito::UserPoolGroup").values()
    )
    assert groups == ["admin", "broker"]


def test_the_build_task_runs_without_a_vpc_of_its_own(stacks: dict[str, Template]) -> None:
    app = stacks["app"]
    app.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {
            "Cpu": "4096",
            "Memory": "8192",
            "EphemeralStorage": {"SizeInGiB": 50},
            "RuntimePlatform": {"CpuArchitecture": "ARM64", "OperatingSystemFamily": "LINUX"},
            "RequiresCompatibilities": ["FARGATE"],
        },
    )
    task = next(iter(resources(app, "AWS::ECS::TaskDefinition").values()))["Properties"]
    container = task["ContainerDefinitions"][0]
    assert [s["Name"] for s in container["Secrets"]] == ["OPENROUTER_API_KEY_BROKER"]
    app.has_resource_properties("AWS::ECS::Cluster", {"CapacityProviders": ["FARGATE", "FARGATE_SPOT"]})


def test_deploys_are_keyless_and_limited_to_one_branch(stacks: dict[str, Template]) -> None:
    app = stacks["app"]
    roles = resources(app, "AWS::IAM::Role")
    deploy = next(r for r in roles.values() if r["Properties"].get("RoleName") == "sbc-dev-github-deploy")
    condition = deploy["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]
    assert condition["StringLike"]["token.actions.githubusercontent.com:sub"] == (
        "repo:syamsruthin/sbc-agentic-rag:ref:refs/heads/main_AWS"
    )
    assert condition["StringEquals"]["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    app.resource_count_is("AWS::IAM::User", 0)
    app.resource_count_is("AWS::IAM::AccessKey", 0)


def test_alarms_stay_within_the_free_ten_and_costs_are_watched(stacks: dict[str, Template]) -> None:
    count = len(resources(stacks["app"], "AWS::CloudWatch::Alarm"))
    assert 1 <= count <= 10
    stacks["foundation"].has_resource_properties(
        "AWS::Budgets::Budget",
        {
            "Budget": Match.object_like(
                {
                    "BudgetType": "COST",
                    "TimeUnit": "MONTHLY",
                    "BudgetLimit": {"Amount": 10, "Unit": "USD"},
                }
            )
        },
    )
    stacks["foundation"].resource_count_is("AWS::CE::AnomalySubscription", 1)
    stacks["app"].has_resource_properties(
        "AWS::Scheduler::Schedule",
        {
            "ScheduleExpression": "cron(*/5 8-19 ? * MON-FRI *)",
            "ScheduleExpressionTimezone": "America/New_York",
        },
    )

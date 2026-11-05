"""The data bucket and the DynamoDB tables (docs/aws/MIGRATION_PLAN.md section 6.3; RUNBOOK section 3)."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_s3 as s3
from constructs import Construct

from sbc_infra.config import Settings
from sbc_infra.tables import TABLES


class Data(Construct):
    def __init__(self, scope: Construct, cid: str, *, settings: Settings) -> None:
        super().__init__(scope, cid)
        # One bucket laid out like the repository (data/, bundles/), plus the API's pdf-cache/, checkpoints/.
        self.bucket = s3.Bucket(
            self,
            "DataBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(id="pdf-cache-1-day", prefix="pdf-cache/", expiration=cdk.Duration.days(1)),
                # Checkpoints offloaded by DynamoDBSaver; the longest retention is the broker's 365 days.
                s3.LifecycleRule(
                    id="checkpoints-1-year", prefix="checkpoints/", expiration=cdk.Duration.days(400)
                ),
                s3.LifecycleRule(
                    id="raw-pdfs-to-glacier-ir",
                    prefix="data/raw/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                            transition_after=cdk.Duration.days(30),
                        )
                    ],
                ),
                s3.LifecycleRule(
                    id="incomplete-uploads", abort_incomplete_multipart_upload_after=cdk.Duration.days(2)
                ),
            ],
        )
        self.tables: dict[str, ddb.Table] = {}
        for role, pk, sk, ttl in TABLES:
            self.tables[role] = ddb.Table(
                self,
                f"{role.capitalize()}Table",
                table_name=f"{settings.prefix}-{role}",
                partition_key=ddb.Attribute(name=pk, type=ddb.AttributeType.STRING),
                sort_key=ddb.Attribute(name=sk, type=ddb.AttributeType.STRING),
                billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                time_to_live_attribute=ttl,
                encryption=ddb.TableEncryption.AWS_MANAGED,
                removal_policy=cdk.RemovalPolicy.RETAIN,
            )

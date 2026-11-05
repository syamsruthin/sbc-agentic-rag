"""CDK entry point. ``SbcApp`` is synthesized only when an API image tag is given (``-c apiImageTag=...``)."""

from __future__ import annotations

import os

import aws_cdk as cdk
from sbc_infra import config
from sbc_infra.stack import AppStack, FoundationStack

app = cdk.App()
settings = config.load(app.node)
env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=config.REGION)
tags = {"project": "sbc-agentic-rag", "env": settings.env_name}
foundation = FoundationStack(app, f"SbcFoundation-{settings.env_name}", settings=settings, env=env, tags=tags)
if settings.api_image_tag:
    AppStack(app, f"SbcApp-{settings.env_name}", settings=settings, foundation=foundation, env=env, tags=tags)
app.synth()

"""The public site: a private S3 bucket for the apps and one CloudFront distribution in front of the apps and
the API (``/api/*`` → the Lambda Function URL), on the default ``*.cloudfront.net`` domain (WP9).

Two details the API depends on:

- **The bearer token travels in ``X-Sbc-Authorization``.** Origin access control signs every request to
  the Function URL with SigV4 in the ``Authorization`` header, replacing whatever the browser sent, so the
  apps send their Cognito or session token in this header and the API reads it first.
- **``CloudFront-Viewer-Address`` is forwarded,** so the per-IP limits and Turnstile see the visitor's IP, not
  CloudFront's.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from constructs import Construct

from sbc_infra.config import REGION, Settings

API_HEADERS = (
    "X-Sbc-Authorization",
    "Content-Type",
    "Accept",
    "x-amz-content-sha256",
    "CloudFront-Viewer-Address",
)

# Routes each app's client-side paths to its index.html; files (anything with an extension) pass through.
SPA_REWRITE = """
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  var apps = ['/broker', '/shopper'];
  for (var i = 0; i < apps.length; i++) {
    var app = apps[i];
    if (uri === app) {
      var to = { location: { value: app + '/' } };
      return { statusCode: 301, statusDescription: 'Moved Permanently', headers: to };
    }
    if (uri.indexOf(app + '/') === 0) {
      if (uri.indexOf('.', uri.lastIndexOf('/')) === -1) { request.uri = app + '/index.html'; }
      return request;
    }
  }
  if (uri.indexOf('.', uri.lastIndexOf('/')) === -1) { request.uri = '/index.html'; }
  return request;
}
"""

CSP = "; ".join(
    [
        "default-src 'self'",
        f"connect-src 'self' https://cognito-idp.{REGION}.amazonaws.com",
        "script-src 'self' https://challenges.cloudflare.com",
        "frame-src https://challenges.cloudflare.com",
        "img-src 'self' data:",
        "style-src 'self' 'unsafe-inline'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
    ]
)


class Web(Construct):
    def __init__(
        self, scope: Construct, cid: str, *, settings: Settings, api_url: lambda_.IFunctionUrl
    ) -> None:
        super().__init__(scope, cid)
        self.bucket = s3.Bucket(
            self,
            "WebBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        headers = cf.ResponseHeadersPolicy(
            self,
            "SecurityHeaders",
            response_headers_policy_name=f"{settings.prefix}-security-headers",
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                content_security_policy=cf.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=CSP, override=True
                ),
                content_type_options=cf.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cf.ResponseHeadersFrameOptions(
                    frame_option=cf.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override=True
                ),
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=cdk.Duration.days(365), include_subdomains=True, override=True
                ),
            ),
        )
        api_requests = cf.OriginRequestPolicy(
            self,
            "ApiRequests",
            origin_request_policy_name=f"{settings.prefix}-api-requests",
            header_behavior=cf.OriginRequestHeaderBehavior.allow_list(*API_HEADERS),
            query_string_behavior=cf.OriginRequestQueryStringBehavior.all(),
            cookie_behavior=cf.OriginRequestCookieBehavior.none(),
        )
        rewrite = cf.Function(
            self,
            "SpaRewrite",
            code=cf.FunctionCode.from_inline(SPA_REWRITE),
            runtime=cf.FunctionRuntime.JS_2_0,
            comment="SPA routes to index.html",
        )
        self.distribution = cf.Distribution(
            self,
            "Site",
            comment=f"sbc-agentic-rag ({settings.env_name})",
            price_class=cf.PriceClass.PRICE_CLASS_100,
            http_version=cf.HttpVersion.HTTP2_AND_3,
            default_root_object="index.html",
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=headers,
                function_associations=[
                    cf.FunctionAssociation(function=rewrite, event_type=cf.FunctionEventType.VIEWER_REQUEST)
                ],
            ),
            additional_behaviors={
                "/api/*": cf.BehaviorOptions(
                    origin=origins.FunctionUrlOrigin.with_origin_access_control(
                        api_url,
                        read_timeout=cdk.Duration.seconds(60),
                        keepalive_timeout=cdk.Duration.seconds(60),
                    ),
                    viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cf.AllowedMethods.ALLOW_ALL,
                    cache_policy=cf.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=api_requests,
                    response_headers_policy=headers,
                ),
            },
        )

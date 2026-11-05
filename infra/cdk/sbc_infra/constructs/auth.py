"""The broker sign-in: a Cognito user pool, a public SPA client and the broker/admin groups (WP5, WP9).

MFA is *optional* at pool level so the shared demo broker account can sign in without it; the admin enrolls
TOTP. The SPA client has no secret and no ``aws.cognito.signin.user.admin`` scope, so a token can't be used to
change the account's own password or attributes (the demo credentials are public, decision D2).
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_cognito as cognito
from constructs import Construct

from sbc_infra.config import Settings


class Auth(Construct):
    def __init__(self, scope: Construct, cid: str, *, settings: Settings) -> None:
        super().__init__(scope, cid)
        self.pool = cognito.UserPool(
            self,
            "BrokerPool",
            user_pool_name=f"{settings.prefix}-brokers",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(username=True, email=True),
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            account_recovery=cognito.AccountRecovery.NONE,
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        self.client = self.pool.add_client(
            "SpaClient",
            user_pool_client_name=f"{settings.prefix}-spa",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True),
            prevent_user_existence_errors=True,
            access_token_validity=cdk.Duration.hours(1),
            id_token_validity=cdk.Duration.hours(1),
            refresh_token_validity=cdk.Duration.hours(12),
            enable_token_revocation=True,
            # No OAuth or hosted UI: the SPA signs in with SRP directly, so its tokens never carry the
            # aws.cognito.signin.user.admin scope.
            disable_o_auth=True,
            read_attributes=cognito.ClientAttributes().with_standard_attributes(email=True),
            write_attributes=cognito.ClientAttributes(),
        )
        for group, description in (
            ("broker", "Broker Assistant users"),
            ("admin", "Kill switches, quarantine"),
        ):
            cognito.CfnUserPoolGroup(
                self,
                f"{group.capitalize()}Group",
                user_pool_id=self.pool.user_pool_id,
                group_name=group,
                description=description,
            )

"""Alarms, the alert topic, the optional warmer (WP9) and the account's cost guardrails.

At most 10 alarms (the CloudWatch free tier). Application events come from the API's JSON log lines (the
``event`` field), so the metric filters need no code beyond what the API already logs.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_ce as ce
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

from sbc_infra.config import Settings

NAMESPACE = "SbcAgenticRag"
LOG_EVENTS = {
    # name: (filter pattern, description)
    "GlobalCapReached": ('{ $.event = "global_cap_reached" }', "The daily cap on LLM turns was reached"),
    "PdfHashMismatch": ('{ $.event = "pdf_hash_mismatch" }', "An SBC changed upstream: rebuild the bundle"),
    "AuditFailed": ('{ $.event = "audit_failed" }', "An audit record could not be written"),
    "DemoAccountLimited": (
        '{ ($.event = "rate_limited") && ($.demo IS TRUE) }',
        "The shared demo account hit its limits",
    ),
    "PlanFilterViolation": ('"PlanFilterViolationError"', "A search returned a chunk outside its plans (P1)"),
}


class Ops(Construct):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        settings: Settings,
        function: lambda_.Function,
        alias: lambda_.Alias,
        log_group: logs.ILogGroup,
    ) -> None:
        super().__init__(scope, cid)
        self.topic = sns.Topic(self, "Alerts", topic_name=f"{settings.prefix}-alerts")
        if settings.alarm_email:
            self.topic.add_subscription(subs.EmailSubscription(settings.alarm_email))
        action = cw_actions.SnsAction(self.topic)

        def alarm(name: str, metric: cw.IMetric, threshold: float, description: str) -> None:
            a = cw.Alarm(
                self,
                name,
                alarm_name=f"{settings.prefix}-{name}",
                metric=metric,
                threshold=threshold,
                evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=description,
            )
            a.add_alarm_action(action)

        five = cdk.Duration.minutes(5)
        alarm("lambda-errors", function.metric_errors(period=five), 1, "The API raised errors")
        alarm("lambda-throttles", function.metric_throttles(period=five), 1, "The API was throttled")
        alarm(
            "lambda-p95-duration",
            function.metric_duration(period=five, statistic="p95"),
            20_000,
            "p95 duration over 20 s",
        )
        for name, (pattern, description) in LOG_EVENTS.items():
            logs.MetricFilter(
                self,
                f"{name}Filter",
                log_group=log_group,
                metric_namespace=NAMESPACE,
                metric_name=name,
                filter_pattern=logs.FilterPattern.literal(pattern),
                metric_value="1",
                default_value=0,
            )
            metric = cw.Metric(namespace=NAMESPACE, metric_name=name, period=five, statistic="Sum")
            alarm(name, metric, 1, description)

        if settings.warmer:
            # Weekday business hours (US Eastern): one invocation every 5 minutes keeps an instance warm for
            # interviewers, inside the Lambda free tier. The Lambda Web Adapter passes non-HTTP events to
            # POST /events, which the API answers with 204.
            role = iam.Role(self, "WarmerRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
            alias.grant_invoke(role)
            scheduler.CfnSchedule(
                self,
                "Warmer",
                name=f"{settings.prefix}-warmer",
                schedule_expression="cron(*/5 8-19 ? * MON-FRI *)",
                schedule_expression_timezone="America/New_York",
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=alias.function_arn, role_arn=role.role_arn, input='{"warm": true}'
                ),
            )


class CostGuards(Construct):
    """A monthly cost budget with email alerts (50/80/100% actual, 100% forecast) and anomaly detection."""

    def __init__(self, scope: Construct, cid: str, *, settings: Settings) -> None:
        super().__init__(scope, cid)
        if not settings.alarm_email:
            return
        subscriber = budgets.CfnBudget.SubscriberProperty(
            subscription_type="EMAIL", address=settings.alarm_email
        )
        notifications = [
            budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type=kind,
                    comparison_operator="GREATER_THAN",
                    threshold=pct,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=[subscriber],
            )
            for kind, pct in (("ACTUAL", 50), ("ACTUAL", 80), ("ACTUAL", 100), ("FORECASTED", 100))
        ]
        budgets.CfnBudget(
            self,
            "Monthly",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=f"{settings.prefix}-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=settings.monthly_budget_usd, unit="USD"),
            ),
            notifications_with_subscribers=notifications,
        )
        monitor = ce.CfnAnomalyMonitor(
            self,
            "AnomalyMonitor",
            monitor_name=f"{settings.prefix}-services",
            monitor_type="DIMENSIONAL",
            monitor_dimension="SERVICE",
        )
        ce.CfnAnomalySubscription(
            self,
            "AnomalyAlerts",
            subscription_name=f"{settings.prefix}-anomalies",
            frequency="DAILY",
            monitor_arn_list=[monitor.attr_monitor_arn],
            subscribers=[
                ce.CfnAnomalySubscription.SubscriberProperty(type="EMAIL", address=settings.alarm_email)
            ],
            threshold_expression=(
                '{"Dimensions": {"Key": "ANOMALY_TOTAL_IMPACT_ABSOLUTE",'
                ' "MatchOptions": ["GREATER_THAN_OR_EQUAL"], "Values": ["2"]}}'
            ),
        )

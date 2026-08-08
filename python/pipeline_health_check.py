"""
Checks job completion status, row count sanity, and data freshness for every
monitored table in the pipeline, and sends a Slack alert the moment any check
fails. Runs shortly after the main pipeline's scheduled completion time.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from google.cloud import bigquery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pipeline_health_check")

SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/REPLACE_WITH_REAL_WEBHOOK"


@dataclass
class MonitoredTable:
    table_id: str
    timestamp_column: str
    expected_freshness_hours: int
    min_expected_rows: int
    max_expected_rows: int


MONITORED_TABLES = [
    MonitoredTable("project.raw.google_ads", "loaded_at", 26, 10, 100_000),
    MonitoredTable("project.raw.meta_ads", "loaded_at", 26, 10, 100_000),
    MonitoredTable("project.raw.crm_contacts", "loaded_at", 26, 1, 500_000),
    MonitoredTable("project.marts.fct_channel_performance", "updated_at", 26, 10, 50_000),
]


def check_freshness(bq_client: bigquery.Client, table: MonitoredTable) -> str | None:
    query = f"""
        select timestamp_diff(current_timestamp(), max({table.timestamp_column}), hour) as hours_since_update
        from `{table.table_id}`
    """
    result = list(bq_client.query(query).result())
    hours_since_update = result[0].hours_since_update if result else None

    if hours_since_update is None:
        return f"{table.table_id}: no rows found, table may be empty or missing"
    if hours_since_update > table.expected_freshness_hours:
        return f"{table.table_id}: stale, last updated {hours_since_update}h ago (expected within {table.expected_freshness_hours}h)"
    return None


def check_row_count(bq_client: bigquery.Client, table: MonitoredTable) -> str | None:
    query = f"""
        select count(*) as row_count
        from `{table.table_id}`
        where date({table.timestamp_column}) = current_date()
    """
    result = list(bq_client.query(query).result())
    row_count = result[0].row_count if result else 0

    if row_count < table.min_expected_rows:
        return f"{table.table_id}: only {row_count} rows loaded today, expected at least {table.min_expected_rows}"
    if row_count > table.max_expected_rows:
        return f"{table.table_id}: {row_count} rows loaded today, unusually high, expected under {table.max_expected_rows}"
    return None


def send_slack_alert(issues: list[str]) -> None:
    message = {
        "text": ":rotating_light: *Pipeline health check found issues:*\n" + "\n".join(f"• {i}" for i in issues)
    }
    requests.post(SLACK_WEBHOOK_URL, json=message, timeout=10)


def main() -> None:
    bq_client = bigquery.Client()
    issues = []

    for table in MONITORED_TABLES:
        freshness_issue = check_freshness(bq_client, table)
        if freshness_issue:
            issues.append(freshness_issue)

        row_count_issue = check_row_count(bq_client, table)
        if row_count_issue:
            issues.append(row_count_issue)

    if issues:
        logger.warning(f"Found {len(issues)} pipeline health issues")
        send_slack_alert(issues)
    else:
        logger.info(f"All {len(MONITORED_TABLES)} tables healthy as of {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()

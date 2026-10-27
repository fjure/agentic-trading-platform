"""Privileged fixed Paper schema provisioning, without any financial seed.

Revision 0012 roles receive CONNECT only. Revision 0013 adds only the explicit
command-function manifest below, never table access or financial permissions.
Revision 0014 permits audited reconciliation writes to an existing basis.
Revision 0015 adds immutable LIMIT/quote/plan ports and bounded worker job and
decision/proposal rights. Revision 0016 adds explicitly attested lifecycle and
bounded execution writes; 0017/0018 add immutable human requests and guarded
outcomes. Revision 0019 adds function-only cancellation authority; 0020 adds five
API-only stored-data projections. Provisioning
itself creates no account, position, order, credential or entry approval.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from .config import PAPER_ROLES, ROLES

DATABASE = "lab_paper_runtime"
FILES = {
    "execution": "paper-execution.json",
    "worker": "paper-worker.json",
    "api": "paper-control.json",
    "watchdog": "paper-watchdog.json",
}
_FLAGS = "rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls"
_COMMAND_FUNCTIONS = {
    "atl_paper_control_api": (
        "public.paper_status(text)",
        "public.paper_operation(uuid)",
        "public.paper_rotation_begin(text,text,text)",
        "public.paper_rotation_complete(bigint)",
        "public.paper_enqueue(text,jsonb,text,text,text)",
    ),
    "atl_paper_execution": (
        "public.paper_claim(uuid,integer)",
        "public.paper_finish(uuid,uuid,bigint,jsonb,uuid,text)",
    ),
}
_RECONCILIATION_SELECT_0014 = (
    "accounts",
    "instruments",
    "positions",
    "portfolio_snapshots",
    "risk_account_context",
    "risk_decisions",
    "risk_reservations",
    "order_intents",
    "broker_orders",
    "execution_reports",
    "audit_events",
    "paper_reconciliation_baselines",
    "paper_order_accounting_bases",
    "paper_activity_evidence",
    "outbox_events",
    "execution_owners",
    "broker_asset_bindings",
    "broker_account_bindings",
    "broker_binding_proofs",
)
_RECONCILIATION_INSERT_0014 = (
    "audit_events",
    "portfolio_snapshots",
    "paper_activity_evidence",
    "paper_reconciliation_receipts",
    "paper_order_accounting_bases",
    "execution_reports",
    "outbox_events",
    "execution_owners",
)
_RECONCILIATION_UPDATE_0014 = {
    "accounts": "ledger_version",
    "risk_account_context": "version,safety_state,reconciliation_current,portfolio_snapshot_id,"
    "portfolio_version,actor_id,trace_id",
    "execution_owners": "owner_id,fencing_token,claimed_at,heartbeat_at,trace_id",
    "positions": "instrument_id,currency,quantity,average_price,realized_pnl,ledger_version",
    "risk_reservations": "proposal_id,risk_decision_id,currency,reserved_notional,state,version,"
    "created_at,trace_id,released_at",
    "broker_orders": "intent_id,client_order_id,broker_order_id,status,requested_quantity,"
    "filled_quantity,version,updated_at,trace_id",
    "outbox_events": "event_id",
}
_WORKER_SELECT_0015 = (
    "instruments",
    "market_bars",
    "evidence_bundles",
    "market_states",
    "positions",
    "position_versions",
    "portfolio_snapshots",
    "decisions",
    "trade_proposals",
    "agent_runs",
    "audit_events",
    "trading_profiles",
    "jobs",
    "job_recovery_checks",
    "outbox_events",
    "order_intents",
)
_WORKER_INSERT_0015 = (
    "jobs",
    "job_recovery_checks",
    "audit_events",
    "decisions",
    "trade_proposals",
    "agent_runs",
    "outbox_events",
)
_EXECUTION_SELECT_0015 = (
    "trade_proposals",
    "decisions",
    "market_states",
    "market_bars",
    "evidence_bundles",
    "position_versions",
    "risk_policies",
    "trading_profiles",
)
_EXECUTION_INSERT_0016 = (
    "market_bars",
    "evidence_bundles",
    "market_states",
    "broker_account_bindings",
    "broker_asset_bindings",
    "broker_binding_proofs",
    "risk_instrument_context",
    "broker_orders",
    "order_intents",
    "risk_reservations",
    "risk_decisions",
)
_EXECUTION_SELECT_COLUMNS_0016 = {
    "paper_reconciliation_receipts": "account_id,ready,portfolio_snapshot_id,portfolio_version,"
    "owner_id,fencing_token,observed_at",
    "publish_log": "cursor,event_id",
    "publish_cursor": "singleton,next_cursor",
}
_EXECUTION_INSERT_COLUMNS_0016 = {"publish_log": "cursor,event_id"}
_EXECUTION_UPDATE_0016 = {
    "publish_cursor": "next_cursor",
    "outbox_events": "published_at",
    "risk_account_context": "valid_until",
    "risk_reservations": "account_id",
    "order_intents": "account_id",
    "risk_instrument_context": "version,capability_version,broker_id,paper_confirmed,"
    "regular_session_confirmed,continuous_session_confirmed,market_order_confirmed,"
    "limit_order_confirmed,fractional_confirmed,quantity_step,price_tick,session_open,"
    "session_version,session_valid_until,valid_until,actor_id,trace_id",
}
_FUNCTIONS_0016 = {
    "atl_paper_control_api": (
        "public.paper_trading_profile_get(text)",
        "public.paper_trading_profile_put(text,text,text,jsonb)",
    ),
    "atl_paper_execution": (
        "public.paper_fee_authority()",
        "public.paper_recovery_fee_authority()",
        "public.paper_activation_read()",
        "public.paper_activation_guard(text,text)",
        "public.paper_activation_begin(uuid,uuid,bigint,jsonb)",
        "public.paper_restart_close(text,bigint)",
        "public.paper_daily_reference()",
        "public.paper_daily_append(text,bigint,integer,jsonb)",
        "public.paper_trading_profile_get(text)",
    ),
    "atl_paper_worker": ("public.paper_planning_active()", "public.paper_worker_heartbeat(text)"),
    "atl_paper_watchdog": ("public.paper_watchdog_poll(text)",),
}
_WORKER_UPDATE_0015 = {
    "jobs": "status,lease_owner,lease_until,fencing_token,attempt,not_before,heartbeat,"
    "error_code,completed_at",
    "outbox_events": "event_id",
}


def _memberships(connection, name):
    return connection.execute(
        "SELECT r.rolname,m.admin_option,m.inherit_option,m.set_option "
        "FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid "
        "JOIN pg_roles member ON member.oid=m.member WHERE member.rolname=%s",
        (name,),
    ).fetchall()


def _preflight(connection, directory):
    """Reject drift across all identities before any role/database mutation."""
    if directory.is_symlink() or not directory.is_dir():
        raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
    existing = {}
    for role, group in PAPER_ROLES.items():
        login = group + "_login"
        path = directory / FILES[role]
        if path.is_symlink():
            raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
        rows = {}
        for name, canlogin in ((group, False), (login, True)):
            row = connection.execute(
                f"SELECT {_FLAGS} FROM pg_roles WHERE rolname=%s", (name,)
            ).fetchone()
            rows[name] = row
            if row is not None and row != (canlogin, False, False, False, False, False, False):
                raise PermissionError("PAPER_ROLE_ATTRIBUTES_UNSAFE")
        if (rows[group] is None) != (rows[login] is None):
            raise PermissionError("PAPER_ROLE_CONFIGURATION_INCOMPLETE")
        if rows[login] is None:
            if path.exists():
                raise PermissionError("PRIVATE_CONFIGURATION_INCOMPLETE")
        else:
            if not path.is_file():
                raise PermissionError("PRIVATE_CONFIGURATION_INCOMPLETE")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
            if _memberships(connection, login) != [(group, False, False, True)]:
                raise PermissionError("PAPER_ROLE_MEMBERSHIP_UNSAFE")
            if _memberships(connection, group):
                raise PermissionError("PAPER_ROLE_MEMBERSHIP_UNSAFE")
            incoming = connection.execute(
                "SELECT member.rolname FROM pg_auth_members m "
                "JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles member ON member.oid=m.member "
                "WHERE r.rolname=%s",
                (group,),
            ).fetchall()
            if incoming != [(login,)]:
                raise PermissionError("PAPER_ROLE_MEMBERSHIP_UNSAFE")
            if connection.execute(
                "SELECT count(*) FROM pg_auth_members m "
                "JOIN pg_roles r ON r.oid=m.roleid WHERE r.rolname=%s",
                (login,),
            ).fetchone()[0]:
                raise PermissionError("PAPER_ROLE_MEMBERSHIP_UNSAFE")
            owners = connection.execute(
                "SELECT count(*) FROM pg_database WHERE datdba IN "
                "(SELECT oid FROM pg_roles WHERE rolname IN (%s,%s))",
                (login, group),
            ).fetchone()[0]
            if owners:
                raise PermissionError("PAPER_DATABASE_OWNER_UNSAFE")
        existing[role] = rows[login] is not None
    owner = connection.execute(
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (DATABASE,)
    ).fetchone()
    if owner is not None and owner != ("lab_migration",):
        raise PermissionError("PAPER_DATABASE_OWNER_MISMATCH")
    return existing, owner


def _create_identities(connection, directory, existing):
    from psycopg import sql

    from .provision import _write_new_secret

    for role, group in PAPER_ROLES.items():
        if existing[role]:
            continue
        login = group + "_login"
        password = secrets.token_urlsafe(48)
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(group))
        )
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(login), sql.Literal(password))
        )
        connection.execute(
            sql.SQL("GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE").format(
                sql.Identifier(group), sql.Identifier(login)
            )
        )
        _write_new_secret(directory / FILES[role], login, password)


def _verify_dormant_privileges(connection):
    """Enforce the exact manifest of the database's already-applied revision."""
    identities = tuple(PAPER_ROLES.values()) + tuple(
        name + "_login" for name in PAPER_ROLES.values()
    )
    version_table = connection.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]
    revisions = (
        connection.execute("SELECT version_num FROM public.alembic_version").fetchall()
        if version_table is not None
        else []
    )
    readiness_revision = (
        revisions[0][0] if revisions in (
            [("0030_readiness_storage",)], [("0031_readiness_jobs",)],
            [("0032_collection_control",)],
        ) else None
    )
    backtest_revision = (
        revisions[0][0]
        if revisions in ([("0028_backtest_coverage",)], [("0029_backtest_jobs",)])
        else "0029_backtest_jobs" if readiness_revision else None
    )
    if backtest_revision:
        # These additive revisions preserve every existing financial/research ACL.
        revisions = [("0027_research_quality",)]
    research_revision = revisions
    if revisions in (
        [("0021_research_foundation",)],
        [("0022_research_jobs",)],
        [("0023_research_paper_roles",)],
        [("0024_research_runtime_metadata",)],
        [("0025_research_operator_resume",)],
        [("0026_research_observability",)],
        [("0027_research_quality",)],
    ):
        # Legacy financial ACLs remain exactly the 0020 manifest.
        revisions = [("0020_paper_read_models",)]
    expected = set()
    if revisions in (
        [("0013_paper_commands",)],
        [("0014_paper_initial_basis",)],
        [("0015_paper_limit_policy",)],
        [("0016_paper_lifecycle",)],
        [("0017_paper_human_orders",)],
        [("0018_paper_human_finish_guard",)],
        [("0019_paper_cancellation",)],
        [("0020_paper_read_models",)],
    ):
        for group, functions in _COMMAND_FUNCTIONS.items():
            expected.add(("schema", "public", group, "USAGE", False))
            expected.update(("function", name, group, "EXECUTE", False) for name in functions)
        if revisions in (
            [("0014_paper_initial_basis",)],
            [("0015_paper_limit_policy",)],
            [("0016_paper_lifecycle",)],
            [("0017_paper_human_orders",)],
            [("0018_paper_human_finish_guard",)],
            [("0019_paper_cancellation",)],
            [("0020_paper_read_models",)],
        ):
            expected.add(
                (
                    "function",
                    "public.safety_latch(charactervarying,charactervarying,charactervarying,"
                    "charactervarying,charactervarying)",
                    "atl_paper_execution",
                    "EXECUTE",
                    False,
                )
            )
            for privilege, relations in (
                ("SELECT", _RECONCILIATION_SELECT_0014),
                ("INSERT", _RECONCILIATION_INSERT_0014),
            ):
                expected.update(
                    ("relation", "public." + name, "atl_paper_execution", privilege, False)
                    for name in relations
                )
            for relation, columns in _RECONCILIATION_UPDATE_0014.items():
                expected.update(
                    (
                        "column",
                        "public." + relation + "." + column,
                        "atl_paper_execution",
                        "UPDATE",
                        False,
                    )
                    for column in columns.split(",")
                )
            expected.add(
                (
                    "function",
                    "public.paper_prepare(uuid,uuid,bigint,jsonb)",
                    "atl_paper_execution",
                    "EXECUTE",
                    False,
                )
            )
            expected.add(
                (
                    "function",
                    "public.paper_daily_read(text,bigint)",
                    "atl_paper_execution",
                    "EXECUTE",
                    False,
                )
            )
            expected.add(
                (
                    "function",
                    "public.paper_initial_history(text)",
                    "atl_paper_execution",
                    "EXECUTE",
                    False,
                )
            )
            expected.add(
                (
                    "function",
                    "public.paper_cost_basis_read(text,integer)",
                    "atl_paper_execution",
                    "EXECUTE",
                    False,
                )
            )
            expected.add(
                (
                    "function",
                    "public.paper_cost_basis_append(text,integer,numeric,numeric,text,text)",
                    "atl_paper_execution",
                    "EXECUTE",
                    False,
                )
            )
        if revisions in (
            [("0015_paper_limit_policy",)],
            [("0016_paper_lifecycle",)],
            [("0017_paper_human_orders",)],
            [("0018_paper_human_finish_guard",)],
            [("0019_paper_cancellation",)],
            [("0020_paper_read_models",)],
        ):
            expected.update(
                ("relation", "public." + name, "atl_paper_execution", "SELECT", False)
                for name in _EXECUTION_SELECT_0015
            )
            expected.add(("schema", "public", "atl_paper_worker", "USAGE", False))
            expected.add(
                (
                    "relation",
                    "public.risk_instrument_context",
                    "atl_paper_execution",
                    "SELECT",
                    False,
                )
            )
            for group, functions in {
                "atl_paper_worker": (
                    "public.paper_planner_inputs(text)",
                    "public.paper_limit_policy()",
                    "public.paper_planner_record(text,text,text,text,bigint)",
                ),
                "atl_paper_execution": (
                    "public.paper_limit_policy()",
                    "public.paper_planner_inputs(text)",
                    "public.paper_limit_plan_read(text)",
                    "public.paper_quote_append(text,integer,text,bigint,jsonb)",
                ),
            }.items():
                expected.update(("function", name, group, "EXECUTE", False) for name in functions)
            for privilege, relations in (
                ("SELECT", _WORKER_SELECT_0015),
                ("INSERT", _WORKER_INSERT_0015),
            ):
                expected.update(
                    ("relation", "public." + name, "atl_paper_worker", privilege, False)
                    for name in relations
                )
            for relation, columns in _WORKER_UPDATE_0015.items():
                expected.update(
                    (
                        "column",
                        "public." + relation + "." + column,
                        "atl_paper_worker",
                        "UPDATE",
                        False,
                    )
                    for column in columns.split(",")
                )
        if revisions in (
            [("0016_paper_lifecycle",)],
            [("0017_paper_human_orders",)],
            [("0018_paper_human_finish_guard",)],
            [("0019_paper_cancellation",)],
            [("0020_paper_read_models",)],
        ):
            expected.add(("schema", "public", "atl_paper_watchdog", "USAGE", False))
            for group, functions in _FUNCTIONS_0016.items():
                expected.update(("function", name, group, "EXECUTE", False) for name in functions)
            expected.update(
                ("relation", "public." + name, "atl_paper_execution", "INSERT", False)
                for name in _EXECUTION_INSERT_0016
            )
            for privilege, relations in (
                ("SELECT", _EXECUTION_SELECT_COLUMNS_0016),
                ("INSERT", _EXECUTION_INSERT_COLUMNS_0016),
            ):
                for relation, columns in relations.items():
                    expected.update(
                        (
                            "column",
                            "public." + relation + "." + column,
                            "atl_paper_execution",
                            privilege,
                            False,
                        )
                        for column in columns.split(",")
                    )
            for relation, columns in _EXECUTION_UPDATE_0016.items():
                expected.update(
                    (
                        "column",
                        "public." + relation + "." + column,
                        "atl_paper_execution",
                        "UPDATE",
                        False,
                    )
                    for column in columns.split(",")
                )
        if revisions in (
            [("0017_paper_human_orders",)],
            [("0018_paper_human_finish_guard",)],
            [("0019_paper_cancellation",)],
            [("0020_paper_read_models",)],
        ):
            expected.update(
                ("function", name, "atl_paper_execution", "EXECUTE", False)
                for name in (
                    "public.paper_human_request_read(text)",
                    "public.paper_human_proposal_read(text)",
                    "public.paper_human_proposal_create(uuid,uuid,bigint,text,text,timestampwithtimezone)",
                    "public.paper_human_operation_read(uuid)",
                    "public.paper_human_finish(uuid,uuid,bigint)",
                )
            )
        if revisions in ([("0019_paper_cancellation",)], [("0020_paper_read_models",)]):
            expected.update(
                ("function", name, "atl_paper_execution", "EXECUTE", False)
                for name in (
                    "public.paper_cancel_read(uuid)",
                    "public.paper_cancel_claim(text,bigint,uuid,integer)",
                    "public.paper_cancel_begin_lookup(uuid,uuid,bigint,text,bigint)",
                    "public.paper_cancel_begin_delete(uuid,uuid,bigint,text,bigint,uuid)",
                    "public.paper_cancel_record(uuid,uuid,bigint,text,bigint,jsonb)",
                    "public.paper_cancel_finish(uuid,uuid,bigint,text,bigint)",
                )
            )
        if revisions == [("0020_paper_read_models",)]:
            expected.update(
                ("function", name, "atl_paper_control_api", "EXECUTE", False)
                for name in (
                    "public.paper_snapshot(text)",
                    "public.paper_trace(text)",
                    "public.paper_orders(integer,text,text)",
                    "public.paper_fills(integer,text,text)",
                    "public.paper_operations(integer,text,text)",
                )
            )
    elif revisions not in ([], [("0012_trading_profile",)]):
        raise PermissionError("PAPER_PRIVILEGE_MANIFEST_UNSUPPORTED")
    if research_revision in (
        [("0023_research_paper_roles",)],
        [("0024_research_runtime_metadata",)],
        [("0025_research_operator_resume",)],
        [("0026_research_observability",)],
        [("0027_research_quality",)],
    ):
        immutable = (
            "version_state",
            "instruments",
            "symbol_mappings",
            "bar_revisions",
            "evidence",
            "calendar_versions",
            "job_projection",
        )
        for group in ("atl_paper_execution", "atl_paper_worker", "atl_paper_control_api"):
            expected.add(("schema", "research", group, "USAGE", False))
            expected.update(
                ("relation", "research." + name, group, "SELECT", False) for name in immutable
            )
        expected.update(
            ("relation", "research." + name, "atl_paper_execution", "INSERT", False)
            for name in immutable
            if name not in {"version_state", "job_projection"}
        )
        expected.update(
            ("relation", "research.ingestion_jobs", "atl_paper_execution", p, False)
            for p in ("SELECT", "INSERT", "UPDATE")
        )
        expected.update(
            ("relation", "research.page_receipts", "atl_paper_execution", p, False)
            for p in ("SELECT", "INSERT")
        )
        expected.add(
            ("function", "research.reserve_version()", "atl_paper_execution", "EXECUTE", False)
        )
        expected.add(
            (
                "function",
                "research.request_job(uuid,text,text,text,uuid,timestampwithtimezone,"
                "timestampwithtimezone,text,text,text)",
                "atl_paper_control_api",
                "EXECUTE",
                False,
            )
        )
        if research_revision != [("0023_research_paper_roles",)]:
            expected.update(
                ("relation", "research.source_status", role, "SELECT", False)
                for role in ("atl_paper_control_api", "atl_paper_worker", "atl_paper_execution")
            )
            expected.add(
                ("relation", "research.source_status", "atl_paper_execution", "UPDATE", False)
            )
            expected.add(
                (
                    "function",
                    "research.credential_generation()",
                    "atl_paper_execution",
                    "EXECUTE",
                    False,
                )
            )
        if research_revision in (
            [("0025_research_operator_resume",)],
            [("0026_research_observability",)],
            [("0027_research_quality",)],
        ):
            expected.add(
                (
                    "function",
                    "research.resume_job(uuid,text,text,text)",
                    "atl_paper_control_api",
                    "EXECUTE",
                    False,
                )
            )
        if research_revision in ([("0026_research_observability",)], [("0027_research_quality",)]):
            expected.update(
                ("relation", "research.job_audit", role, "SELECT", False)
                for role in ("atl_paper_control_api", "atl_paper_worker", "atl_paper_execution")
            )
        if research_revision == [("0027_research_quality",)]:
            expected.update(
                ("relation", "research.quality_observations", role, "SELECT", False)
                for role in ("atl_paper_control_api", "atl_paper_worker", "atl_paper_execution")
            )
            expected.add(
                (
                    "relation",
                    "research.quality_observations",
                    "atl_paper_execution",
                    "INSERT",
                    False,
                )
            )
    if backtest_revision:
        for role in ("atl_paper_worker", "atl_paper_control_api"):
            expected.add(("schema", "backtest", role, "USAGE", False))
            expected.add(("relation", "backtest.corporate_action_coverage", role, "SELECT", False))
        if backtest_revision == "0029_backtest_jobs":
            expected.update(
                ("relation", "backtest." + name, "atl_paper_control_api", "SELECT", False)
                for name in ("runs", "manifests", "fold_results", "results", "cursor_state")
            )
            expected.add(
                (
                    "function",
                    "backtest.request_run(uuid,text,text,jsonb,text,text,text)",
                    "atl_paper_control_api",
                    "EXECUTE",
                    False,
                )
            )
    if readiness_revision:
        for role in ("atl_paper_control_api", "atl_paper_execution"):
            expected.update(
                ("relation", "research." + name, role, "SELECT", False)
                for name in ("trust_version_state", "trust_decisions", "evidence_revisions",
                             "import_receipts", "commit_confirmations", "collection_plans",
                             "plan_revisions", "due_jobs", "observation_receipts")
            )
        for privilege, names in (
            ("INSERT", ("collection_plans", "due_jobs", "plan_revisions", "observation_receipts")),
            ("UPDATE", ("collection_plans", "due_jobs")),
        ):
            expected.update(
                ("relation", "research." + name, "atl_paper_execution", privilege, False)
                for name in names
            )
        expected.add(("function", "research.confirm_receipt(uuid,text)",
                      "atl_paper_execution", "EXECUTE", False))
        expected.update(
            ("relation", "backtest." + name, "atl_paper_control_api", "SELECT", False)
            for name in ("readiness_jobs", "readiness_reports", "readiness_findings")
        )
        if readiness_revision in ("0031_readiness_jobs", "0032_collection_control"):
            expected.add(("relation", "backtest.readiness_bindings",
                          "atl_paper_control_api", "SELECT", False))
            expected.add(("function",
                          "backtest.request_readiness(uuid,text,text,jsonb,text,text,text,text)",
                          "atl_paper_control_api", "EXECUTE", False))
        if readiness_revision == "0032_collection_control":
            expected.add(("relation", "research.collection_commands",
                          "atl_paper_control_api", "SELECT", False))
            expected.update(
                ("function", name, "atl_paper_control_api", "EXECUTE", False)
                for name in ("research.collection_plan_projection(uuid)",
                             "research.set_collection_plan(uuid,boolean,bigint,text,text,text,jsonb)")
            )
    # Empty/unmigrated databases and 0012 permit no direct object ACLs. Inspect
    # columns and login grantees too; initial SET ROLE is not a permission bound.
    actual = connection.execute(
        "WITH acls(kind,name,acl) AS ("
        "SELECT 'schema',nspname::text,nspacl FROM pg_namespace UNION ALL "
        "SELECT 'relation',n.nspname||'.'||c.relname,c.relacl FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace UNION ALL "
        "SELECT 'column',n.nspname||'.'||c.relname||'.'||a.attname,a.attacl "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace UNION ALL "
        "SELECT 'function',n.nspname||'.'||p.proname||'('||"
        "replace(oidvectortypes(p.proargtypes),' ','')||')',p.proacl FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace) "
        "SELECT kind,name,r.rolname,x.privilege_type,x.is_grantable "
        "FROM acls CROSS JOIN LATERAL aclexplode(acl) x JOIN pg_roles r ON r.oid=x.grantee "
        "WHERE r.rolname=ANY(%s)",
        (list(identities),),
    ).fetchall()
    if set(actual) != expected:
        raise PermissionError("PAPER_OBJECT_PRIVILEGES_UNSAFE")
    owned = connection.execute(
        "WITH identities AS (SELECT oid FROM pg_roles WHERE rolname=ANY(%s)), "
        "owned AS (SELECT 1 FROM pg_namespace WHERE nspowner IN (SELECT oid FROM identities)) "
        "SELECT EXISTS(SELECT 1 FROM owned) OR "
        "EXISTS(SELECT 1 FROM pg_class WHERE relowner IN (SELECT oid FROM identities)) OR "
        "EXISTS(SELECT 1 FROM pg_proc WHERE proowner IN (SELECT oid FROM identities)) OR "
        "EXISTS(SELECT 1 FROM pg_type WHERE typowner IN (SELECT oid FROM identities))",
        (list(identities),),
    ).fetchone()[0]
    if owned:
        raise PermissionError("PAPER_OBJECT_PRIVILEGES_UNSAFE")
    for identity in identities:
        if connection.execute(
            "SELECT has_database_privilege(%s,%s,'CREATE') OR has_database_privilege(%s,%s,'TEMP')",
            (identity, DATABASE, identity, DATABASE),
        ).fetchone()[0]:
            raise PermissionError("PAPER_DDL_PRIVILEGES_UNSAFE")


def _confine_schema(connection):
    """Remove legacy ACLs from this schema; never grant normal-role membership."""
    from psycopg import sql

    names = sql.SQL(",").join([sql.Identifier(name) for name in ROLES.values()])
    for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        connection.execute(
            sql.SQL(f"REVOKE ALL ON ALL {kind} IN SCHEMA public FROM PUBLIC, {{}}").format(names)
        )
    # Table-level REVOKE does not remove the existing column-level grants.
    for table, column in connection.execute(
        "SELECT c.relname,a.attname FROM pg_attribute a "
        "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND a.attacl IS NOT NULL"
    ).fetchall():
        connection.execute(
            sql.SQL("REVOKE ALL ({}) ON public.{} FROM PUBLIC, {}").format(
                sql.Identifier(column),
                sql.Identifier(table),
                names,
            )
        )
    connection.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM PUBLIC, {}").format(names))


def provision_paper(common: dict, directory: Path):
    import psycopg
    from alembic import command
    from alembic.config import Config
    from psycopg import sql
    from sqlalchemy import URL, create_engine

    with psycopg.connect(dbname="lab", autocommit=True, **common) as connection:
        connection.execute("SET log_statement='none'")
        connection.execute("SET log_min_error_statement='panic'")
        existing, owner = _preflight(connection, directory)
        if owner is not None:
            with psycopg.connect(dbname=DATABASE, **common) as paper_connection:
                _verify_dormant_privileges(paper_connection)
        with connection.transaction():
            _create_identities(connection, directory, existing)
        if owner is None:
            # The migration superuser can connect while ordinary logins cannot.
            connection.execute(
                "CREATE DATABASE lab_paper_runtime OWNER lab_migration CONNECTION LIMIT 0"
            )
        connection.execute("REVOKE ALL ON DATABASE lab_paper_runtime FROM PUBLIC")
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=common["user"],
            password=common["password"],
            host="postgres",
            port=5432,
            database=DATABASE,
        ),
        hide_parameters=True,
        echo=False,
        connect_args={"connect_timeout": 5},
    )
    try:
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[1] / "control_api/migrations")
        )
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with psycopg.connect(dbname=DATABASE, **common) as connection:
            _confine_schema(connection)
            _verify_dormant_privileges(connection)
            for role, group in PAPER_ROLES.items():
                login = group + "_login"
                for identity in (login, group):
                    unsafe = connection.execute(
                        "SELECT has_database_privilege(%s,%s,'CREATE') OR "
                        "has_database_privilege(%s,%s,'TEMP') OR "
                        "has_schema_privilege(%s,'public','CREATE')",
                        (identity, DATABASE, identity, DATABASE, identity),
                    ).fetchone()[0]
                    if unsafe:
                        raise PermissionError("PAPER_DDL_PRIVILEGES_UNSAFE")
                connection.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(DATABASE),
                        sql.Identifier(login),
                    )
                )
                for other in ("lab_runtime", "lab_demo"):
                    allowed = connection.execute(
                        "SELECT has_database_privilege(%s,%s,'CONNECT')", (login, other)
                    ).fetchone()[0]
                    if allowed:
                        raise PermissionError("PAPER_DATABASE_ISOLATION_FAILED")
                path = directory / FILES[role]
                os.chown(path, 10001, 10001, follow_symlinks=False)
                os.chmod(path, 0o600, follow_symlinks=False)
            connection.execute("ALTER DATABASE lab_paper_runtime CONNECTION LIMIT -1")
    finally:
        engine.dispose()

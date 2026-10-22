"""Exact isolated Paper authority while retaining the existing lab Execution role."""

from sqlalchemy import text


def is_execution(connection):
    database, role, login = connection.execute(
        text("SELECT current_database(),current_user,session_user")
    ).one()
    if database == "lab_paper_runtime":
        return role == "atl_paper_execution" and login == "atl_paper_execution_login"
    return role == "atl_execution"

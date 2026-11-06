import os
import uuid

from sqlalchemy import text


def configured_node_id() -> str | None:
    node_id = os.getenv("NODE_ID")
    if node_id is None or not node_id.strip():
        return None
    try:
        return str(uuid.UUID(node_id))
    except ValueError as exc:
        raise ValueError("NODE_ID must be a valid UUID") from exc


def current_node_id(context) -> str:
    return node_id_for_connection(context.connection)


def node_id_for_connection(connection) -> str:
    configured_id = configured_node_id()
    persisted_id = connection.execute(
        text("SELECT node_id FROM node_identity ORDER BY id LIMIT 1")
    ).scalar_one_or_none()
    if persisted_id is not None and configured_id is not None and persisted_id != configured_id:
        raise ValueError("NODE_ID does not match the persisted node identity")
    return persisted_id or configured_id or "unconfigured"

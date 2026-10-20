"""Internal-only service entry point, without access/body logging."""

import logging

import uvicorn

from services.demo.app import create_app

logging.disable(logging.CRITICAL)
uvicorn.run(create_app(), host="0.0.0.0", port=8010, access_log=False, log_config=None)

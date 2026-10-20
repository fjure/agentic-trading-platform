"""No inherited provider configuration, access logs, telemetry or debug output."""

import logging


def main():
    logging.disable(logging.CRITICAL)
    try:
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(), host="0.0.0.0", port=8011, access_log=False,
                    log_config=None, log_level="critical", server_header=False,
                    proxy_headers=False)
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

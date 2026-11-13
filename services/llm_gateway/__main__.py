"""Start the separately isolated, disabled-by-default gateway process."""

import uvicorn

from .app import create_gateway_app, create_provider_gateway_app


def main(*, journal_path=None, auth=None, credentials=None, capabilities=()) -> None:
    if journal_path is None and auth is None and credentials is None and not capabilities:
        app = create_gateway_app()
    else:
        if journal_path is None or auth is None or credentials is None or not capabilities:
            raise ValueError("incomplete provider gateway composition")
        app = create_provider_gateway_app(
            journal_path=journal_path, auth=auth, credentials=credentials,
            capabilities=capabilities,
        )
    uvicorn.run(
        app, host="0.0.0.0", port=8080, access_log=False, proxy_headers=False,
    )


if __name__ == "__main__":
    main()

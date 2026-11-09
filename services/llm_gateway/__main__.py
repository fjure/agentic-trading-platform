"""Start the separately isolated, disabled-by-default gateway process."""

import uvicorn

from .app import create_gateway_app


def main() -> None:
    uvicorn.run(create_gateway_app(), host="0.0.0.0", port=8080, access_log=False)


if __name__ == "__main__":
    main()

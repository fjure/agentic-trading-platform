"""Exercise the canonical research Compose boundaries against a synthetic TLS provider."""

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
LAYERS = (
    "",
    ".paper",
    ".research-control",
    ".research-setup",
    ".research-authority",
    ".research-dispatch",
)
GATEWAY = "research-llm-gateway"
SUPERVISOR = "research-supervisor"
EGRESS = "research-provider-egress"
SETUP = "research-setup"
EMULATOR = "arp09-emulator"
DB_CANARY = "arp09-db-canary"
SCRIPT = "/opt/lab/arp09_fixture.py"
RPC = "research_gateway_rpc"
EGRESS_RPC = "research_provider_egress_rpc"
OUTBOUND = "research_provider_outbound"


def write_fixture(scratch):
    issuer_key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ARP-09 ephemeral fixture CA")])
    host = "api.openai.com"
    endpoint = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(UTC)

    def certificate(subject, public_key):
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
        )

    ca = (
        certificate(issuer, issuer_key.public_key())
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(issuer_key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(issuer_key, hashes.SHA256())
    )
    endpoint_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        certificate(endpoint, endpoint_key.public_key())
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
        .sign(issuer_key, hashes.SHA256())
    )

    for path, data in {
        "provider.pem": ca.public_bytes(serialization.Encoding.PEM),
        "endpoint.pem": leaf.public_bytes(serialization.Encoding.PEM),
        "provider.key": endpoint_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        "dispatch-key": bytes(range(32)),
        "profile.json": b'{"fixture":"ARP-09"}',
        "unused": b"synthetic fixture placeholder",
    }.items():
        target = scratch / path
        target.write_bytes(data)
        # The private scratch directory blocks other host identities. Individual
        # files are readable by the non-root test users only after being mounted.
        target.chmod(0o444)


def verify_topology(model, *, fixture):
    services = model["services"]
    networks = model["networks"]

    def peers(network):
        return {
            name for name, service in services.items() if network in service.get("networks", {})
        }

    assert peers(RPC) == {GATEWAY, SUPERVISOR}
    assert peers(EGRESS_RPC) == {GATEWAY, EGRESS}
    assert peers(OUTBOUND) == ({EGRESS, EMULATOR} if fixture else {EGRESS})
    assert set(services[GATEWAY]["networks"]) == {RPC, EGRESS_RPC}
    assert set(services[EGRESS]["networks"]) == {EGRESS_RPC, OUTBOUND}
    assert networks[RPC].get("internal") and networks[EGRESS_RPC].get("internal")
    assert networks[OUTBOUND].get("internal", False) == fixture
    if fixture:
        assert all(
            networks[network].get("internal")
            for name in (GATEWAY, EGRESS, SUPERVISOR, SETUP, EMULATOR, DB_CANARY)
            for network in services[name]["networks"]
        )
    for name in (GATEWAY, EGRESS):
        service = services[name]
        assert service.get("read_only") and service.get("cap_drop") == ["ALL"]
        assert not service.get("ports") and not service.get("privileged")
    for volume, expected in {
        "research_dispatch_journal": {
            GATEWAY: False,
            **({"arp09-journal-init": False} if fixture else {}),
        },
        "research_credentials": {"research-setup-volumes": False, SETUP: False, GATEWAY: True},
        "research_setup_socket": {
            "research-setup-volumes": False,
            SETUP: False,
            "control-api": True,
        },
    }.items():
        actual = {
            name: mount.get("read_only", False)
            for name, service in services.items()
            for mount in service.get("volumes", [])
            if mount.get("source") == volume
        }
        assert actual == expected, volume

    assert {mount["source"] for mount in services[GATEWAY]["volumes"]} == {
        "research_credentials", "research_dispatch_journal",
    }, "unexpected gateway volume"
    assert {mount["source"] for mount in services[GATEWAY]["secrets"]} == {
        "research_provider_dispatch_key",
    }
    assert not services[EGRESS].get("volumes") and not services[EGRESS].get("secrets")
    assert model["volumes"]["research_dispatch_journal"].get("external", False) != fixture
    if not fixture:
        assert not any(name.startswith("arp09_") for name in model["configs"])
        assert services[GATEWAY]["command"] == ["python", "-m", "services.llm_gateway"]
        assert services[EGRESS]["environment"]["ATL_RESEARCH_EGRESS_ENABLED"] == "0"


def runtime_model(model):
    """Start only the roles in the isolated proof from the verified Compose render."""
    names = (
        GATEWAY,
        EGRESS,
        SUPERVISOR,
        SETUP,
        EMULATOR,
        "research-setup-volumes",
        "arp09-journal-init",
        DB_CANARY,
    )
    services = {name: model["services"][name] for name in names}
    # The signed-admission fixture has no database. The canonical supervisor
    # dependency is still present in the separately checked production model.
    services[SUPERVISOR].pop("depends_on")
    references = {
        "networks": set().union(*(service.get("networks", {}) for service in services.values())),
        **{
            resource: {
                mount["source"]
                for service in services.values()
                for mount in service.get(resource, [])
            }
            for resource in ("configs", "secrets", "volumes")
        },
    }
    runtime = {
        "name": model["name"],
        "services": services,
        **{
            resource: {name: model[resource][name] for name in used}
            for resource, used in references.items()
        },
    }
    # Docker Desktop's default pools may already be occupied. Allocate small,
    # isolated test subnets from the RFC 2544 address block.
    pool = int(model["name"].rsplit("-", 1)[1][:3], 16) % 512
    for index, network in enumerate(sorted(runtime["networks"])):
        subnet = f"198.{18 + pool // 256}.{pool % 256}.{index * 32}/27"
        runtime["networks"][network]["ipam"] = {"config": [{"subnet": subnet}]}
    return runtime


def main():
    project = "atl-arp09-" + uuid4().hex[:12]
    base = ["docker", "compose", "-p", project]
    for layer in LAYERS:
        base += ["-f", f"infra/compose/compose{layer}.yaml"]
    base += ["--profile", "*"]

    production = subprocess.check_output(
        [*base, "config", "--format", "json"], cwd=ROOT, timeout=25
    )
    verify_topology(json.loads(production), fixture=False)

    with TemporaryDirectory(prefix="atl-arp09-") as directory:
        write_fixture(Path(directory))
        environment = dict(
            os.environ,
            ARP09_SCRATCH=Path(directory).as_posix(),
            ARP09_TEST_IMAGE=f"{project}:fixture",
        )
        compose = [*base, "-f", "tests/security/compose.research-production-fixture.yaml"]

        def run(*args, timeout=45):
            subprocess.run(
                [*compose, *args], check=True, cwd=ROOT, env=environment, timeout=timeout
            )

        def one_off(service, *command):
            run("run", "-T", "--rm", "--no-deps", "--pull", "never", service, *command)

        def execute(service, *args):
            run("exec", "-T", service, "python", SCRIPT, *args)

        rendered = subprocess.check_output(
            [*compose, "config", "--format", "json"],
            cwd=ROOT,
            env=environment,
            timeout=25,
        )
        model = json.loads(rendered)
        verify_topology(model, fixture=True)
        runtime = Path(directory) / "compose.json"
        runtime.write_text(json.dumps(runtime_model(model)), encoding="utf-8")
        compose = ["docker", "compose", "-p", project, "-f", str(runtime), "--profile", "*"]
        attempt = str(uuid4())
        run("build", GATEWAY, timeout=270)
        try:
            one_off("research-setup-volumes")
            one_off("arp09-journal-init")
            one_off(SETUP, "python", SCRIPT, "setup", "seed")
            run(
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                EMULATOR,
                DB_CANARY,
                EGRESS,
                SUPERVISOR,
            )
            run("up", "-d", "--no-deps", "--no-build", "--pull", "never", GATEWAY)
            execute(SUPERVISOR, "exercise", "initial", attempt)
            execute(GATEWAY, "gateway-checks")
            execute(SUPERVISOR, "check-role", "supervisor")
            execute(EGRESS, "check-role", "egress")
            one_off(SETUP, "python", SCRIPT, "setup", "revoke")
            run("restart", "--no-deps", GATEWAY)
            execute(SUPERVISOR, "exercise", "restarted", attempt)
            execute(GATEWAY, "verify-emulator")
        finally:
            run("down", "--volumes")
            subprocess.run(
                ["docker", "image", "rm", environment["ARP09_TEST_IMAGE"]],
                cwd=ROOT,
                timeout=25,
                check=False,
                capture_output=True,
            )
    print(
        "PASS ARP-09 Compose: isolated TLS, wrong CA/SNI, redirect, "
        "mount boundaries, replay and revocation"
    )


if __name__ == "__main__":
    main()

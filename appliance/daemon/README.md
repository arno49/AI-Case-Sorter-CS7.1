# cs71d

`cs71d` is the private machine-control daemon for the Raspberry Pi appliance.
This workspace currently provides the package boundary and strict configuration
validation. Serial ownership, scheduling, persistence, and the Unix-socket API
arrive in later roadmap tasks.

The package depends on the repository's `cs71_protocol` implementation and does
not duplicate framing or recovery logic.

From the repository root:

```sh
python -m pip install --require-hashes -r appliance/daemon/requirements-dev.txt
python -m pip install --no-build-isolation -e ./host -e ./appliance/daemon
(cd appliance/daemon && ruff format --check . && ruff check . && mypy && pytest)
cs71d --check-config appliance/daemon/config/development.toml
```

With no config argument, `--check-config` validates a development profile using
the simulator backend and no device path. The production example accepts only
the stable `/dev/cs71` identity; the scaffold never opens it.

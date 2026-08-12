# cs71-vision

The third appliance service (`docs/architecture/adr/0013-vision-classifier-service-and-hybrid-autonomy.md`):
camera capture and, in later PI-VISION tasks, classification. It never opens
the serial device and never bypasses `cs71d` — it only ever asks `cs71d` to
act, the same way `cs71-web` does today.

PI-VISION-001 shipped the camera abstraction and capture loop. PI-VISION-002
added the self-labeled dataset: `cs71-vision` reads `cs71d`'s own operation
history (never `machine.db` directly) to learn which slot a completed sort
actually reached, and pairs that with a frame it already captured.
PI-VISION-003 gives `cs71-vision` its first HTTP surface of its own — a
single read-only resource the web BFF queries for dataset review — so it now
talks to the web side, but only ever to answer that one read. It still
classifies nothing; that is PI-VISION-004 onward.

## Self-labeled dataset (PI-VISION-002)

The whole point: build a labeled training set for free, from ordinary
operator-driven sorting, with zero extra effort from anyone.

- `cs71vision.daemon_client.DaemonClient` is a minimal, **read-only** HTTP
  client for `cs71d`'s private Unix-socket API — `GET /v1/operations` only,
  authenticated with its own service credential
  (`/etc/cs71-vision/service-token`, a third copy of the same shared secret
  `cs71d`/`cs71-web` already use). It reads which slot a `SUCCEEDED` sort
  operation reached from `terminal_fields.slot` — the *only* place that
  value is exposed; `cs71d`'s operation records carry no other durable,
  externally readable trace of "which slot" (this PR is what taught the
  daemon's own contract to expose it, additively, since nothing needed it
  before).
- `cs71vision.correlator.FrameBuffer` keeps the last 64 captured frames;
  `Correlator.poll_once()` asks `cs71d` for newly succeeded sorts, finds the
  frame captured at-or-before each one's `created_at`, and hands the pair to
  the dataset store. A success with no matching frame is discarded, not
  guessed at — this is deliberately conservative rather than approximate.
- `cs71vision.dataset.DatasetStore` owns `vision.db` exclusively, the same
  separate-ownership rule `machine.db`/`web.db` already follow: forward-only
  checksummed migrations, WAL mode, owner-only file mode, the same shape as
  `cs71d.journal.Journal`. `operation_id` is the primary key, so polling the
  same `cs71d` operation twice is a no-op, never a duplicate.
- `cs71vision.runtime.CorrelationLoop` polls on its own thread and its own
  interval (2s by default), independent of the capture interval — it
  reconciles against `cs71d`'s durable record, it does not drive capture.

Wiring is additive and optional: a config with no `daemon_service_token_path`
runs exactly like PI-VISION-001's plain capture-only mode. Set it, and
`cs71-vision` starts correlating against `cs71d` automatically.

## Dataset api (PI-VISION-003)

Lets an operator see training readiness before training is offered, not
after (ADR-0013). `cs71vision.api.DatasetApiServer` serves one resource,
`GET /v1/dataset`, on its own Unix domain socket — mirroring `cs71d.api`'s
shape (bearer auth against the same shared service credential, stale-socket
reclaim on start, `AF_UNIX`-only) at a fraction of its surface: one route, no
commands, no event stream. The response is per-class counts against the
configured floor:

```json
{
  "api_version": "v1",
  "minimum_examples_per_class": 40,
  "classes": [
    { "slot": 3, "count": 52, "eligible": true },
    { "slot": 5, "count": 12, "eligible": false }
  ],
  "training_ready": true
}
```

A "class" is the slot the operator sorted into — the same self-labeling
ADR-0013 describes, so this introduces no second labeling concept. The floor
is `minimum_examples_per_class` in `cs71vision.config.VisionConfig` (default
40, configurable per installation); a class below it is `eligible: false`,
never merely absent. `training_ready` is true once at least one class clears
the floor.

Same gate as correlation: `cs71vision.runtime.build_api_server` returns
`None` when `daemon_service_token_path` is unset — no credential to
authenticate a caller against, and nothing in the dataset ever gets
populated either, so capture-only mode exposes no api. It opens its own
`DatasetStore` connection, independent of the correlation loop's — safe
under the WAL mode `DatasetStore.open` already enforces, and it keeps this
server's lifecycle free of any dependency on whether correlation happens to
be running.

The web BFF's side is `appliance/web/src/lib/server/vision/client.ts` — a
small hand-typed client, not a second generated OpenAPI contract: one
resource does not justify that weight yet (see PI-VISION-003's backlog
entry for the full reasoning, and revisit if PI-VISION-004/005 grow this
surface). It reuses the daemon module's own Unix-socket transport and
credential reader, both already generic infrastructure, and reads through
`cs71-web`'s existing copy of the shared service credential — the same
secret `cs71-web` already presents to `cs71d`, no new credential to
provision. The operator-facing page is `/dataset`
(`appliance/web/src/routes/dataset/`), gated by the same `machine.read`
capability `/system` uses.

## Camera

`cs71vision.camera.Camera` is a two-method protocol: `read()` returns a
PNG-encoded `Frame`, `close()` releases the source. Two implementations:

- `FixtureCamera` — deterministic, dependency-free, the same evidence role
  `appliance/daemon/src/cs71d/simulator` plays for the serial protocol.
  Every frame is a solid-color image whose value advances with an internal
  counter, never wall-clock time or randomness. This is what this
  workspace's own tests and CI actually exercise.
- `V4L2Camera` — a real camera, opened through OpenCV's V4L2 backend
  (`cv2.VideoCapture(path, cv2.CAP_V4L2)`), not a hand-rolled ioctl
  implementation. That trade-off is deliberate: reconstructing the V4L2
  mmap-streaming protocol's exact kernel-ABI struct layouts from memory,
  with no real V4L2 device anywhere in this development or CI environment
  to catch a wrong field offset against, is a correctness risk this
  workspace should not carry. Every small "V4L2 Python bindings" package on
  PyPI that exists to avoid writing that protocol by hand is itself
  GPL-licensed (a direct transcription of the GPL `linux/videodev2.h`
  kernel header), which does not fit this workspace's MIT license. OpenCV
  is Apache-2.0 (the `opencv-python-headless` packaging itself is MIT),
  battle-tested against exactly this class of UVC/V4L2 webcam — the camera
  hardware already specified in `3DModels/Classifier/CameraV2` is a plain
  USB VGA UVC module — and removes both the correctness risk and the
  licensing question in one move.

`V4L2Camera` is unit-tested only against its own refusal path (a device it
cannot open). Its real-hardware behavior is not evidenced anywhere in this
repository yet — the same class of gap the Linux DTR gate already is for
`cs71d`: real camera evidence is PI-HIL/hardware-evidence territory, not
something a fixture can stand in for.

## Configuration

`cs71vision.config` mirrors `cs71d.config` deliberately: three profiles
(`development`/`test`/`production`), a `fixture`/`v4l2` backend choice, and
a production profile that fixes every path (`PRODUCTION_DEVICE_PATH =
"/dev/cs71vision"`, the udev-created stable symlink for the approved camera
module — see `appliance/ops/udev/98-cs71-vision.rules`) so there is nothing
left to misconfigure. Development defaults to the fixture backend; nothing
here ever opens a real device unless a profile explicitly says to.

## Capture loop

`cs71vision.runtime.CaptureLoop` polls one `Camera` on a fixed interval, on
its own thread, and hands each frame to a `Sink` — a plain callable. A
camera read failure or a sink that raises is logged and the loop keeps
going; nothing here ever crashes the service over one bad frame. `cli.py`'s
sink both logs frame metadata and adds the frame to the `FrameBuffer` the
correlation loop reads from.

## Running it

```sh
cs71vision --check-config                      # validate the development defaults
cs71vision --check-config /etc/cs71/cs71vision.toml
cs71vision --serve                              # capture against the fixture backend
```

## Packaging

`appliance/ops/install.sh` installs `cs71-vision.service` alongside
`cs71d.service`/`cs71-web.service`, with the same least-privilege sandbox
pattern: its own system user, no access to the serial device or either
existing database. Camera hardware identity
(`--camera-vendor-id`/`--camera-product-id`) is optional at install time,
the same way the sorter's own adapter identity is required — if omitted,
`cs71-vision` is installed and enabled but has no matched device until an
operator supplies that hardware evidence.

`cs71-vision.service`'s *effective* `Group=` is `cs71-api` — the same shape
`cs71d.service` itself uses, not a `SupplementaryGroups=` grant — which does
two jobs with one directive: it lets `cs71-vision` reach `cs71d`'s socket as
a client, and it makes `cs71-vision`'s own new dataset-api socket
(PI-VISION-003, above) come out `cs71-vision:cs71-api` 0660, so `cs71-web`
can reach it through the same supplementary grant it already carries for
`cs71d`'s socket — no new grant needed on that side. Real OS-level group
membership is still granted separately in `install.sh`, for anything that
reaches this identity a different way (`su`, `runuser`) — see
`appliance/ops/README.md`. Its own `vision.db` lives under
`StateDirectory=cs71-vision` (`/var/lib/cs71-vision`), included in
`backup.sh`'s manifest and `restore.sh`'s restore path whenever it exists,
the same way `machine.db`/`web.db` already are — optionally, since an
appliance upgraded from a pre-PI-VISION install may not have one yet. The
dataset-api socket lives under `RuntimeDirectory=cs71-vision`
(`/run/cs71-vision`), recreated fresh on every start the same way
`cs71d.service`'s own `RuntimeDirectory=` is.

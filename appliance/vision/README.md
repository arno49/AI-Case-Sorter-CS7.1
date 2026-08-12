# cs71-vision

The third appliance service (`docs/architecture/adr/0013-vision-classifier-service-and-hybrid-autonomy.md`):
camera capture and, in later PI-VISION tasks, classification. It never opens
the serial device and never bypasses `cs71d` — it only ever asks `cs71d` to
act, the same way `cs71-web` does today.

This first slice (PI-VISION-001) is deliberately narrow: a camera
abstraction and a capture loop that runs as a packaged systemd service. It
does not yet store anything durable, classify anything, or talk to `cs71d`
or the web BFF — those are PI-VISION-002 onward.

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
going; nothing here ever crashes the service over one bad frame. The
default sink only logs frame metadata (dimensions, byte count, timestamp) —
storing anything durable is PI-VISION-002's job, not this one's.

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

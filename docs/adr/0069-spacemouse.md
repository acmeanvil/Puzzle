# 0069. Read 6-DOF navigation devices directly, with open code only

- Status: Accepted (2026-11-06)
- Date: 2026-11-06

## Context

Engineers coming from Ansys, COMSOL or a CAD package often steer the 3D view with a
6-degree-of-freedom device in one hand and the mouse in the other: 3Dconnexion's
SpaceMouse family (SpaceNavigator, SpaceMouse Compact, Wireless, Pro, Enterprise) and
compatible devices. Puzzle's view (ADR 0045, `gui/viewport.py` `SceneView`) is rendered
off screen by VTK and painted as an image, with its own rotate, pan and zoom; a frame
takes about 10 ms, so a device driving the camera 60 times a second is within reach.

There are three ways in:

- **3Dconnexion's Navigation Library** (in their SDK, used through their 3DxWare
  driver). It gives their settings panel and radial menu, but it is closed, needs their
  developer agreement, and ties Puzzle's behaviour to one vendor. The maintainer decided
  against it (2026-11-06): the work stays open.
- **The device's own reports.** Every one of these devices is a USB or Bluetooth HID
  device (generic desktop page 0x01, multi-axis controller 0x08). It sends translation
  and rotation as signed 16-bit counts and its buttons as a bit mask. Older models send
  translation as report 1 and rotation as report 2; newer ones (Compact, Wireless, the
  universal receiver) send all six axes in report 1. Button reports are 3 (and, on the
  Enterprise, further reports). Vendor IDs are 0x046d (made under Logitech) and 0x256f
  (3Dconnexion).
- **spacenavd**, the open-source daemon (BSD licence) Linux distributions ship. It
  reads the device and serves motion and button events over a Unix socket in a small,
  documented protocol, with permissions already handled.

What we don't know yet, to be measured on hardware:

- whether an installed 3DxWare driver lets another program read the device at the same
  time (on Windows HID devices open shared by default; on macOS 3DxWare may take the
  device exclusively);
- whether macOS asks for Input Monitoring permission for a multi-axis controller;
- which models the maintainer's workstation has (the first one tested).

## Decision

1. **Puzzle reads the device itself and moves its own camera.** No vendor SDK, no
   vendor library at run time.
   - **Windows and macOS (and Linux without spacenavd):** HID reports through
     **hidapi** (its licences include BSD-3 and GPL-3) through its Python binding,
     conda-forge's `hidapi` (`import hid`, built for every platform Puzzle ships),
     behind a thin module of Puzzle's own (`gui/spacemouse/hid.py`). One code path for all three
     systems; on Windows it needs no driver (Windows' own HID class driver), and it opens
     the device shared.
   - **Linux with spacenavd running:** its socket (`/var/run/spnav.sock`, the X11-free
     protocol), preferred there because it already has the permissions and the user's
     settings. Without it, hidapi through hidraw (which needs a udev rule; the
     Settings page says so).
2. **Known devices are a table, not code** (`gui/spacemouse/devices.py`): vendor and
   product IDs, report layout (split or combined axes), axis order and signs, button
   count and names. A device not in the table but presenting as a multi-axis controller
   is tried with the combined layout.
3. **Motion is a pure function** (`gui/spacemouse/motion.py`), tested without Qt or
   hardware: six counts and the time since the last ones, plus the settings, in; a
   camera change out (rotation about a pivot, pan in the view plane, zoom along the view
   direction).
   - Dead zone, then a response curve (linear near rest, faster at the stops), scaled by
     the elapsed time so speed doesn't depend on the frame rate.
   - Pan and zoom scale with the model's size and the camera's distance, so a bolt and
     a bridge feel the same.
   - **Object mode** by default (push the cap and the model moves that way, as
     3Dconnexion's default and Ansys), **camera mode** as a setting.
   - **Pivot:** the centre of what is picked or selected; else the model point under the
     centre of the view (VTK's picker); else the model's centre. The pivot is held while
     the cap is moved and chosen afresh when it comes to rest.
   - Locks and options: rotation only, translation only, dominant axis only (the
     largest of the six), zoom by the cap's push/pull or by its forward/back, each axis
     invertible.
4. **A controller on the GUI side** (`gui/spacemouse/controller.py`): a reader thread per
   device posts axes and buttons to the GUI thread; motion is coalesced to one camera
   change per rendered frame (`SceneView.refresh` already renders at most once per event
   loop pass) and applied through `Viewport` methods, never `plotter.render()` directly.
   A device plugged in or removed while Puzzle runs is found or dropped within a couple
   of seconds. Moving the device stops an animation or a running pick as the mouse would.
5. **Buttons map to Puzzle's actions by name** (`actions_by_name`: Fit, Isometric, the six
   views, Refresh view, section cut, picks), with defaults per device size: two buttons
   are Fit and Isometric; a Pro's named view keys are its views. Anything with a fixed
   set of values is a dropdown of readable names (CLAUDE.md, ADR 0029).
6. **Settings > Interface > 3D mouse** (preferences, ADR 0024; they never change
   results): on or off, speed, response, object or camera mode, locks, per-axis invert,
   button mapping, and the device found (by its name) or why none is (no device; hidapi
   missing; on Linux no permission and no spacenavd). `puzzle settings` reaches them too.
7. **It ships in the installers** (ADR 0068): hidapi joins the GUI feature in
   `pixi.toml`, so the lock, `puzzle-fem`'s requirements and every installer carry it.
   Nothing to install on Windows or macOS; on Linux the Settings page says how to add
   the udev rule or start spacenavd.
8. **No agent skill.** The device steers the view a person is looking at; the built-in
   agent has nothing to call (ADR 0057's skills are for features an agent uses).

## Plan

1. `devices.py`, the report parser and `motion.py`, with tests on recorded reports and
   on the motion maths (rotation about the pivot, scale with model size, dead zone, time
   step independence, dominant axis, invert).
2. `hid.py` (over `import hid`: enumerate, open shared, read with a timeout) and the
   controller, with the view moving on the maintainer's Windows workstation.
3. Buttons and the Settings page.
4. spacenavd on Linux; a check on macOS (shared access beside 3DxWare, permissions).
5. hidapi in `pixi.toml`, the lock, `puzzle-fem`'s recipe and the installers; the smoke
   test checks the installed Python imports `hid` and enumerates, since CI has no device.

## Consequences

- One device code path for three systems, all of it open, testable without hardware
  except the last step.
- Puzzle owns how the device feels: the response curve, pivot and modes are ours to tune
  and document, and 3Dconnexion's own settings panel doesn't apply to Puzzle.
- If 3DxWare holds the device exclusively on a system, Puzzle can't read it there while
  3DxWare runs; the Settings page must say so plainly. Measured in step 2 (Windows) and
  step 4 (macOS).
- A new native dependency (hidapi) in the GUI environment; small and on every platform
  conda-forge builds for.
- Device quirks (axis signs, report layouts of new models) are data in one table, so a
  new device is a row and a recorded report in a test.

## Alternatives considered

- **3Dconnexion's Navigation Library or their older 3DxWare SDK.** The vendor's feel and
  settings panel for free, but closed, under their agreement, and Puzzle's behaviour
  would follow their driver. Declined by the maintainer.
- **Windows Raw Input (WM_INPUT) and macOS IOKit, each by hand.** The same reports with no
  dependency, but two platform APIs of our own to keep; hidapi already wraps both.
- **pyspacemouse.** A Python reader over hidapi with device tables, but small and
  thinly maintained, and its motion handling isn't what Puzzle needs; its device data
  informs ours.
- **Mouse and keyboard emulation by the vendor driver.** Works with no code, but it is
  coarse (key presses per axis), configured outside Puzzle and absent without 3DxWare.

## Built so far

**Steps 1–3 in code (2026-11-06), waiting for a device to be tried on:**

- `gui/spacemouse/devices.py`: the known devices (SpaceNavigator to Enterprise and the
  universal receiver, by USB ID), reports parsed by their length (split or combined
  axes, buttons), device frame to view frame by each device's `signs`.
- `motion.py`: dead zone, response curve, locks, one axis at a time, inversion; object
  and camera modes; pan and zoom by the camera's distance; rotation about the pivot;
  moves scaled by the time step and capped at 0.1 s.
- `hid.py` over conda-forge's `hidapi`, and `controller.py`: a daemon thread that finds a
  device (again every 3 s), reads it, and posts readings; a 16 ms timer on the GUI
  thread moves the camera through `Viewport.set_camera` about `Viewport.pivot()` (the
  picked or highlighted entities' centre, else the model's), held while the cap moves.
  Buttons run view-bar actions by name; a Pro's Fit, Top, Right and Front keys are those
  views. It starts only with a real 3D view and hidapi present.
- Settings > 3D mouse: on or off, speed, what moves, motion, one axis at a time, zoom
  direction, the two buttons.
- `tests/test_spacemouse.py`: reports, frames, shaping, every motion rule, the
  controller and its reader thread with a fake device.
- hidapi is in pixi.toml's GUI feature for linux-64, linux-aarch64, osx-64 and win-64,
  so the lock and those installers carry it; the smoke test imports it and enumerates.

Found:

- **conda-forge has no osx-arm64 hidapi** yet. Puzzle runs without a 3D mouse there;
  building one with the other packages of Puzzle's channel (ADR 0068) is to do, and
  the noarch `puzzle-fem` can't require it, so it stays an environment dependency.
- The pivot under the view's centre (VTK's picker) isn't done yet: with nothing picked
  the model's centre is used.
- Not yet: spacenavd on Linux (step 4), the device's name and the reason when there is
  none on the Settings page, and the axis signs checked on hardware.

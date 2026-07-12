using Cxx = import "./include/c++.capnp";
$Cxx.namespace("cereal");

@0xb526ba661d550a59;

# custom.capnp: a home for empty structs reserved for custom forks
# These structs are guaranteed to remain reserved and empty in mainline
# cereal, so use these if you want custom events in your fork.

# DO rename the structs
# DON'T change the identifier (e.g. @0x81c2f05a394cf4af)

# ---------------------------------------------------------------------------
# Europilot fork events.
#
# Each struct below was a reserved empty struct in upstream cereal
# (CustomReserved0..3). Following upstream convention we rename the struct but
# keep the @0x... identifier and the log.capnp Event-union slot it maps to.
#
# Everything here is delivered to the device by the app.europilot.eu gateway
# and is ADVISORY / GUIDANCE ONLY. See the architecture docs:
#   - "OSM is een nudge, nooit een filter"      (map data raises priors only)
#   - "Verkeerslichtdata (C-ITS SPaT/MAP)"      (advisory, never authoritative)
# ---------------------------------------------------------------------------

struct NdwMatrixSigns @0x81c2f05a394cf4af {
  # Result of matching the ego pose against an NDW matrix-sign region snapshot.
  #
  # Matching happens ON THE DEVICE (europilot/ndw/match.py): distance to a
  # gantry and which lane governs us depend on the precise pose, which changes
  # far faster than the gateway poll. The gateway only serves tile snapshots of
  # sign geometry + live aspects; europilotd matches and publishes the result.
  fetchMonoTime @0 :UInt64;    # monotonic ns when this was published
  valid @1 :Bool;
  snapshotAge @2 :Float32;     # seconds since the gateway's last good NDW refresh

  governing @3 :Gantry;        # the gantry we last passed: governs us now
  upcoming @4 :Gantry;         # the next gantry ahead: lets us slow down early

  struct Gantry {
    valid @0 :Bool;            # false when no gantry matched
    distance @1 :Float32;      # meters along the heading axis; negative = passed

    # A Dutch matrix sign shows a speed either with a red ring (legally binding)
    # or without one (advice). Keeping them apart is legally meaningful, so we
    # never collapse them into a single number.
    mandatorySpeed @2 :Int16;  # km/h, red-ringed: binding;  -1 = none shown
    advisorySpeed @3 :Int16;   # km/h, no red ring: advice;   -1 = none shown
    targetSpeed @4 :Int16;     # lowest of the two: what a controller aims for; -1 = none

    flashing @5 :Bool;
    closedLanes @6 :List(Int8);  # lane indices closed or diverted at this gantry

    road @7 :Text;             # e.g. "A2"
    carriageway @8 :Text;
  }
}

# Slots for the two remaining tile-distributed EU sources (OSM map context and
# C-ITS SPaT/MAP traffic lights). Deliberately still RESERVED: like NDW, each is
# distributed as tiles and matched against the ego pose ON THE DEVICE, so its
# bus schema falls out of the matcher -- and there is no gateway endpoint or
# matcher for them yet. Define each one together with its pipeline.

struct CustomReserved1 @0xaedffd8f31e7b55d {
}

struct CustomReserved2 @0xf35cc4560bbf6ec2 {
}

struct SpeedLimit @0xda96579883444c35 {
  # Advisory speed limit read from the car's own Road Sign Assist (RSA) front
  # camera, decoded from the Toyota/Lexus FCM camera-CAN messages by
  # europilot/rsa.py. Advisory only -- surfaced to the driver, never used to
  # hard-limit control.
  fetchMonoTime @0 :UInt64;   # monotonic ns when this was published
  valid @1 :Bool;
  speedLimit @2 :Int16;       # km/h; -1 unknown
  source @3 :Source;

  enum Source {
    none @0;
    rsaCamera @1;             # Toyota/Lexus Road Sign Assist front camera
  }
}

struct CustomReserved4 @0x80ae746ee2596b11 {
}

struct CustomReserved5 @0xa5cd762cd951a455 {
}

struct CustomReserved6 @0xf98d843bfd7004a3 {
}

struct CustomReserved7 @0xb86e6369214c01c8 {
}

struct CustomReserved8 @0xf416ec09499d9d19 {
}

struct CustomReserved9 @0xa1680744031fdb2d {
}

struct CustomReserved10 @0xcb9fd56c7057593a {
}

struct CustomReserved11 @0xc2243c65e0340384 {
}

struct CustomReserved12 @0x9ccdc8676701b412 {
}

struct CustomReserved13 @0xcd96dafb67a082d0 {
}

struct CustomReserved14 @0xb057204d7deadf3f {
}

struct CustomReserved15 @0xbd443b539493bc68 {
}

struct CustomReserved16 @0xfc6241ed8877b611 {
}

struct CustomReserved17 @0xa30662f84033036c {
}

struct CustomReserved18 @0xc86a3d38d13eb3ef {
}

struct CustomReserved19 @0xa4f1eb3323f5f582 {
}

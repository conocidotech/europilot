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
  # Dynamic NDW matrix signs above the road (speed / lane signalling).
  fetchMonoTime @0 :UInt64;    # monotonic ns when the gateway data was received
  valid @1 :Bool;
  signs @2 :List(Sign);

  struct Sign {
    laneIndex @0 :Int8;        # 0 = rightmost lane; -1 = applies to all lanes
    kind @1 :Kind;
    speedLimit @2 :Int16;      # km/h; -1 if not a speed sign
    distance @3 :Float32;      # meters ahead
    latitude @4 :Float64;
    longitude @5 :Float64;

    enum Kind {
      none @0;
      speedLimit @1;
      endSpeedLimit @2;
      laneClosed @3;           # red cross
      laneOpen @4;             # green arrow
      mergeLeft @5;
      mergeRight @6;
      hardShoulderOpen @7;
      warning @8;
    }
  }
}

struct MapData @0xaedffd8f31e7b55d {
  # OSM-derived road context. GUIDANCE ONLY: raises attention / priors, must
  # never filter or gate perception.
  fetchMonoTime @0 :UInt64;
  valid @1 :Bool;
  currentRoad @2 :Road;
  upcoming @3 :List(Feature);

  struct Road {
    speedLimit @0 :Int16;      # km/h; -1 unknown
    roadClass @1 :RoadClass;
    name @2 :Text;
    oneWay @3 :Bool;
  }

  struct Feature {
    kind @0 :FeatureKind;
    distance @1 :Float32;      # meters ahead
    speedLimit @2 :Int16;      # km/h for an upcoming speed change; -1 n/a
    curvature @3 :Float32;     # 1/m, signed; 0 if n/a
  }

  enum RoadClass {
    unknown @0;
    motorway @1;
    trunk @2;
    primary @3;
    secondary @4;
    tertiary @5;
    residential @6;
    service @7;
  }

  enum FeatureKind {
    speedChange @0;
    curve @1;
    junction @2;
    roundabout @3;
    stopSign @4;
    trafficLight @5;
  }
}

struct TrafficLightState @0xf35cc4560bbf6ec2 {
  # C-ITS SPaT/MAP traffic-light data. ADVISORY ONLY, never authoritative for
  # control.
  fetchMonoTime @0 :UInt64;
  valid @1 :Bool;
  intersections @2 :List(Intersection);

  struct Intersection {
    intersectionId @0 :UInt32;
    distance @1 :Float32;      # meters to the stop line
    movements @2 :List(Movement);
  }

  struct Movement {
    signalGroupId @0 :UInt8;
    phase @1 :Phase;
    timeToChange @2 :Float32;  # seconds until the next phase; -1 unknown
  }

  enum Phase {
    unknown @0;
    red @1;
    amber @2;
    green @3;
    flashingAmber @4;
  }
}

struct SpeedLimit @0xda96579883444c35 {
  # Resolved advisory speed limit, fused from the sources below. Surfaced to
  # the driver; never used to hard-limit control.
  fetchMonoTime @0 :UInt64;
  valid @1 :Bool;
  speedLimit @2 :Int16;        # km/h; -1 unknown
  source @3 :Source;
  confidence @4 :Float32;      # 0..1

  enum Source {
    none @0;
    osm @1;
    ndwMatrix @2;
    camera @3;
    combined @4;
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

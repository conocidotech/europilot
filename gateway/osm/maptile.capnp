@0xe0b1a5e7a1decaf0;

# Europilot OSM map tile -- the binary format the device memory-maps.
#
# One MapTile == one 0.25-degree grid cell (gateway/osm/grid.py). The gateway
# serializes tiles with gateway/osm/wire.py; the device mmaps them and matches
# roads against its own pose (device-side matching -- EUROPILOT-15). Cap'n Proto
# because it needs no parse step: the device reads fields straight out of the
# mapped bytes on a Snapdragon at driving speed.
#
# Advisory data only. Nothing here filters perception or hard-limits control.

enum RoadClass {
  unknown @0;
  motorway @1;
  trunk @2;
  primary @3;
  secondary @4;
  tertiary @5;
  unclassified @6;
  residential @7;
  livingStreet @8;
  service @9;
}

# Three-valued on purpose: unknown is NOT absent. "no cycleway:* tag and no
# parallel cycleway seen" is unknown; a tag/geometry saying there is none is
# absent. The device must not read silence as "no bike path here".
enum Presence {
  unknown @0;
  absent @1;
  present @2;
}

# lat/lon as fixed-point 1e-7 degrees (OSM-native 100-nanodegree precision).
# Int32 spans the globe at that scale; Float32 would drop ~2 significant digits.
struct Point {
  lat @0 :Int32;
  lon @1 :Int32;
}

enum CameraKind {
  fixed @0;        # fixed speed camera (flitspaal)
  section @1;      # average-speed section start (trajectcontrole)
}

# A speed-enforcement point attached to the road it enforces (server-side, so the
# device never reacts to a camera that sits on the off-ramp). Advisory only.
struct Camera {
  point @0 :Point;        # location on this road (section: the section start)
  maxspeed @1 :UInt8;     # enforced km/h; 0 == unknown (defer to the road limit)
  kind @2 :CameraKind;
}

enum RoundaboutKind {
  roundabout @0;   # junction=roundabout carriageway
  mini @1;         # highway=mini_roundabout (painted)
}

# A roundabout reached from this road, at its entry node. Advisory only: the
# device eases toward a comfortable approach speed, there is no posted limit here.
struct Roundabout {
  point @0 :Point;   # the entry node where this road meets the roundabout
  kind @1 :RoundaboutKind;
}

struct Road {
  id @0 :UInt64;
  roadClass @1 :RoadClass;
  maxspeed @2 :UInt8;          # km/h; 0 == unknown (never a valid posted limit)
  oneway @3 :Bool;
  lanes @4 :UInt8;             # 0 == unknown
  name @5 :Text;
  points @6 :List(Point);      # geometry, in order

  # spatial-join attributes (gateway/osm/spatial.py)
  inResidential @7 :Bool;
  calmingPerKm @8 :Float32;
  crossingPerKm @9 :Float32;
  cyclewayLeft @10 :Presence;
  cyclewayRight @11 :Presence;
  cyclestreet @12 :Bool;
  residentialScore @13 :Float32;
  comfortSpeed @14 :UInt8;     # advisory km/h; 0 == none (defer to posted)

  # speed cameras / trajectcontrole on THIS road (gateway/osm/cameras.py)
  cameras @15 :List(Camera);

  # roundabouts entered from THIS road (gateway/osm/roundabouts.py)
  roundabouts @16 :List(Roundabout);
}

struct MapTile {
  formatVersion @0 :UInt16;
  tileLat @1 :Int32;           # grid indices, not degrees (grid.tile_of)
  tileLon @2 :Int32;
  contentHash @3 :Text;        # sha256 hex of the canonical form; cache/delivery key
  generatedAtUnixS @4 :UInt32; # when the gateway serialized this tile
  attribution @5 :Text;        # ODbL attribution carried with the data
  roads @6 :List(Road);
}

import maplibregl, { type GeoJSONSource, type StyleSpecification } from "maplibre-gl";
import { useEffect, useRef } from "react";

import type { Feature } from "./api";

const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.85 } }],
};
const LOW = "#e3f0ff";
const HIGH = "#b8430c";
const MASKED = "#b9c1ca";

function bounds(features: Feature[]): [[number, number], [number, number]] | null {
  const points = features.flatMap((f) => {
    const geom = f.geometry as { type: string; coordinates: number[][][] } | null;
    return geom?.type === "Polygon" ? geom.coordinates[0]! : [];
  });
  if (!points.length) return null;
  const lngs = points.map((p) => p[0]!);
  const lats = points.map((p) => p[1]!);
  return [
    [Math.min(...lngs), Math.min(...lats)],
    [Math.max(...lngs), Math.max(...lats)],
  ];
}

/** 熱區地圖：里多邊形依數值著色；被遮蔽的里一律灰色、不顯示數值。 */
export function MapView({
  features,
  describe,
  legend,
}: {
  features: Feature[];
  describe: (f: Feature["properties"]) => string;
  legend: { low: string; high: string; masked: string };
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const ready = useRef(false);
  const describeRef = useRef(describe);
  describeRef.current = describe;

  useEffect(() => {
    if (!container.current) return;
    const m = new maplibregl.Map({
      container: container.current,
      style: STYLE,
      center: [120.97, 24.8],
      zoom: 13,
      attributionControl: { compact: true },
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.on("load", () => {
      m.addSource("villages", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({
        id: "village-fill",
        type: "fill",
        source: "villages",
        paint: { "fill-color": MASKED, "fill-opacity": 0.7 },
      });
      m.addLayer({
        id: "village-line",
        type: "line",
        source: "villages",
        paint: { "line-color": "#1f3a5f", "line-width": 1.2 },
      });
      m.on("click", "village-fill", (e) => {
        const props = e.features?.[0]?.properties as Feature["properties"] | undefined;
        if (props) {
          new maplibregl.Popup({ closeButton: false })
            .setLngLat(e.lngLat)
            .setText(describeRef.current({ ...props, masked: String(props.masked) === "true" }))
            .addTo(m);
        }
      });
      m.on("mouseenter", "village-fill", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "village-fill", () => (m.getCanvas().style.cursor = ""));
      ready.current = true;
      m.fire("healthapp:data");
    });
    map.current = m;
    // 版面完成（或視窗改變）後容器尺寸才正確：跟著重算畫布大小
    const observer = new ResizeObserver(() => m.resize());
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      ready.current = false;
      m.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () => {
      const values = features
        .filter((f) => !f.properties.masked && f.properties.value !== null)
        .map((f) => f.properties.value as number);
      const lo = Math.min(...values);
      const hi = Math.max(...values);
      const color: maplibregl.ExpressionSpecification | string = values.length
        ? [
            "case",
            ["==", ["get", "masked"], true],
            MASKED,
            hi > lo
              ? ["interpolate", ["linear"], ["get", "value"], lo, LOW, hi, HIGH]
              : HIGH,
          ]
        : MASKED;
      (m.getSource("villages") as GeoJSONSource | undefined)?.setData({
        type: "FeatureCollection",
        features,
      } as unknown as Parameters<GeoJSONSource["setData"]>[0]);
      m.setPaintProperty("village-fill", "fill-color", color);
      const box = bounds(features);
      if (box) m.fitBounds(box, { padding: 30, duration: 0 });
    };
    if (ready.current) apply();
    else m.once("healthapp:data", apply);
  }, [features]);

  return (
    <div>
      <div className="map" ref={container} />
      <div className="legend">
        <span className="swatch" style={{ background: LOW }} />
        {legend.low}
        <span className="swatch" style={{ background: HIGH }} />
        {legend.high}
        <span className="swatch" style={{ background: MASKED }} />
        {legend.masked}
      </div>
    </div>
  );
}

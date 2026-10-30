import QRCode from "qrcode";
import { useEffect, useState } from "react";

/** 在本機產生 QR（不送到第三方服務）。 */
export function Qr({ value, label }: { value: string; label: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    QRCode.toDataURL(value, { width: 560, margin: 1, errorCorrectionLevel: "M" })
      .then((url) => alive && setSrc(url))
      .catch(() => alive && setSrc(null));
    return () => {
      alive = false;
    };
  }, [value]);
  return src ? <img className="qr" src={src} alt={label} /> : null;
}

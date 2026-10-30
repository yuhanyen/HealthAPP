import { useEffect, useRef } from "react";

interface Detector {
  detect(source: HTMLVideoElement): Promise<{ rawValue: string }[]>;
}
type DetectorCtor = new (options: { formats: string[] }) => Detector;

export function canScan(): boolean {
  return "BarcodeDetector" in window && !!navigator.mediaDevices;
}

/** 相機掃 QR（BarcodeDetector）。據點端掃民眾量測 QR、商家端掃兌換券共用。 */
export function Scanner({ onResult, onClose }: { onResult: (text: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const Ctor = (window as unknown as { BarcodeDetector?: DetectorCtor }).BarcodeDetector;
    let stream: MediaStream | null = null;
    let timer = 0;
    if (!Ctor || !navigator.mediaDevices) return;
    const detector = new Ctor({ formats: ["qr_code"] });
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" } })
      .then((s) => {
        stream = s;
        if (video.current) {
          video.current.srcObject = s;
          void video.current.play();
        }
        timer = window.setInterval(async () => {
          if (!video.current) return;
          const found = await detector.detect(video.current).catch(() => []);
          if (found[0]) onResult(found[0].rawValue);
        }, 400);
      })
      .catch(onClose);
    return () => {
      window.clearInterval(timer);
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, [onResult, onClose]);
  return <video ref={video} style={{ width: "100%", borderRadius: 12 }} muted playsInline />;
}
